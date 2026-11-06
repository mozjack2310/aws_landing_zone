#!/usr/bin/env python3
"""
ForRad Sprint 3: Closed-Loop Golden Storm Baseline Drift & Alert Engine
- Audits active S3 Parquet partitions via serverless Amazon Athena SQL.
- Prunes historical offline gaps using sliding window epoch boundaries.
- Broadcasts real-time baseline status & alerts to the Tactical HUD via Redis.
- Dispatches out-of-band alerts to Amazon SNS (OPS10-BP05 / REL06-BP03).
- Supports `--simulate-fault` for fail-safe chaos verification.
"""

import argparse
import json
import os
import sys
import time
import boto3
import redis

PROFILE_NAME = os.getenv("AWS_PROFILE", "terraform")
REGION = os.getenv("AWS_REGION", "us-east-1")
DATABASE = "forrad_telemetry_lakehouse_dev"
OUTPUT_BUCKET = "forrad-cold-lakehouse-dev-052535487590"

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
PUBSUB_CHANNEL = "ui:radar_tick"
ALERT_STREAM = "telemetry:alerts"


def publish_to_hud_and_redis(status: str, anomalies: list[str], metrics_summary: list[dict]):
    """Pushes baseline audit state into Redis Streams and Pub/Sub for the Tactical HUD."""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=2)
        now_epoch = int(time.time())
        alert_payload = {
            "event_type": "DRIFT_AUDIT",
            "audit_status": status,
            "timestamp": str(now_epoch),
            "anomaly_count": str(len(anomalies)),
            "anomalies_json": json.dumps(anomalies),
            "metrics_json": json.dumps(metrics_summary),
        }
        stream_id = r.xadd(ALERT_STREAM, alert_payload, maxlen=1000, approximate=True)
        alert_payload["stream_id"] = stream_id
        r.publish(PUBSUB_CHANNEL, json.dumps(alert_payload))
        print(f"[HUD_Bridge] Published {status} state to Redis '{PUBSUB_CHANNEL}' (AlertID={stream_id})")
    except Exception as exc:
        print(f"[HUD_Bridge WARN] Could not publish to local Redis: {exc}")


def publish_to_sns(session: boto3.Session, anomalies: list[str], simulated: bool):
    """Publishes actionable drift alerts to Amazon SNS if an alert topic exists."""
    try:
        sns = session.client("sns")
        topics = sns.list_topics().get("Topics", [])
        target_arn = None
        for t in topics:
            arn = t["TopicArn"]
            if any(k in arn.lower() for k in ["governance", "drift", "alert"]):
                target_arn = arn
                break

        if not target_arn:
            print("[SNS_Alert] No governance/drift SNS topic found; skipping cloud push.")
            return

        subject = f"[{'SIMULATED ' if simulated else ''}DRIFT ALERT] ForRad Telemetry SLA Breach"
        message = {
            "system": "ForRad Hybrid Telemetry Fabric",
            "severity": "HIGH" if not simulated else "TEST_CHAOS_DRILL",
            "region": REGION,
            "database": DATABASE,
            "anomalies": anomalies,
            "timestamp_epoch": int(time.time()),
        }
        resp = sns.publish(
            TopicArn=target_arn,
            Subject=subject[:100],
            Message=json.dumps(message, indent=2),
        )
        print(f"[SNS_Alert] Dispatched notification to {target_arn} (MessageId={resp['MessageId']})")
    except Exception as exc:
        print(f"[SNS_Alert WARN] SNS publish skipped: {exc}")


