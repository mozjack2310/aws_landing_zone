# Operational Readiness Review (ORR-001): Data Lifecycle, RTO/RPO Targets & Safe Decommissioning

| Document ID | Scope | AWS Well-Architected Alignment | Review Cadence | Owner |
| :--- | :--- | :--- | :--- | :--- |
| **ORR-001** | All 5 Architectural Layers (`v0.6.0-stage4-account-factory`) | `OPS07-BP02`, `OPS02-BP01`, `REL13-BP01`, `REL09-BP01`, `COST04-BP02`, `SUS04-BP01` | Prior to Phase Promotions | Platform Engineering |

---

## 1. Data Classification, Storage Tiering & Retention Matrix (`SUS04-BP01` / `COST04-BP05`)

| Storage Tier | Backing Resource | Data Classification | Access Pattern & Throughput | Retention & Lifecycle Policy | Idle Cost |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier 0: Edge Hot Buffer** | Local Redis 7 (`telemetry:stream` + AOF `appendonly yes`) | Public Operational Weather Telemetry | Sub-millisecond `XADD` / `XREADGROUP` / `XRANGE` replay | Capped ring buffer (`MAXLEN ~ 10000` frames); local SSD persistence | `$0.00 / mo` |
| **Tier 1: Cloud Hot State** | Amazon DynamoDB (`forrad-hybrid-telemetry-state-dev`) | Public Operational Weather Telemetry (`SSE-Owned`) | Single-digit ms point lookups by `StationID` (`PAY_PER_REQUEST`) | Latest station state + idempotency token watermark | `$0.00 / mo` |
| **Tier 2: Cold Analytics Lakehouse** | Amazon S3 (`forrad-cold-lakehouse-dev-052535487590`) + Glue/Athena | Historical Weather Telemetry (`SSE-S3` Snappy Parquet) | 15-minute compacted Parquet batches; Partition Projection by `station_id` & `day` | Compaction every 15m (`v2.3` reduces `PUT`s by 93.3%); archivable after 90 days | `$0.00 / mo` (`< $0.06/mo` active) |
| **Tier 3: Control-Plane State** | Amazon S3 (`corp-platform-tfstate-*`) + DynamoDB (`corp-platform-tflocks`) | Confidential Infrastructure State (`SSE-S3` + TLS 1.2+) | Read/Write during CI/CD drift plans and Terraform applies | Object versioning enabled across all 4 isolated state keys | `~$0.03 / mo` |

---

## 2. Disaster Recovery Objectives: RTO & RPO (`REL13-BP01` / `REL09-BP01`)

| Failure Scenario | Recovery Time Objective (RTO) | Recovery Point Objective (RPO) | Automated Recovery Mechanism |
| :--- | :--- | :--- | :--- |
| **Edge Container Process Crash (`forrad-edge` / `forrad-redis` / `cloudflared`)** | **`< 15 seconds`** | **`0 frames lost`** | Systemd Quadlet `Restart=always` (`RestartSec=5s`) + `/healthz` probe + Redis AOF + `XREADGROUP` PEL recovery. |
| **Browser / WAN Drop on Tactical HUD (`https://forradwx.app`)** | **`< 3 seconds`** | **`0 frames lost`** | SSE `retry: 3000` directive + automatic `Last-Event-ID` `XRANGE` gap replay from Redis Streams. |
| **WSL2 Host Reboot / Power Loss** | **`< 60 seconds`** (upon host boot) | **`0 buffered frames lost`** | User systemd lingering + Redis AOF persistence + automatic cloud backlog drain (`hdef_cloud_sync`). |
| **Cloud Spoke / Governance Resource Drift** | **`< 5 minutes`** | **Last Git Commit on `master`** | Nightly/on-demand `drift-detection.yml` (`-detailed-exitcode`) + deterministic `terraform apply`. |

---

## 3. Safe Member Account & Resource Decommissioning Checklist (`COST02-BP06` / `COST04-BP02`)

1. **Closing a Vended Member Account (`governance/accounts.tf`):**
   Because `aws_organizations_account.vended` enforces `close_on_deletion = true`, removing an account key from `var.vended_accounts` and running `terraform -chdir=governance apply -parallelism=1` automatically initiates the AWS Organizations `CloseAccount` workflow rather than leaving an orphaned billable account.
2. **Pausing Active S3/DynamoDB Ingestion During Extended Study Holds:**
   To drop active cloud spend from `< $0.15/mo` to `~$0.03/mo` during multi-week offline windows while keeping the Git repository at zero drift:
   ```bash
   systemctl --user stop forrad-edge.service forrad-drift-audit.timer
   ```
   Resume anytime with `systemctl --user start forrad-edge.service forrad-drift-audit.timer`.
