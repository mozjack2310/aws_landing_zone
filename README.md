# Enterprise AWS Landing Zone & Hybrid Distributed Event Fabric (ForRad)

Production-grade, multi-account AWS Landing Zone and Hybrid Edge Telemetry Fabric engineered with **Terraform**, **AWS Organizations (SCPs & Stage 4 Account Factory)**, **AWS IAM Roles Anywhere (X.509 mTLS)**, **Rootless Podman Systemd Quadlets**, and **SHA-Pinned Shift-Left Policy-as-Code (Checkov + GitHub OIDC)**.

* **Live Production Tactical HUD:** [https://forradwx.app](https://forradwx.app)
* **Current Release Baseline:** `v0.6.0-stage4-account-factory`

---

## 1. Architecture & State Blast-Radius Isolation

![ForRad Hybrid Landing Zone Architecture](docs/diagrams/forrad_hybrid_landing_zone.png)

```mermaid
flowchart TD
    subgraph L1["Layer 1: bootstrap/ (Remote State Foundation)"]
        S3State["S3 State Bucket<br/>(SSE-S3 + TLS 1.2+ + Versioning)"]
        DDBLock["DynamoDB State Lock Table<br/>(PAY_PER_REQUEST)"]
    end

    subgraph L2["Layer 2: governance/ (Zero-Trust Control Plane & Account Factory)"]
        SCPs["Preventive SCPs:<br/>SCP-01 (us-east-1 Lock)<br/>SCP-02 (Audit Lock)<br/>SCP-03 (Zero Static Keys)"]
        OrgRoot["AWS Organizations Root (r-56g0)<br/>OUs: Security | Infrastructure | Workloads | Sandbox"]
        AcctFactory["Stage 4 Account Factory (accounts.tf)<br/>Vends Member Accounts + OrganizationAccountAccessRole"]
        RolesAnywhere["IAM Roles Anywhere Trust Anchor<br/>(Offline 4096-bit X.509 Root CA)"]
        GitHubOIDC["GitHub Actions OIDC Provider +<br/>SHA-Pinned Drift & Checkov Gates"]
    end

    subgraph L3["Layer 3: network-hub/ (IPAM Supernet)"]
        Supernet["Corporate Supernet<br/>10.100.0.0/16"]
    end

    subgraph L4["Layer 4: spoke-vending/ (Zero-Idle Hybrid Telemetry Spoke)"]
        SpokeVPC["Spoke VPC 10.100.16.0/20<br/>(App /22, DB /24, TGW /28)"]
        Endpoints["Free S3 & DynamoDB<br/>Gateway VPC Endpoints"]
        HotDDB["Hot Path: DynamoDB<br/>forrad-hybrid-telemetry-state-dev"]
        ColdS3["Cold Path: S3 Snappy Parquet Lakehouse<br/>+ Glue/Athena Partition Projection"]
    end

    subgraph L5["Layer 5: Hybrid Edge Runtime (RHEL 10 WSL2 + Rootless Podman Quadlets)"]
        EdgeQuadlets["forrad-redis.service (Redis 7 ACL + AOF)<br/>forrad-edge.service (FastAPI SSE v2.2 + UDP :20777)<br/>forrad-cloudflared.service (QUIC -> forradwx.app)"]
        DriftTimer["forrad-drift-audit.timer (Every 15m)<br/>Athena Sliding-Window Audit + RUN001 Runbook"]
    end

    L1 --> L2 --> L3 --> L4
    L5 -- "X.509 mTLS (aws_signing_helper)" --> RolesAnywhere
    L5 -- "Hot & Cold Telemetry Drain" --> HotDDB
    L5 -- "Snappy Parquet Compaction" --> ColdS3
    DriftTimer -- "15m SLA Query & SNS Alert" --> ColdS3
```

---

## 2. FinOps Zero-Idle Cost Ledger (< $1.50/Month Cap | $0.03 MTD Actual)

| Architectural Layer | Prohibited Cloud Cost Trap | Avoided Cost | Implemented Zero-Idle Pattern | Idle Run-Cost |
| :--- | :--- | :--- | :--- | :--- |
| **1. Bootstrap (`bootstrap/`)** | Unencrypted Local State / Idle KMS CMKs | `$1.00+ / mo` | S3 Versioning + TLS 1.2+ + `PAY_PER_REQUEST` DynamoDB Lock | **~$0.03 / mo** |
| **2. Governance & Account Factory (`governance/`)** | AWS Private CA (`$400/mo`) & Paid Control Plane Add-ons | `$400.00 / mo` | Offline X.509 Root CA + IAM Roles Anywhere + Organizations Account Factory (`accounts.tf`) | **$0.00 / mo** |
| **3. Network Hub (`network-hub/`)** | Idle Transit Gateway Attachments | `$36.00 / mo` | Pre-staged `/28` TGW Subnets (`enable_tgw_attachment = false`) | **$0.00 / mo** |
| **4. Spoke & Lakehouse (`spoke-vending/`)** | Managed NAT Gateways (`$32/mo`) & ALBs (`$16/mo`) | `$85.00+ / mo` | S3/DynamoDB Gateway VPC Endpoints + `PAY_PER_REQUEST` DynamoDB + S3 Snappy Parquet + Athena Projection | **$0.00 / mo** |
| **5. Hybrid Edge & CI/CD (`edge-broker/`, `deploy/quadlets/`)** | Always-On Cloud Compute (ECS/EC2) & Static IAM Keys | `$35.00+ / mo` | Rootless Podman Quadlets + Cloudflare Anycast QUIC Tunnel + 15m Systemd Drift Timer + GitHub OIDC | **$0.00 / mo** |

---

## 3. Empirical Verification Artifacts & Operational Runbooks

* [Architecture Decision Records (ADRs)](docs/)
* [Phase 1: Zero-Trust X.509 mTLS & Blast-Radius Canary Proof](docs/verification/phase1_mtls_canary_proof.md)
* [Phase 2B: Queue-First Redis Streams Backplane & Silent-Failure Guardrails Proof](docs/verification/phase2b_redis_streams_proof.md)
* [Phase 3.2: Edge Caching, CI/CD Secret Injection, & Zero-Drift Validation Proof](docs/verification/phase3_2_edge_cache_proof.md)
* [Phase 4 & 5: Cloudflare Anycast QUIC Ingress, HSTS Preload, & SSE Anti-Buffering Proof](docs/verification/phase4_production_ingress_proof.md)
* [Phase 3 Track A: S3 Snappy Parquet Cold Lakehouse & Athena Partition Projection Proof](docs/verification/sprint2_track_a_lakehouse_proof.md)
* [Phase 3 Track B: FastAPI Multiplexed SSE Broadcaster & Last-Event-ID Replay Proof](docs/verification/sprint2_track_b_sse_proof.md)
* [Phase 3 Track C: Closed-Loop Golden Storm Athena Drift Detector & SNS Andon Proof](docs/verification/sprint3_drift_engine_proof.md)
* [Operational Incident Runbook: `RUN001-drift-remediation.md` (`OPS07-BP03`)](docs/runbooks/RUN001-drift-remediation.md)

---

## 4. Zero-Trust Split-Domain Edge Ingress (`forradwx.app` & `forradwx.com`)

* **Primary Telemetry Gateway ([https://forradwx.app](https://forradwx.app)):** Enforces strict `.app` HSTS preload transport security, terminating public client traffic at the Cloudflare Anycast edge and routing via outbound-only `cloudflared` QUIC tunnels into the local rootless Podman `forrad-edge` broker (`X-Accel-Buffering: no`, `<1ms` SSE preamble flush, and 15-second keepalive heartbeats with zero open WAN firewall ports).
* **Discovery & Canonical Redirect (`https://forradwx.com`):** Executes an edge-level `HTTP 301 Moved Permanently` redirect to `https://forradwx.app` to preserve single-origin CORS pinning and cache locality.

---

## 5. Stage 4 Multi-Account Vending Factory (`governance/accounts.tf`)

Member accounts (`forrad-log-archive`, `forrad-security-tooling`, `forrad-network-hub`, `forrad-workloads-prod`) are codified in `governance/accounts.tf` (`SEC01-BP01`, `SEC01-BP02`, `REL01-BP03`) with `default = {}` to preserve zero cloud drift during hold periods. To preview or vend a member account into its target OU with serialized control-plane safety (`-parallelism=1`):

```bash
terraform -chdir=governance plan -parallelism=1 \
  -var='vended_accounts={"workloads_prod":{"name":"forrad-workloads-prod","email":"admin+forrad-prod@forradwx.com","ou_key":"workloads","environment":"prod"}}'
```