def run_drift_audit(window_minutes: int, simulate_fault: bool):
    session = boto3.Session(profile_name=PROFILE_NAME, region_name=REGION)
    athena = session.client("athena")

    now = int(time.time())
    window_epoch = now - (window_minutes * 60)
    today_utc = time.strftime("%Y-%m-%d", time.gmtime())

    query = f"""
    SELECT 
        station_id,
        COUNT(*) as live_samples,
        ROUND(AVG(latency_seconds), 2) as avg_latency_sec,
        MAX(latency_seconds) as max_latency_sec,
        ROUND(AVG(queue_lag_seconds), 1) as avg_lag_sec,
        MAX(queue_lag_seconds) as max_lag_sec
    FROM radar_sweeps_cold
    WHERE day = '{today_utc}' AND frame_timestamp >= {window_epoch}
    GROUP BY station_id
    ORDER BY station_id ASC;
    """

    print(f"[DriftEngine] Auditing active {window_minutes}m sliding window (day = {today_utc}) on {DATABASE}...")
    resp = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": DATABASE},
        ResultConfiguration={"OutputLocation": f"s3://{OUTPUT_BUCKET}/athena-results/drift-engine/"},
    )
    qid = resp["QueryExecutionId"]

    while True:
        status = athena.get_query_execution(QueryExecutionId=qid)
        state = status["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)

    if state != "SUCCEEDED":
        reason = status["QueryExecution"]["Status"].get("StateChangeReason", "Unknown")
        print(f"[DriftEngine FAIL] Query state={state}: {reason}")
        sys.exit(1)

    stats = status["QueryExecution"]["Statistics"]
    scanned_kb = stats.get("DataScannedInBytes", 0) / 1024
    exec_ms = stats.get("EngineExecutionTimeInMillis", 0)
    print(f"[DriftEngine PASS] Query Succeeded | Scanned={scanned_kb:.2f} KB | Time={exec_ms} ms\n")

    rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
    headers = [col.get("VarCharValue", "") for col in rows[0]["Data"]]
    print(" | ".join(f"{h:<18}" for h in headers))
    print("-" * 115)

    anomalies = []
    metrics_summary = []

    for r in rows[1:]:
        vals = [col.get("VarCharValue", "") for col in r["Data"]]
        station = vals[0]
        samples = int(vals[1]) if vals[1] else 0
        avg_lat = float(vals[2]) if vals[2] else 0.0
        max_lag = float(vals[5]) if vals[5] else 0.0

        print(" | ".join(f"{v:<18}" for v in vals))
        metrics_summary.append({
            "station_id": station,
            "samples": samples,
            "avg_latency_sec": avg_lat,
            "max_lag_sec": max_lag,
        })

        if avg_lat > 12.0:
            anomalies.append(f"{station}: High transport latency ({avg_lat}s > 12.0s SLA)")
        if max_lag > 300.0:
            anomalies.append(f"{station}: Queue lag breach ({max_lag}s > 300.0s SLA)")

    if len(rows) <= 1:
        print(f"(No samples ingested within the last {window_minutes} minutes — waiting for next batch)")

    if simulate_fault:
        anomalies.append("KBMX [CHAOS_DRILL]: Simulated reflectivity calibration drift (+4.2 dBZ > 2.5 sigma)")
        anomalies.append("KMOB [CHAOS_DRILL]: Simulated barometric transducer stall (QueueLag=412s > 300s SLA)")

    print("-" * 115)
    if anomalies:
        audit_status = "DRIFT_ALERT"
        print("[DRIFT ALERT] Operational anomalies detected against Golden Baseline:")
        for a in anomalies:
            print(f"  * {a}")
        publish_to_hud_and_redis(audit_status, anomalies, metrics_summary)
        publish_to_sns(session, anomalies, simulate_fault)
    else:
        audit_status = "NOMINAL"
        print("[BASELINE NOMINAL] All active stations operating within Golden Storm tolerances.")
        publish_to_hud_and_redis(audit_status, [], metrics_summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ForRad Golden Storm Drift Detector")
    parser.add_argument("--window-minutes", type=int, default=15, help="Sliding active window in minutes")
    parser.add_argument("--simulate-fault", action="store_true", help="Inject synthetic fault to test alerting")
    args = parser.parse_args()
    run_drift_audit(args.window_minutes, args.simulate_fault)
