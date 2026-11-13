# ADR-010: Stage 4 Multi-Account Vending Factory & SHA-Pinned CI/CD Supply Chain

| Field | Specification |
| :--- | :--- |
| **ADR ID** | ADR-010 |
| **Status** | Accepted (2026-11-12) |
| **Deciders** | Principal Cloud Platform Architect / Enterprise Security Engineering |
| **Scope** | Layer 2 (`governance/accounts.tf`), CI/CD (`.github/workflows/`), & Autonomous Drift Timer (`deploy/quadlets/`) |
| **FinOps Impact** | `$0.00 / month` idle baseline (AWS Organizations member accounts incur `$0.00/mo` fixed fees) |
| **Well-Architected Alignment** | `SEC01-BP01`, `SEC01-BP02`, `SEC10-BP05`, `SEC11-BP02`, `SEC11-BP06`, `REL01-BP03`, `OPS05-BP01`, `OPS07-BP03` |
| **Primary Codebase Binding** | `governance/accounts.tf`, `.github/workflows/drift-detection.yml`, `.github/workflows/shift-left-pac.yml`, `deploy/quadlets/forrad-drift-audit.*`, `docs/runbooks/RUN001-drift-remediation.md` |

---

## 1. Context and Problem Statement
1. **Multi-Account Isolation vs. Control-Plane Quotas (`SEC01-BP01`, `REL01-BP03`):** AWS Organizations (`r-56g0`, Management Account `052535487590`) enforces a concurrency limit of 1 in-flight `CreateAccount` operation. Hardcoding member accounts causes parallel throttling (`ConcurrentModificationException`), exposes contact emails in public Git, and triggers CI/CD drift during hold periods.
2. **CI/CD Mutable Tag Supply-Chain Risk (`SEC11-BP02`):** Referencing third-party GitHub Actions via mutable tags (`@v4`, `@v3`, `@v12`) in workflows holding `id-token: write` OIDC permissions exposes `GitHubActionsLandingZoneRole` if an upstream tag is force-pushed.
3. **Unattended Drift Auditing (`OPS07-BP03`):** The 15-minute Athena drift detector (`analytics/storm_drift_detector.py`) required autonomous execution via X.509 machine credentials (`AWS_PROFILE=forrad-edge-broker`) rather than expiring human SSO sessions.

---

## 2. Decision Outcome

### 2.1 Parameterized Multi-Account Factory (`governance/accounts.tf`)
* Codifies `aws_organizations_account.vended` mapped to `security` (`ou-56g0-faju8s4k`), `infrastructure` (`ou-56g0-3svxwvwg`), `workloads` (`ou-56g0-amz3p06a`), and `sandbox` (`ou-56g0-ke15l7bn`).
* Enforces RFC 5233 `+` email sub-addressing validation, `close_on_deletion = true`, `create_govcloud = false`, and pre-provisioned `OrganizationAccountAccessRole` ARNs (`SEC10-BP05`).
* Defaults `var.vended_accounts = {}` with empty output maps synced to remote S3 state so scheduled `terraform plan -detailed-exitcode` runs return Exit Code `0`.

### 2.2 Peeled 40-Character Commit SHA Pinning (`.github/workflows/`)
All 7 external action references are pinned to dereferenced (`^{}`) 40-character Git commit SHAs:
* `actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4`
* `hashicorp/setup-terraform@b9cd54a3c349d3f38e8881555d616ced269862dd # v3`
* `aws-actions/configure-aws-credentials@7474bc4690e29a8392af63c5b98e7449536d5c3a # v4`
* `bridgecrewio/checkov-action@3f0caf0c3aa9f56ade6b49f6e60b63aaeafb75a3 # v12`

### 2.3 Autonomous 15-Minute Drift Timer & Secret-Safe Runbook (`RUN001`)
* Tracks `deploy/quadlets/forrad-drift-audit.service` and `forrad-drift-audit.timer` executing under `AWS_PROFILE=forrad-edge-broker`.
* Codifies `docs/runbooks/RUN001-drift-remediation.md` loading `~/.config/containers/systemd/forrad-redis.env` at runtime (`SEC02-BP03`).

---

## 3. Verification Proof
```text
Checkov github_actions scan : Passed checks: 60, Failed checks: 0, Skipped checks: 0
Checkov terraform scan      : Passed checks: 33, Failed checks: 0, Skipped checks: 11 (Audited FinOps/RBAC skips)
Governance Plan Exit Code   : 0 ("No changes. Your infrastructure matches the configuration.")
Drift Timer Service Audit   : status=0/SUCCESS (1.156s CPU, 41.5M RAM, [BASELINE NOMINAL] across KBMX/KHTX/KMOB/KMXX)
```
