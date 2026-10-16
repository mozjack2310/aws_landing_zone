#!/usr/bin/env python3
"""
ForRad Phase 2 HDEF Edge Telemetry Producer (Live NWS Radar API + X.509 mTLS)
Fetches live NEXRAD WSR-88D station telemetry via HTTP GET from api.weather.gov
and syncs state to Amazon DynamoDB using IAM Roles Anywhere (Zero Static Keys).
"""

import json
import time
import urllib.request
from decimal import Decimal
import boto3
from botocore.config import Config
from boto3.dynamodb.conditions import Key

PROFILE_NAME = "forrad-edge-broker"
REGION = "us-east-1"
TABLE_NAME = "forrad-hybrid-telemetry-state-dev"
NWS_USER_AGENT = "ForRad-HDEF-EdgeBroker/1.0 (https://github.com/mozjack2310/aws_landing_zone)"

BOTO_CONFIG = Config(
    region_name=REGION,
    retries={"max_attempts": 9, "mode": "adaptive"},
    tcp_keepalive=True,
    connect_timeout=5,
    read_timeout=10,
)

STATIONS = [
    ("KBMX", "Birmingham, AL"),
    ("KHTX", "Huntsville, AL"),
    ("KMXX", "Maxwell AFB, AL"),
    ("KMOB", "Mobile, AL"),
]


def fetch_live_nws_radar(station_id: str, fallback_loc: str, now_epoch: int) -> dict:
    """Perform live HTTP GET against NOAA/NWS Radar Station API."""
    url = f"https://api.weather.gov/radar/stations/{station_id}"
    req = urllib.request.Request(url, headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            props = data.get("properties", {})
            rda = props.get("rda", {}).get("properties", {})
            latency_sec = rda.get("latency", {}).get("current", {}).get("value") or 0
            status = rda.get("status", "ONLINE")
            mode = rda.get("mode", "OPERATIONAL")
            station_name = props.get("name", fallback_loc)
            return {
                "StationID": station_id,
                "FrameTimestamp": now_epoch,
                "Location": f"{station_name} ({fallback_loc})",
                "RadarStatus": f"{status}_{mode}",
                "LatencySeconds": Decimal(str(round(float(latency_sec), 2))),
                "SourceEndpoint": url,
                "AuthMechanism": "IAM_ROLES_ANYWHERE_X509_MTLS",
                "ExpiresAt": now_epoch + 86400,
            }
    except Exception as exc:
        return {
            "StationID": station_id,
            "FrameTimestamp": now_epoch,
            "Location": fallback_loc,
            "RadarStatus": f"FALLBACK_SIMULATED ({type(exc).__name__})",
            "LatencySeconds": Decimal("0.0"),
            "SourceEndpoint": url,
            "AuthMechanism": "IAM_ROLES_ANYWHERE_X509_MTLS",
            "ExpiresAt": now_epoch + 86400,
        }


def main() -> None:
    session = boto3.Session(profile_name=PROFILE_NAME, region_name=REGION)
    dynamodb = session.resource("dynamodb", config=BOTO_CONFIG)
    table = dynamodb.Table(TABLE_NAME)

    now_epoch = int(time.time())
    frames = [fetch_live_nws_radar(sid, loc, now_epoch) for sid, loc in STATIONS]

    start = time.perf_counter()
    with table.batch_writer() as batch:
        for frame in frames:
            batch.put_item(Item=frame)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"[BatchSync] Fetched live NWS radar API & flushed {len(frames)} stations in {elapsed_ms:.1f} ms")

    resp = table.query(
        KeyConditionExpression=Key("StationID").eq("KBMX"),
        ProjectionExpression="StationID, FrameTimestamp, #loc, RadarStatus, LatencySeconds, SourceEndpoint",
        ExpressionAttributeNames={"#loc": "Location"},
        ScanIndexForward=False,
        Limit=1,
    )
    for item in resp.get("Items", []):
        print(f"[Live KBMX] {dict(item)}")


if __name__ == "__main__":
    main()
