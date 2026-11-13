# Architecture Decision Records (ADR) — Complete Chronological Register

This register tracks the architectural evolution of the **ForRad Enterprise AWS Landing Zone & Hybrid Distributed Event Fabric** from Day-0 bootstrap through Stage 4 multi-account vending (`v0.6.0-stage4-account-factory`).

## Master Decision Ledger (`ADR-001` through `ADR-010`)

| ADR ID | Date | Title | Status & Evolution | Idle FinOps Impact | Primary Codebase Binding |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **ADR-001** | 2026-10 | Multi-Account OU Hierarchy & 4-Layer State Isolation | **Accepted** (Extended by ADR-010) | `~$0.03 / mo` (S3 State) | `bootstrap/`, `governance/main.tf` |
| **ADR-002** | 2026-10 | Zero-Trust Identity (IAM Identity Center, GitHub OIDC, X.509 mTLS) | **Accepted** (Hardened by ADR-008) | Avoids `$400/mo` Private CA | `governance/github_oidc.tf`, `spoke-vending/main.tf` |
| **ADR-003** | 2026-10 | Event-Driven Control Plane Drift & Tamper Alerting | **Accepted** *(CMK superseded by ADR-007)* | `$0.00 / mo` (Default Bus) | `governance/drift_alerts.tf` |
| **ADR-004** | 2026-10 | API Rate-Limit Resiliency (`adaptive` retries) & Zero-Idle Data Plane | **Accepted** | Avoids `$115+/mo` NAT/TGW | `spoke-vending/main.tf`, `edge-broker/` |
| **ADR-005** | 2026-10 | Shift-Left Policy-as-Code (Checkov) & Preventive SCP Guardrails | **Accepted** (Hardened by ADR-010) | `$0.00 / mo` (GitHub Runners) | `governance/main.tf`, `.github/workflows/` |
| **ADR-006** | 2026-11-03 | Queue-First Hybrid Telemetry Transport (Redis Streams + SSE) & Split-Domain Ingress | **Accepted** (`v2.2` SSE / `v2.3` Sync) | Avoids `$1/mo` R53 & `$16+/mo` ALB | `edge-broker/sse_broadcaster.py`, `README.md` |
| **ADR-007** | 2026-11-03 | Tiered Cryptographic Key Selection & Telemetry Visibility Boundaries | **Accepted** *(Supersedes ADR-003 CMK)* | Saves `$1.00/mo` per CMK | `bootstrap/main.tf`, `spoke-vending/main.tf` |
| **ADR-008** | 2026-11-06 | Edge Identity Perimeter, Offline Root CA Vaulting & Redis 7 ACL Lockdown | **Accepted** | `$0.00 / mo` (Local Vault + ACL) | `spoke-vending/main.tf`, `deploy/quadlets/` |
| **ADR-009** | 2026-11-06 | Rootless Container Sandboxing (`DropCapability=ALL`), Socket Tuning & Origin Headers | **Accepted** | `$0.00 / mo` (RHEL 10 WSL2) | `deploy/quadlets/`, `edge-broker/Dockerfile` |
| [**ADR-010**](ADR-010-multi-account-vending-and-supply-chain.md) | 2026-11-12 | Stage 4 Multi-Account Vending Factory, SHA-Pinned CI/CD & 15m Drift Timer | **Accepted** | `$0.00 / mo` (Zero Acct Fee) | `governance/accounts.tf`, `.github/workflows/` |

---

## Chronological Decision History & Architectural Lineage

### ADR-001: Multi-Account OU Hierarchy & 4-Layer Terraform State Isolation
* **Status:** Accepted (2026-10) | Extended by [ADR-010](ADR-010-multi-account-vending-and-supply-chain.md)
* **Context:** Monolithic Terraform states and single-account workloads create blast-radius exposure and noisy-neighbor quota contention (`SEC01-BP01`).
* **Decision Outcome:** Partition AWS Organizations (`r-56g0`) into `Security`, `Infrastructure`, `Workloads`, and `Sandbox` OUs with four isolated S3 state keys (`bootstrap/`, `governance/`, `network-hub/`, `spoke-vending/`) locked via `PAY_PER_REQUEST` DynamoDB (`corp-platform-tflocks`).

### ADR-002: Zero-Trust Machine & Human Identity Architecture
* **Status:** Accepted (2026-10) | Hardened by ADR-008
* **Context:** Long-lived IAM access keys (`AKIA...`) represent the primary cloud breach vector, while AWS Private CA costs `$400/month` (`SEC02-BP02`).
* **Decision Outcome:** Enforce `SCP-03` blocking static IAM keys; federate human access via IAM Identity Center permission sets (`1h` Break-Glass, `4h` Admin, `8h` Auditor); authenticate GitHub Actions via OIDC and hybrid RHEL 10 edge nodes via IAM Roles Anywhere (`8d90dec1-80e8-44f8-ab30-594429b37dd7`) backed by an offline 4096-bit RSA Root CA.

### ADR-003: Event-Driven Control Plane Drift & Tamper Alerting
* **Status:** Accepted (2026-10) | *KMS CMK encryption clause superseded by ADR-007*
* **Context:** Out-of-band console mutations to SCPs, IAM Roles Anywhere trust anchors, or SSO permission sets must trigger immediate alerts (`SEC04-BP01`).
* **Decision Outcome:** Route CloudTrail management plane events through EventBridge (`forrad-control-plane-security-drift`) and nightly OIDC drift plans to Amazon SNS (`forrad-security-governance-alerts`).

