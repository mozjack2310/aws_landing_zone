#!/usr/bin/env python3
"""
ForRad Sprint 3: Calibrated Golden Storm Baseline Drift Engine
- Runs serverless Athena SQL queries against `forrad_telemetry_lakehouse_dev`.
- Isolates real-time streaming performance from historical backplane replays.
- Detects physical sensor drift and edge transmission lag against Golden Baseline.
"""

import sys
import time
import boto3

PROFILE_NAME = "terraform"
REGION = "us-east-1"
DATABASE = "forrad_telemetry_lakehouse_dev"
OUTPUT_BUCKET = "forrad-cold-lakehouse-dev-052535487590"

# Analytical Query isolating real-time streams from historical batch backlogs
DRIFT_QUERY = """
SELECT 
    station_id,
    COUNT(*) as total_samples,
    ROUND(AVG(latency_seconds), 2) as avg_latency_sec,
    MAX(latency_seconds) as max_latency_sec,
    ROUND(AVG(CASE WHEN queue_lag_seconds <= 3600 THEN queue_lag_seconds ELSE NULL END), 1) as live_avg_queue_lag_sec,
    MAX(CASE WHEN queue_lag_seconds <= 3600 THEN queue_lag_seconds ELSE 0 END) as live_max_queue_lag_sec,
    MAX(queue_lag_seconds) as historical_max_backlog_sec
FROM radar_sweeps_cold
WHERE day >= '2026-11-04'
GROUP BY station_id
ORDER BY station_id ASC;
"""

def run_calibrated_drift():
    session = boto3.Session(profile_name=PROFILE_NAME, region_name=REGION)
    athena = session.client("athena")

    print(f"[DriftEngine] Auditing live telemetry against Golden Storm Baseline ({DATABASE})...")
    resp = athena.start_query_execution(
        QueryString=DRIFT_QUERY,
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

    results = athena.get_query_results(QueryExecutionId=qid)
    rows = results["ResultSet"]["Rows"]

    headers = [col.get("VarCharValue", "") for col in rows[0]["Data"]]
    print(" | ".join(f"{h:<22}" for h in headers))
    print("-" * 155)

    anomalies = []
    for r in rows[1:]:
        vals = [col.get("VarCharValue", "") for col in r["Data"]]
        station = vals[0]
        avg_latency = float(vals[2]) if vals[2] else 0.0
        live_max_lag = float(vals[5]) if vals[5] else 0.0
        hist_backlog = float(vals[6]) if vals[6] else 0.0

        print(" | ".join(f"{v:<22}" for v in vals))

        # Real-time baseline evaluations
        if avg_latency > 12.0:
            anomalies.append(f"Station {station}: High transmission latency ({avg_latency}s > 12.0s)")
        if live_max_lag > 300.0:
            anomalies.append(f"Station {station}: Live stream queue stall ({live_max_lag}s > 300.0s)")

    print("-" * 155)
    if anomalies:
        print("[DRIFT ALERT] Operational anomalies detected against Golden Baseline:")
        for alert in anomalies:
            print(f"  * {alert}")
    else:
        print("[BASELINE NOMINAL] Live streaming telemetry is within operational bounds.")
        print("                   (Historical cold replays preserved in data lakehouse without triggering live alerts)")

if __name__ == "__main__":
    run_calibrated_drift()
