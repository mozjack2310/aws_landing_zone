#!/usr/bin/env python3
"""
ForRad Phase 2B HDEF Queue-First Telemetry Producer & Cloud Sync Consumer (v2.1 Hardened)
Eliminates Producer-Consumer Silent Failures:
  1. Static Consumer Identity ('edge-broker-01') + XAUTOCLAIM prevents orphaned PEL entries across restarts.
  2. 3-Strike Poison-Pill Quarantine via XPENDING routes crashing PEL entries (>3 deliveries) to telemetry:dlq.
  3. Pre-flight 380 KB payload size assertion prevents DynamoDB 400 KB BatchWriteItem failures.
  4. Stream High-Watermark (80% of MAXLEN) + QueueLagSeconds (REL05-BP04) + DLQ depth telemetry prevents grey failures.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from decimal import Decimal
import boto3
from botocore.config import Config
from boto3.dynamodb.conditions import Key
import redis

# --- Configuration & Constants ---
PROFILE_NAME = os.getenv("AWS_PROFILE", "forrad-edge-broker")
REGION = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME = os.getenv("DDB_TABLE_NAME", "forrad-hybrid-telemetry-state-dev")

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY = "telemetry:stream"
DLQ_STREAM_KEY = "telemetry:dlq"
CONSUMER_GROUP = "hdef_cloud_sync"

# Guardrail #1: Deterministic static consumer ID so container/process restarts reclaim their own PEL
CONSUMER_NAME = os.getenv("HDEF_CONSUMER_NAME", "forrad-edge-broker-01")
POLLER_LOCK_KEY = "lock:nws_poller"
PUBSUB_CHANNEL = "ui:radar_tick"

STREAM_MAXLEN = 10000
HIGH_WATERMARK_WARN = int(STREAM_MAXLEN * 0.80)  # Alert at 8,000 unconsumed messages
MAX_DELIVERY_ATTEMPTS = 3                        # Quarantine PEL poison pills after 3 failed deliveries
AUTOCLAIM_IDLE_MS = 60000                        # Reclaim messages stuck >60s on any stalled peer consumer
DDB_MAX_ITEM_BYTES = 380000                      # Safe ceiling below DynamoDB's hard 400 KB limit

NWS_USER_AGENT = (
    "ForRad-HDEF-EdgeBroker/2.1 (+https://forradwx.app; "
    "https://github.com/mozjack2310/aws_landing_zone)"
)

STATIONS = [
    ("KBMX", "Birmingham, AL"),
    ("KHTX", "Huntsville, AL"),
    ("KMXX", "Maxwell AFB, AL"),
    ("KMOB", "Mobile, AL"),
]

# ADR-004 Compliance: Adaptive retry mode with 9 max attempts & persistent TCP keepalive
BOTO_CONFIG = Config(
    region_name=REGION,
    retries={"max_attempts": 9, "mode": "adaptive"},
    tcp_keepalive=True,
    connect_timeout=5,
    read_timeout=10,
)


def compute_idempotency_token(station_id: str, frame_timestamp: int, source_type: str = "NWS_API") -> str:
    """REL04-BP04: Deterministic SHA-256 idempotency token for deduplication."""
    raw = f"{station_id}:{frame_timestamp}:{source_type}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ensure_consumer_group(r: redis.Redis) -> None:
    """Idempotently ensure the Redis Stream and Consumer Group exist."""
    try:
        r.xgroup_create(name=STREAM_KEY, groupname=CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def quarantine_to_dlq(r: redis.Redis, msg_id: str, reason: str, fields: dict) -> None:
    """Route poison-pill or repeatedly failing frames to telemetry:dlq and XACK primary stream."""
    r.xadd(
        DLQ_STREAM_KEY,
        {
            "original_id": str(msg_id),
            "quarantined_at": str(int(time.time())),
            "consumer": CONSUMER_NAME,
            "reason": reason,
            "raw": json.dumps(fields),
        },
        maxlen=1000,
        approximate=True,
    )
    r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
    print(f"[DLQ_QUARANTINE] Routed frame {msg_id} -> {DLQ_STREAM_KEY} ({reason})")


def poll_nws_to_redis_stream(r: redis.Redis) -> tuple[int, int]:
    """
    Acquire singleton poller lock, fetch live NEXRAD station telemetry with 304 ETag
    protection, append to `telemetry:stream`, and notify `ui:radar_tick`.
    Returns (enqueued_count, fallback_count).
    """
    if not r.set(POLLER_LOCK_KEY, CONSUMER_NAME, nx=True, ex=25):
        print("[PollerLock] Another worker holds lock:nws_poller; skipping duplicate poll.")
        return 0, 0

    now_epoch = int(time.time())
    enqueued_count = 0
    fallback_count = 0

    for station_id, fallback_loc in STATIONS:
        url = f"https://api.weather.gov/radar/stations/{station_id}"
        etag_cache_key = f"nws:etag:{station_id}"
        cached_state_key = f"nws:last_frame:{station_id}"

        headers = {
            "User-Agent": NWS_USER_AGENT,
            "Accept": "application/geo+json",
        }
        cached_etag = r.get(etag_cache_key)
        if cached_etag:
            headers["If-None-Match"] = cached_etag

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                new_etag = resp.headers.get("ETag")
                if new_etag:
                    r.set(etag_cache_key, new_etag, ex=3600)

                data = json.loads(resp.read().decode("utf-8"))
                props = data.get("properties", {})
                rda = props.get("rda", {}).get("properties", {})
                latency_sec = rda.get("latency", {}).get("current", {}).get("value") or 0.0
                status = rda.get("status", "ONLINE")
                mode = rda.get("mode", "OPERATIONAL")
                station_name = props.get("name", fallback_loc)

                frame = {
                    "schema_version": "2.1",
                    "payload_tier": "HOT_STATE",
                    "idempotency_token": compute_idempotency_token(station_id, now_epoch),
                    "StationID": station_id,
                    "FrameTimestamp": str(now_epoch),
                    "Location": f"{station_name} ({fallback_loc})",
                    "RadarStatus": f"{status}_{mode}",
                    "LatencySeconds": str(round(float(latency_sec), 2)),
                    "SourceEndpoint": url,
                    "AuthMechanism": "IAM_ROLES_ANYWHERE_X509_MTLS",
                    "IngressDomain": "https://forradwx.app",
                    "ExpiresAt": str(now_epoch + 86400),
                }
                r.set(cached_state_key, json.dumps(frame), ex=3600)

        except urllib.error.HTTPError as http_err:
            if http_err.code == 304:
                cached_json = r.get(cached_state_key)
                if cached_json:
                    frame = json.loads(cached_json)
                    frame["FrameTimestamp"] = str(now_epoch)
                    frame["idempotency_token"] = compute_idempotency_token(station_id, now_epoch, "NWS_304_CACHE")
                    frame["ExpiresAt"] = str(now_epoch + 86400)
                else:
                    continue
            else:
                fallback_count += 1
                frame = {
                    "schema_version": "2.1",
                    "payload_tier": "HOT_STATE",
                    "idempotency_token": compute_idempotency_token(station_id, now_epoch, "FALLBACK"),
                    "StationID": station_id,
                    "FrameTimestamp": str(now_epoch),
                    "Location": fallback_loc,
                    "RadarStatus": f"HTTP_ERROR_{http_err.code}",
                    "LatencySeconds": "0.0",
                    "SourceEndpoint": url,
                    "AuthMechanism": "IAM_ROLES_ANYWHERE_X509_MTLS",
                    "IngressDomain": "https://forradwx.app",
                    "ExpiresAt": str(now_epoch + 86400),
                }
        except Exception as exc:
            fallback_count += 1
            frame = {
                "schema_version": "2.1",
                "payload_tier": "HOT_STATE",
                "idempotency_token": compute_idempotency_token(station_id, now_epoch, "FALLBACK"),
                "StationID": station_id,
                "FrameTimestamp": str(now_epoch),
                "Location": fallback_loc,
                "RadarStatus": f"FALLBACK_SIMULATED ({type(exc).__name__})",
                "LatencySeconds": "0.0",
                "SourceEndpoint": url,
                "AuthMechanism": "IAM_ROLES_ANYWHERE_X509_MTLS",
                "IngressDomain": "https://forradwx.app",
                "ExpiresAt": str(now_epoch + 86400),
            }

        stream_id = r.xadd(STREAM_KEY, frame, maxlen=STREAM_MAXLEN, approximate=True)
        ui_tick = {**frame, "stream_id": stream_id}
        r.publish(PUBSUB_CHANNEL, json.dumps(ui_tick))
        enqueued_count += 1

    print(
        f"[RedisEnqueue] Published {enqueued_count} NEXRAD frames to '{STREAM_KEY}' "
        f"(Fallbacks={fallback_count})"
    )
    return enqueued_count, fallback_count


def enforce_pel_hygiene(r: redis.Redis) -> None:
    """
    Guardrail #1 & #2:
    - Reclaim idle messages (>60s) from any crashed consumer via XAUTOCLAIM.
    - Quarantine any message in the PEL whose delivery count exceeds MAX_DELIVERY_ATTEMPTS (3).
    """
    try:
        r.xautoclaim(
            name=STREAM_KEY,
            groupname=CONSUMER_GROUP,
            consumername=CONSUMER_NAME,
            min_idle_time=AUTOCLAIM_IDLE_MS,
            start_id="0-0",
            count=25,
        )
    except Exception as exc:
        print(f"[PELHygiene] XAUTOCLAIM warning: {exc}")

    pending_entries = r.xpending_range(
        name=STREAM_KEY,
        groupname=CONSUMER_GROUP,
        min="-",
        max="+",
        count=50,
    )
    for entry in pending_entries:
        msg_id = entry["message_id"]
        deliveries = entry["times_delivered"]
        if deliveries > MAX_DELIVERY_ATTEMPTS:
            rows = r.xrange(STREAM_KEY, min=msg_id, max=msg_id)
            raw_fields = rows[0][1] if rows else {}
            quarantine_to_dlq(
                r,
                msg_id,
                f"MAX_DELIVERIES_EXCEEDED ({deliveries} > {MAX_DELIVERY_ATTEMPTS})",
                raw_fields,
            )


def drain_stream_to_dynamodb(r: redis.Redis, table) -> tuple[int, int]:
    """
    Consume frames from Redis Consumer Group `hdef_cloud_sync`, batch-write to DynamoDB,
    and issue XACK strictly after AWS confirms persistence.
    Returns (flushed_count, max_queue_lag_sec).
    """
    enforce_pel_hygiene(r)

    valid_items: list[dict] = []
    ack_ids: list[str] = []
    max_queue_lag_sec = 0
    now_epoch = int(time.time())

    for cursor in ("0", ">"):
        response = r.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=CONSUMER_NAME,
            streams={STREAM_KEY: cursor},
            count=25,
            block=500 if cursor == ">" else None,
        )
        if not response:
            continue

        for _stream_name, messages in response:
            for msg_id, fields in messages:
                if fields.get("event_type") == "SYSTEM_INIT":
                    r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
                    print(f"[StreamBootstrap] Acknowledged SYSTEM_INIT marker ({msg_id})")
                    continue

                # Pre-Flight Guard: Ensure payload does not exceed DynamoDB 400 KB limit
                raw_size = len(json.dumps(fields).encode("utf-8"))
                if raw_size > DDB_MAX_ITEM_BYTES:
                    quarantine_to_dlq(
                        r, msg_id, f"PAYLOAD_OVERSIZE ({raw_size} > {DDB_MAX_ITEM_BYTES} bytes)", fields
                    )
                    continue

                try:
                    frame_ts = int(fields["FrameTimestamp"])
                    lag_sec = max(0, now_epoch - frame_ts)
                    max_queue_lag_sec = max(max_queue_lag_sec, lag_sec)

                    ddb_item = {
                        "StationID": str(fields["StationID"]),
                        "FrameTimestamp": frame_ts,
                        "Location": str(fields["Location"]),
                        "RadarStatus": str(fields["RadarStatus"]),
                        "LatencySeconds": Decimal(str(fields.get("LatencySeconds", "0.0"))),
                        "QueueLagSeconds": lag_sec,
                        "SourceEndpoint": str(fields.get("SourceEndpoint", "unknown")),
                        "AuthMechanism": str(fields.get("AuthMechanism", "IAM_ROLES_ANYWHERE_X509_MTLS")),
                        "IngressDomain": str(fields.get("IngressDomain", "https://forradwx.app")),
                        "IdempotencyToken": str(fields.get("idempotency_token", "")),
                        "SchemaVersion": str(fields.get("schema_version", "2.1")),
                        "PayloadTier": str(fields.get("payload_tier", "HOT_STATE")),
                        "RedisStreamID": str(msg_id),
                        "ExpiresAt": int(fields["ExpiresAt"]),
                    }
                    valid_items.append(ddb_item)
                    ack_ids.append(msg_id)
                except (KeyError, ValueError, TypeError) as err:
                    quarantine_to_dlq(r, msg_id, f"SCHEMA_VALIDATION_ERR: {err}", fields)

    if not valid_items:
        return 0, max_queue_lag_sec

    start = time.perf_counter()
    with table.batch_writer(overwrite_by_pkeys=["StationID", "FrameTimestamp"]) as batch:
        for item in valid_items:
            batch.put_item(Item=item)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Post-Persistence XACK: Only acknowledge after DynamoDB batch_writer succeeds
    r.xack(STREAM_KEY, CONSUMER_GROUP, *ack_ids)
    print(
        f"[CloudDrain] Flushed {len(valid_items)} frames from '{CONSUMER_GROUP}' "
        f"(consumer='{CONSUMER_NAME}', max_lag={max_queue_lag_sec}s) to DynamoDB & XACKed in {elapsed_ms:.1f} ms"
    )
    return len(valid_items), max_queue_lag_sec


def audit_backplane_health(r: redis.Redis, fallback_count: int, max_lag_sec: int) -> None:
    """
    Guardrail #3 & #4 (REL05-BP04 / REL06-BP01):
    Explicitly check stream depth, consumer lag, PEL count, and DLQ volume to prevent silent failures.
    """
    stream_len = r.xlen(STREAM_KEY)
    dlq_len = r.xlen(DLQ_STREAM_KEY)
    groups = r.xinfo_groups(STREAM_KEY)
    group_info = next((g for g in groups if g.get("name") == CONSUMER_GROUP), {})
    pending_count = int(group_info.get("pending", 0))
    lag_count = int(group_info.get("lag") or 0)

    status = "HEALTHY"
    if (pending_count + lag_count) >= HIGH_WATERMARK_WARN:
        status = "CRITICAL_BACKPRESSURE_HIGH_WATERMARK"
    elif dlq_len > 0 or fallback_count > 0 or pending_count > 0:
        status = "DEGRADED_CHECK_TELEMETRY"

    print(
        f"[BackplaneHealth] status={status} | stream_len={stream_len}/{STREAM_MAXLEN} | "
        f"pel_pending={pending_count} | unread_lag={lag_count} | dlq_len={dlq_len} | "
        f"max_queue_age={max_lag_sec}s | nws_fallbacks={fallback_count}"
    )


def main() -> None:
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    ensure_consumer_group(r)

    # Single X.509 mTLS Session Instantiation (Caches 1-Hr STS token)
    session = boto3.Session(profile_name=PROFILE_NAME, region_name=REGION)
    dynamodb = session.resource("dynamodb", config=BOTO_CONFIG)
    table = dynamodb.Table(TABLE_NAME)

    _, fallback_count = poll_nws_to_redis_stream(r)
    _, max_lag_sec = drain_stream_to_dynamodb(r, table)
    audit_backplane_health(r, fallback_count, max_lag_sec)

    # Verify latest KBMX frame in DynamoDB (Query + ProjectionExpression)
    resp = table.query(
        KeyConditionExpression=Key("StationID").eq("KBMX"),
        ProjectionExpression=(
            "StationID, FrameTimestamp, #loc, RadarStatus, "
            "LatencySeconds, QueueLagSeconds, IdempotencyToken, RedisStreamID"
        ),
        ExpressionAttributeNames={"#loc": "Location"},
        ScanIndexForward=False,
        Limit=1,
    )
    for item in resp.get("Items", []):
        print(f"[Verify KBMX] {dict(item)}")


if __name__ == "__main__":
    main()
