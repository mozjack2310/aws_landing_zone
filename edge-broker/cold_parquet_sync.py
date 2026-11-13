#!/usr/bin/env python3
"""
ForRad Sprint 2/3 (Hardened v2.3): Cold-Path S3 Parquet Compactor
- Enforces Pre-Flight Redis Watermark Gate (>= 60 frames OR >= 900s age) via native XINFO GROUPS
  `last-delivered-id` cursor without requiring external keys outside Zero-Trust Redis 7 ACLs.
- Eliminates the Small-File S3 Anti-Pattern (PERF03-BP01) by writing consolidated Snappy Parquet batches.
- Calculates `queue_lag_seconds` from the Redis Stream ingestion ID (`msg_id`) rather than S3 flush time.
"""

import argparse
import io
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from botocore.config import Config
import pyarrow as pa
import pyarrow.parquet as pq
import redis

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

STREAM_KEY = "telemetry:stream"
GROUP_NAME = "cold_parquet_compact"
CONSUMER_NAME = "forrad-cold-compactor-01"

AWS_PROFILE = os.getenv("HDEF_AWS_PROFILE", os.getenv("AWS_PROFILE", "forrad-edge-broker"))
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("COLD_LAKEHOUSE_BUCKET", "forrad-cold-lakehouse-dev-052535487590")

BATCH_SIZE = int(os.getenv("COLD_BATCH_SIZE", "500"))
MIN_BATCH_FRAMES = int(os.getenv("COLD_MIN_BATCH_FRAMES", "60"))
MAX_BATCH_WAIT_SEC = int(os.getenv("COLD_MAX_WAIT_SEC", "900"))

BOTO_CONFIG = Config(
    region_name=AWS_REGION,
    retries={"max_attempts": 9, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=15,
)

PARQUET_SCHEMA = pa.schema([
    ("station_id", pa.string()),
    ("frame_timestamp", pa.int64()),
    ("latency_seconds", pa.float64()),
    ("queue_lag_seconds", pa.int64()),
    ("radar_status", pa.string()),
    ("location", pa.string()),
    ("idempotency_token", pa.string()),
    ("redis_stream_id", pa.string()),
    ("compacted_at_epoch", pa.int64()),
])


def ensure_consumer_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def should_compact_now(r: redis.Redis, force: bool) -> tuple[bool, int, int]:
    """Checks Redis watermark locally via XINFO GROUPS before instantiating AWS mTLS credentials."""
    now_epoch = int(time.time())
    groups = r.xinfo_groups(STREAM_KEY)
    group_info = next((g for g in groups if g.get("name") == GROUP_NAME), None)

    unread_lag = int(group_info.get("lag") or 0) if group_info else 0
    pel_pending = int(group_info.get("pending") or 0) if group_info else 0
    total_waiting = unread_lag + pel_pending

    last_id = str(group_info.get("last-delivered-id") or "0-0") if group_info else "0-0"
    try:
        last_flush_epoch = int(last_id.split("-")[0]) // 1000
    except Exception:
        last_flush_epoch = 0

    elapsed_sec = max(0, now_epoch - last_flush_epoch) if last_flush_epoch > 0 else MAX_BATCH_WAIT_SEC

    if total_waiting == 0:
        return False, 0, elapsed_sec
    if force or total_waiting >= MIN_BATCH_FRAMES or elapsed_sec >= MAX_BATCH_WAIT_SEC:
        return True, total_waiting, elapsed_sec
    return False, total_waiting, elapsed_sec


def main(force: bool = False) -> None:
    t0 = time.perf_counter()
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        username=REDIS_USERNAME if REDIS_PASSWORD else None,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        decode_responses=True,
        socket_connect_timeout=3,
    )
    ensure_consumer_group(r)

    ready, waiting_count, elapsed_sec = should_compact_now(r, force)
    if not ready:
        if waiting_count == 0:
            print("[ColdParquet] No uncompacted telemetry frames waiting in stream.")
        else:
            print(
                f"[ColdParquetHold] Buffering frames in Redis ({waiting_count}/{MIN_BATCH_FRAMES} frames, "
                f"{elapsed_sec}/{MAX_BATCH_WAIT_SEC}s elapsed) — skipping S3 PUT to preserve Free Tier."
            )
        return

    messages = []
    try:
        _, claimed, _ = r.xautoclaim(STREAM_KEY, GROUP_NAME, CONSUMER_NAME, min_idle_time=30000, start_id="0-0", count=BATCH_SIZE)
        if claimed:
            messages.extend(claimed)
    except Exception:
        pass

    remaining = max(0, BATCH_SIZE - len(messages))
    if remaining > 0:
        resp = r.xreadgroup(GROUP_NAME, CONSUMER_NAME, {STREAM_KEY: ">"}, count=remaining, block=500)
        if resp:
            messages.extend(resp[0][1])

    if not messages:
        print("[ColdParquet] No uncompacted telemetry frames waiting in stream.")
        return

    now_epoch = int(time.time())
    partitions: dict[tuple[str, str], list[dict]] = defaultdict(list)
    ack_ids: list[str] = []

    for msg_id, fields in messages:
        station_id = fields.get("StationID", "UNKNOWN")
        frame_ts = int(float(fields.get("FrameTimestamp", now_epoch)))
        latency_sec = float(fields.get("LatencySeconds", 0.0))

        try:
            stream_commit_epoch = int(msg_id.split("-")[0]) // 1000
            lag_sec = max(0, stream_commit_epoch - frame_ts)
        except Exception:
            lag_sec = 0

        day_str = datetime.fromtimestamp(frame_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        partitions[(station_id, day_str)].append({
            "station_id": station_id,
            "frame_timestamp": frame_ts,
            "latency_seconds": latency_sec,
            "queue_lag_seconds": lag_sec,
            "radar_status": fields.get("RadarStatus", "Operate_Operational"),
            "location": fields.get("Location", "Unknown"),
            "idempotency_token": fields.get("IdempotencyToken", msg_id),
            "redis_stream_id": msg_id,
            "compacted_at_epoch": now_epoch,
        })
        ack_ids.append(msg_id)

    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    s3 = session.client("s3", config=BOTO_CONFIG)

    objects_written = 0
    for (station_id, day_str), rows in partitions.items():
        table = pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="SNAPPY")
        payload = buf.getvalue()

        first_id = rows[0]["redis_stream_id"].replace("-", "_")
        last_id = rows[-1]["redis_stream_id"].replace("-", "_")
        s3_key = f"telemetry/station_id={station_id}/day={day_str}/batch_{first_id}_to_{last_id}.parquet"

        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=payload,
            ServerSideEncryption="AES256",
        )
        objects_written += 1
        print(f"[S3ParquetPut] s3://{S3_BUCKET}/{s3_key} (rows={len(rows)}, bytes={len(payload)}, codec=SNAPPY)")

    if ack_ids:
        r.xack(STREAM_KEY, GROUP_NAME, *ack_ids)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"[ColdDrainComplete] Compacted {len(ack_ids)} frames into {objects_written} Parquet objects & XACKed '{GROUP_NAME}' in {elapsed_ms:.1f} ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ForRad Cold Parquet Compactor")
    parser.add_argument("--force", action="store_true", help="Force immediate compaction regardless of batch watermark")
    args = parser.parse_args()
    main(force=args.force)
