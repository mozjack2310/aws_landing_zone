# Playbook: Edge X.509 mTLS Compromise & Control-Plane Drift Containment

## Playbook Info
| Playbook ID | Description | Tools Used | Special Permissions | Playbook Author | Last Updated | Escalation POC | Stakeholders | Communication Plan |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **PLAY001** | Investigate, contain, and remediate compromised X.509 edge identities, unauthorized WAN IP calls, or control-plane SCP/OIDC drift (`OPS07-BP04`, `SEC10-BP04`) | AWS CLI v2, OpenSSL, CloudTrail, Athena, IAM Roles Anywhere, IAM Identity Center | `AWSAdministratorAccess-BreakGlass` (`PT1H` session ceiling) | Enterprise Security Engineering | 2026-11-12 | Principal Cloud Architect | Platform & Security Ops | Automated SNS (`forrad-security-governance-alerts`) + Git Post-Mortem (`COE`) |

---

## 1. Phase 1: Detect & Scope Impact (Outside-In Investigation)

1. **Inspect CloudTrail for Unauthorized `CreateSession` or `aws:SourceIp` Denies:**
   ```bash
   aws cloudtrail lookup-events \
     --lookup-attributes AttributeKey=EventSource,AttributeValue=rolesanywhere.amazonaws.com \
     --max-results 20 --region us-east-1
   ```

2. **Inspect Control-Plane Drift Alert Telemetry:**
   If triggered by `forrad-control-plane-security-drift` or GitHub Actions `drift-detection.yml` (Exit Code `2`), run a local read-only plan across all four state layers to isolate the mutated resource:
   ```bash
   for layer in bootstrap governance network-hub spoke-vending; do
     echo "=== Auditing Layer: $layer ==="
     terraform -chdir="$layer" plan -detailed-exitcode
   done
   ```

---

## 2. Phase 2: Immediate Containment (`SEC10-BP02` / `REL05-BP07`)

### Scenario A — Suspected Edge Leaf Certificate (`edge-broker.key`) Exfiltration
1. **Immediately Disable the IAM Roles Anywhere Profile (Halts All Token Minting in `<1s`):**
   ```bash
   aws rolesanywhere disable-profile \
     --profile-id 07b24293-067f-433f-966e-7119f30ada45 \
     --region us-east-1
   ```
2. **If Root CA Compromise is Suspected, Disable the Trust Anchor:**
   ```bash
   aws rolesanywhere disable-trust-anchor \
     --trust-anchor-id 8d90dec1-80e8-44f8-ab30-594429b37dd7 \
     --region us-east-1
   ```

### Scenario B — Unauthorized GitHub Actions OIDC Execution
1. **Temporarily Lock the OIDC Role Trust Policy or Revoke Active STS Sessions:**
   Attach an explicit `DenyAll` inline policy or run `terraform -chdir=governance apply` to restore the canonical `GitHubActionsLandingZoneRole` subject claim boundary (`repo:mozjack2310/aws_landing_zone:*`).

---

## 3. Phase 3: Eradication, Rotation & Recovery

1. **Mint a New Leaf Certificate from the Offline Host Vault (`~/.ca-vault/`):**
   Generate a fresh 4096-bit leaf keypair (`certs/edge-broker.key` and `certs/edge-broker.crt`), verify `chmod 0400`, and re-enable the IAM Roles Anywhere profile.
2. **Re-Verify Telemetry & Drift Baseline:**
   Restart `forrad-edge.service` and execute companion Runbook [**RUN001: NEXRAD Drift Remediation**](../runbooks/RUN001-drift-remediation.md) (`systemctl --user start forrad-drift-audit.service`) to confirm `status=0/SUCCESS`.
