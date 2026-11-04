#!/usr/bin/env python3
"""
ForRad Sprint 2 (Track A) Cold Lakehouse Parquet Compactor
- Subscribes to `telemetry:stream` via independent Consumer Group `cold_parquet_compact`.
- Enforces strict PyArrow columnar schema (bigint, double, string) matching AWS Glue / Athena.
- Compacts frames in memory using Snappy compression and flushes consolidated Parquet files
  partitioned by `station_id=<ID>/day=<YYYY-MM-DD>/` to Amazon S3 before issuing XACK.
"""

from collections import defaultdict
from datetime import datetime, timezone
import io
import os
import time
import boto3
from botocore.config import Config
import pyarrow as pa
import pyarrow.parquet as pq
import redis

PROFILE_NAME = os.getenv("HDEF_AWS_PROFILE", "forrad-edge-broker")
REGION = os.getenv("AWS_REGION", "us-east-1")
BUCKET_NAME = os.getenv("COLD_LAKEHOUSE_BUCKET", "forrad-cold-lakehouse-dev-052535487590")

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY = "telemetry:stream"
CONSUMER_GROUP = "cold_parquet_compact"
CONSUMER_NAME = os.getenv("HDEF_COMPACTOR_NAME", "forrad-parquet-compactor-01")

BOTO_CONFIG = Config(
    region_name=REGION,
    retries={"max_attempts": 9, "mode": "adaptive"},
    tcp_keepalive=True,
    connect_timeout=5,
    read_timeout=10,
)

# Strict Columnar Schema matching aws_glue_catalog_table.radar_sweeps_cold
PARQUET_SCHEMA = pa.schema([
    ("frame_timestamp", pa.int64()),
    ("location", pa.string()),
    ("radar_status", pa.string()),
    ("latency_seconds", pa.float64()),
    ("queue_lag_seconds", pa.int64()),
    ("idempotency_token", pa.string()),
    ("redis_stream_id", pa.string()),
    ("schema_version", pa.string()),
])


def ensure_compactor_group(r: redis.Redis) -> None:
    """Create independent Consumer Group 'cold_parquet_compact' starting at stream beginning ('0')."""
    try:
        r.xgroup_create(name=STREAM_KEY, groupname=CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def compact_and_flush_to_s3(r: redis.Redis, s3_client) -> int:
    """Read up to 200 stream frames, group by (station_id, day), write Snappy Parquet to S3, and XACK."""
    partition_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    ack_ids: list[str] = []
    now_epoch = int(time.time())

    for cursor in ("0", ">"):
        response = r.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=CONSUMER_NAME,
            streams={STREAM_KEY: cursor},
            count=200,
            block=500 if cursor == ">" else None,
        )
        if not response:
            continue

        for _stream_name, messages in response:
            for msg_id, fields in messages:
                if fields.get("event_type") == "SYSTEM_INIT":
                    r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
                    continue

                try:
                    station_id = str(fields["StationID"])
                    frame_ts = int(fields["FrameTimestamp"])
                    day_str = datetime.fromtimestamp(frame_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    lag_sec = max(0, now_epoch - frame_ts)

                    row = {
                        "frame_timestamp": frame_ts,
                        "location": str(fields.get("Location", "Unknown")),
                        "radar_status": str(fields.get("RadarStatus", "UNKNOWN")),
                        "latency_seconds": float(fields.get("LatencySeconds", 0.0)),
                        "queue_lag_seconds": lag_sec,
                        "idempotency_token": str(fields.get("idempotency_token", "")),
                        "redis_stream_id": str(msg_id),
                        "schema_version": str(fields.get("schema_version", "2.1")),
                    }
                    partition_rows[(station_id, day_str)].append(row)
                    ack_ids.append(msg_id)
                except (KeyError, ValueError, TypeError) as err:
                    print(f"[CompactorSkip] Skipping malformed message {msg_id}: {err}")
                    r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)

    if not partition_rows:
        print("[ColdParquet] No uncompacted telemetry frames waiting in stream.")
        return 0

    start = time.perf_counter()
    objects_written = 0
    total_rows = 0

    for (station_id, day_str), rows in partition_rows.items():
        table = pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="SNAPPY")
        payload_bytes = buf.getvalue()

        first_id = rows[0]["redis_stream_id"].replace("-", "_")
        last_id = rows[-1]["redis_stream_id"].replace("-", "_")
        s3_key = f"telemetry/station_id={station_id}/day={day_str}/batch_{first_id}_to_{last_id}.parquet"

        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=payload_bytes,
            ServerSideEncryption="AES256",
        )
        objects_written += 1
        total_rows += len(rows)
        print(
            f"[S3ParquetPut] s3://{BUCKET_NAME}/{s3_key} "
            f"(rows={len(rows)}, bytes={len(payload_bytes)}, codec=SNAPPY)"
        )

    elapsed_ms = (time.perf_counter() - start) * 1000
    r.xack(STREAM_KEY, CONSUMER_GROUP, *ack_ids)
    print(
        f"[ColdDrainComplete] Compacted {total_rows} frames into {objects_written} Parquet objects "
        f"& XACKed '{CONSUMER_GROUP}' in {elapsed_ms:.1f} ms"
    )
    return total_rows


def main() -> None:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
    )
    ensure_compactor_group(r)

    session = boto3.Session(profile_name=PROFILE_NAME, region_name=REGION)
    s3_client = session.client("s3", config=BOTO_CONFIG)

    compact_and_flush_to_s3(r, s3_client)


if __name__ == "__main__":
    main()