### ADR-004: FinOps Zero-Idle Data Plane & Adaptive Retry Physics
* **Status:** Accepted (2026-10)
* **Context:** Managed NAT Gateways (`$32.40/mo`) and idle Transit Gateway attachments (`$36.00/mo`) violate the `< $1.50/month` FinOps ceiling (`COST03-BP01`).
* **Decision Outcome:** Route S3 and DynamoDB traffic exclusively through `$0.00/mo` VPC Gateway Endpoints, pre-stage `/28` TGW subnets with `enable_tgw_attachment = false`, and enforce `mode = adaptive` (`max_attempts = 9`) across boto3 and Terraform providers.

### ADR-005: Shift-Left Policy-as-Code (Checkov) & Preventive SCP Guardrails
* **Status:** Accepted (2026-10) | Supply-chain pinned in [ADR-010](ADR-010-multi-account-vending-and-supply-chain.md)
* **Context:** Security regressions must be blocked prior to `terraform apply`, and member accounts must be bounded by preventive guardrails (`SEC01-BP06`).
* **Decision Outcome:** Attach `scp_region_boundary` (`us-east-1` lock), `scp_security_baseline` (CloudTrail/Config protection), and `scp_workload_invariants` (zero static IAM keys), validated on every push by Checkov (`shift-left-pac.yml`).

### ADR-006: Queue-First Hybrid Telemetry Transport & Split-Domain Cloudflare Ingress
* **Status:** Accepted (2026-11-03) | Upgraded to `v2.2` SSE & `v2.3` Compactor
* **Context:** Direct synchronous cloud writes drop telemetry frames during WAN jitter, while AWS Route 53 Hosted Zones (`$1.00/mo`) and ALBs (`$16.20+/mo`) add fixed idle cost.
* **Decision Outcome:** Decouple NWS/UDP (`:20777`) ingestion from cloud sinks via local Redis 7 Streams (`telemetry:stream`), consumer groups (`hdef_cloud_sync`, `cold_parquet_compact`), and FastAPI SSE (`GET /api/v1/telemetry/sse` with `Last-Event-ID` gap replay, `<1ms` preamble flush, and `X-Accel-Buffering: no`) exposed via outbound Cloudflare QUIC tunnels on `https://forradwx.app` (`301` redirect from `forradwx.com`).

### ADR-007: Tiered Cryptographic Key Selection & Telemetry Visibility Boundaries
* **Status:** Accepted (2026-11-03) | *Supersedes Dev KMS CMK in ADR-003*
* **Context:** Provisioning a `$1.00/month` KMS Customer Managed Key (CMK) plus `$0.03/10k` KMS API calls for public NOAA/NWS weather telemetry triggered a `WXDash-Dollar-Tripwire` forecast breach (`COST01-BP09`).
* **Decision Outcome:** Standardize non-PII public weather telemetry (`forrad-hybrid-telemetry-state-dev`, `forrad-cold-lakehouse-dev-052535487590`) and dev SNS drift alerts on `SSE-S3` (`AES256`) and AWS-Owned encryption (`$0.00/mo`), documented via audited `#checkov:skip` annotations while enforcing `aws:PrincipalOrgID` perimeter locks.

### ADR-008: Edge Identity Perimeter, Offline Root CA Vaulting & Redis 7 ACL Lockdown
* **Status:** Accepted (2026-11-06)
* **Context:** Whole-directory `certs/` container volume mounts risked exposing `forrad-root-ca.key`, and default Redis loopback allowed unauthenticated commands (`SEC02-BP03`).
* **Decision Outcome:** Vault `forrad-root-ca.key` offline on the host (`~/.ca-vault/`, `chmod 0400`); mount only leaf `edge-broker.crt` and `edge-broker.key` (`:ro`); restrict `ForRadEdgeBrokerRole-dev` with `aws:SourceIp`; and enforce Redis 7 `users.acl` (`user default off`, scoping `forrad_broker` to `~telemetry:* ~nws:* ~lock:* ~ui:* &ui:*` with `-@admin -@dangerous`).

### ADR-009: Rootless Container Sandboxing, Socket Tuning & Zero-Trust Origin Headers
* **Status:** Accepted (2026-11-06)
* **Context:** Public ingress required strict container runtime isolation and kernel UDP buffer headroom during high-velocity radar sweeps (`SEC06-BP02`).
* **Decision Outcome:** Supervise `forrad-redis`, `forrad-edge`, and `forrad-cloudflared` via rootless Podman systemd Quadlets with `DropCapability=ALL`, `NoNewPrivileges=true`, `ReadOnly=true`, `4 MB` UDP `SO_RCVBUF`, `/healthz` readiness probes, and FastAPI HSTS preload / `X-Frame-Options: DENY` / `nosniff` middleware.

### ADR-010: Stage 4 Multi-Account Vending Factory, SHA-Pinned CI/CD & 15m Drift Timer
* **Status:** Accepted (2026-11-12) — **Full Specification:** [docs/adr/ADR-010-multi-account-vending-and-supply-chain.md](ADR-010-multi-account-vending-and-supply-chain.md)
* **Context:** Stage 4 required a zero-drift member account vending factory (`governance/accounts.tf`), cryptographic pinning of all third-party GitHub Actions (`SEC11-BP02`), and autonomous 15-minute X.509 drift auditing (`RUN001`).
* **Decision Outcome:** Codified `var.vended_accounts` (`default = {}`, `close_on_deletion = true`, RFC 5233 email validation), pinned all 7 GitHub Actions across `shift-left-pac.yml` and `drift-detection.yml` to 40-character commit SHAs (`60/60` Checkov checks passed), and automated `forrad-drift-audit.timer` under `AWS_PROFILE=forrad-edge-broker`.
