# Architecture Decision Records (ADR) — ForRad Hybrid Cloud & Landing Zone

| ADR ID | Title | Status | FinOps Impact | Primary Codebase Binding |
| :--- | :--- | :--- | :--- | :--- |
| **ADR-001** | Multi-Account OU Hierarchy & 4-Layer State Isolation | **Accepted** | $0.00 / mo | `bootstrap/`, `governance/main.tf` |
| **ADR-002** | Zero-Trust Identity (IAM Identity Center, GitHub OIDC, X.509 mTLS) | **Accepted** | Avoids $400/mo AWS Private CA | `governance/github_oidc.tf`, `spoke-vending/main.tf` |
| **ADR-003** | Event-Driven Control Plane Drift & Tamper Alerting | **Accepted** | $0.00 / mo (Default EventBus) | `governance/drift_alerts.tf` |
| **ADR-004** | API Rate-Limit Resiliency, Adaptive Backoff & Zero-Idle FinOps | **Accepted** | Avoids $115+/mo NAT/TGW/CMK | `spoke-vending/main.tf`, `edge-broker/telemetry_sync.py` |
| **ADR-005** | Shift-Left Policy-as-Code (Checkov) & Preventive SCP Guardrails | **Accepted** | $0.00 / mo (GitHub Runners) | `governance/policies/`, `.github/workflows/` |
| **ADR-006** | Split-Domain Cloudflare Zero-Trust Edge Ingress (`forradwx.app` / `.com`) | **Accepted** | Avoids $1.00/mo Route 53 Zones | `edge-broker/telemetry_sync.py`, `README.md` |

---

## ADR-002: Zero-Trust Machine & Human Identity Architecture
- **Context:** Long-lived IAM access keys (`AKIA...`) represent the #1 cloud breach vector, while AWS Private Certificate Authority costs $400/month per CA.
- **Decision Drivers:** Enforce `SCP-03` (zero static IAM keys) while maintaining a `< $1.50/month` idle FinOps baseline.
- **Considered Options:**
  1. Static IAM Users with 90-day key rotation (Rejected: high breach blast radius).
  2. AWS Private CA + IAM Roles Anywhere (Rejected: $400/month fixed fee violates budget).
  3. Offline OpenSSL Root CA (`4096-bit RSA`) + IAM Roles Anywhere Trust Anchor + GitHub OIDC Immutable Subject Claims (Selected).
- **Consequences:** On-premises RHEL 10 edge brokers (`forrad-edge-broker-01`) and GitHub Actions runners authenticate exclusively via 1-hour ephemeral STS tokens at **$0.00/month** idle cost.

## ADR-004: FinOps Zero-Idle Data Plane & Adaptive Retry Physics
- **Context:** Standard cloud landing zones deploy NAT Gateways ($32.40/mo), Transit Gateway attachments ($36.00/mo), and per-table KMS CMKs ($1.00/mo), immediately breaching a $1.00 tripwire budget.
- **Decision Outcome:** Utilize free VPC Gateway Endpoints for S3/DynamoDB, pre-stage `/28` TGW subnets with `enable_tgw_attachment = false`, use AWS-owned KMS encryption on non-PII dev telemetry tables (`#checkov:skip=CKV_AWS_119`), and enforce `retries={"max_attempts": 9, "mode": "adaptive"}` across all SDK/provider clients.

## ADR-006: Split-Domain Cloudflare Zero-Trust Ingress (`forradwx.app` & `forradwx.com`)
- **Context:** Hosting two public DNS zones in AWS Route 53 costs $1.00/month fixed ($0.50/zone), and opening inbound WAN ports (`TCP 80/443`) on the homelab pfSense firewall exposes residential IPs.
- **Decision Outcome:** Retain authoritative DNS on Cloudflare ($0.00/mo). Terminate public traffic on `https://forradwx.app` (HSTS preloaded) via outbound-only `cloudflared` QUIC tunnels, with `https://forradwx.com` issuing an edge HTTP `301` redirect to `https://forradwx.app`.
