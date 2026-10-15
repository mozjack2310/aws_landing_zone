#!/usr/bin/env python3
"""
ForRad Phase 2 HDEF Edge Telemetry Producer
Authenticates via AWS IAM Roles Anywhere (X.509 mTLS, Zero Static Keys)
and performs batched, idempotent state synchronization to Amazon DynamoDB.
"""

import time
from decimal import Decimal
import boto3
from botocore.config import Config
from boto3.dynamodb.conditions import Key

PROFILE_NAME = "forrad-edge-broker"
REGION = "us-east-1"
TABLE_NAME = "forrad-hybrid-telemetry-state-dev"

# 1. ADR-004 Compliance: Adaptive retry mode with 9 max attempts & TCP keepalive
BOTO_CONFIG = Config(
    region_name=REGION,
    retries={"max_attempts": 9, "mode": "adaptive"},
    tcp_keepalive=True,
    connect_timeout=5,
    read_timeout=10,
)


def create_edge_session() -> boto3.Session:
    """Instantiate the X.509 mTLS session ONCE to cache the 1-hour STS token."""
    return boto3.Session(profile_name=PROFILE_NAME, region_name=REGION)


def build_nexrad_batch(now_epoch: int) -> list[dict]:
    """Generate deterministic Alabama NEXRAD radar telemetry frames with 24h TTL."""
    ttl_expiry = now_epoch + 86400
    stations = [
        ("KBMX", "Birmingham, AL", Decimal("0.5"), Decimal("42.5"), "ACTIVE_SCAN"),
        ("KHTX", "Huntsville, AL", Decimal("0.5"), Decimal("38.0"), "ACTIVE_SCAN"),
        ("KMXX", "Maxwell AFB, AL", Decimal("1.5"), Decimal("29.0"), "CLEAR_AIR"),
        ("KMOB", "Mobile, AL", Decimal("0.5"), Decimal("51.0"), "SEVERE_WATCH"),
    ]
    return [
        {
            "StationID": station_id,
            "FrameTimestamp": now_epoch,
            "Location": location,
            "TiltAngle": tilt,
            "MaxReflectivityDBZ": dbz,
            "StreamStatus": status,
            "AuthMechanism": "IAM_ROLES_ANYWHERE_X509_MTLS",
            "ExpiresAt": ttl_expiry,
        }
        for station_id, location, tilt, dbz, status in stations
    ]


def main() -> None:
    session = create_edge_session()
    sts = session.client("sts", config=BOTO_CONFIG)
    identity = sts.get_caller_identity()
    print(f"[mTLS Auth] Principal : {identity['Arn']}")

    dynamodb = session.resource("dynamodb", config=BOTO_CONFIG)
    table = dynamodb.Table(TABLE_NAME)

    now_epoch = int(time.time())
    frames = build_nexrad_batch(now_epoch)

    # 2. Anti-Chatty I/O: Single BatchWriteItem call for all 4 radar stations
    start = time.perf_counter()
    with table.batch_writer() as batch:
        for frame in frames:
            batch.put_item(Item=frame)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"[BatchSync] Flushed {len(frames)} NEXRAD station frames in {elapsed_ms:.1f} ms")

    # 3. Anti-Extraneous Fetching: Partition Query with ProjectionExpression (No Scan)
    resp = table.query(
        KeyConditionExpression=Key("StationID").eq("KBMX"),
        ProjectionExpression="StationID, FrameTimestamp, #loc, MaxReflectivityDBZ, StreamStatus",
        ExpressionAttributeNames={"#loc": "Location"},
        ScanIndexForward=False,
        Limit=2,
    )
    print(f"[QueryVerify] Latest KBMX frames in {TABLE_NAME}:")
    for item in resp.get("Items", []):
        print(f"  -> {dict(item)}")


if __name__ == "__main__":
    main()
