# Runbook: NEXRAD Radar Telemetry & Sensor Drift Remediation

## Runbook Info
| Runbook ID | Description | Tools Used | Special Permissions | Runbook Author | Last Updated | Escalation POC |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| RUN001 | Triage, calibrate, and remediate radar telemetry drift across NEXRAD Level III stations (KBMX, KHTX, KMOB, KMXX) and IoT Edge nodes | Podman, AWS Athena, Redis 7 ACLs, AWS SNS, Python 3.12 | AWS IAM `ForRadEdgeBrokerRole-dev` (X.509 mTLS) / Local Rootless Podman | Platform Engineering | 2026-11-12 | Principal Cloud Architect |

---

## 1. Trigger Conditions & Blast Radius
* **In-Band Trigger:** Tactical HUD status changes to `ANDON: DRIFT_DETECTED` when station latency or telemetry delta breaches Golden Storm thresholds over the 15-minute Athena sliding window (`analytics/storm_drift_detector.py`).
* **Out-of-Band Trigger:** Amazon SNS alert published to `arn:aws:sns:us-east-1:052535487590:forrad-security-governance-alerts`.
* **Affected Scope:** Real-time Tactical HUD accuracy (`https://forradwx.app`), DynamoDB hot-path state (`forrad-hybrid-telemetry-state-dev`), and S3 Snappy Parquet Lakehouse analytics.

---

## 2. Diagnostic Procedure (Phase 1: Triage)

1. **Inspect the 15-Minute Drift Audit Systemd Status & Journal:**
   ~~~bash
   systemctl --user status forrad-drift-audit.service -l --no-pager
   journalctl --user -u forrad-drift-audit.service -n 50 --no-pager
   ~~~

2. **Query Real-Time Redis Stream Backlog (Zero Hardcoded Secrets — `SEC02-BP03`):**
   ~~~bash
   set -a && source ~/.config/containers/systemd/forrad-redis.env && set +a
   ~/aws_landing_zone/.venv/bin/python3 -c "import os, redis; r = redis.Redis(host='127.0.0.1', port=6379, username=os.environ['REDIS_USERNAME'], password=os.environ['REDIS_PASSWORD'], decode_responses=True); print('Stream Length:', r.xlen('telemetry:stream')); print('Latest Entries:', r.xrevrange('telemetry:stream', count=3))"
   ~~~

3. **Check Hot DynamoDB & Cold S3 Parquet Consumer Group Lag:**
   ~~~bash
   podman logs forrad-edge --tail 30 | grep -E "ColdDrain|CloudDrain|BackplaneHealth"
   ~~~

---

## 3. Mitigation & Remediation Steps (Phase 2: Recovery)

1. **Scenario A — Stale Poller Lock or Edge Worker Hang:**
   Release the distributed poller lock and restart `forrad-edge.service`:
   ~~~bash
   set -a && source ~/.config/containers/systemd/forrad-redis.env && set +a
   ~/aws_landing_zone/.venv/bin/python3 -c "import os, redis; r = redis.Redis(host='127.0.0.1', port=6379, username=os.environ['REDIS_USERNAME'], password=os.environ['REDIS_PASSWORD']); r.delete('lock:nws_poller')"
   systemctl --user restart forrad-edge.service
   ~~~

2. **Scenario B — Re-Verify Baseline After Chaos Injection (`OPS07-BP03`):**
   Trigger an immediate on-demand 15-minute sliding-window audit via systemd to verify all 4 stations return to `NOMINAL` and automatically clear the Tactical HUD Andon banner:
   ~~~bash
   systemctl --user start forrad-drift-audit.service
   systemctl --user status forrad-drift-audit.service -l --no-pager
   ~~~
