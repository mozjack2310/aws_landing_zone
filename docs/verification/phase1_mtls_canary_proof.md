# Phase 1 Canary Verification: Zero-Trust X.509 mTLS & Blast-Radius Containment

## 1. X.509 Certificate Metadata (Offline Root CA -> Edge Leaf)
- **Subject:** `C=US, ST=AL, O=ForRad Platform, OU=HDEF Edge, CN=forrad-edge-broker-01`
- **Issuer:** `C=US, ST=AL, O=ForRad Platform, OU=Security, CN=ForRad Hybrid Root CA`
- **Hex Serial Number:** `5671F7539479BA3B31E2FEA3DEF4A0240C08ED6A`
- **X.509v3 Constraints:** `CA:FALSE`, `Digital Signature`, `TLS Web Client Authentication`

## 2. IAM Roles Anywhere mTLS Token Exchange (Zero Static IAM Keys)
- **Trust Anchor ARN:** `arn:aws:rolesanywhere:us-east-1:<AWS_ACCOUNT_ID>:trust-anchor/<TRUST_ANCHOR_UUID>`
- **Profile ARN:** `arn:aws:rolesanywhere:us-east-1:<AWS_ACCOUNT_ID>:profile/<PROFILE_UUID>`
- **Assumed STS Principal:** `arn:aws:sts::<AWS_ACCOUNT_ID>:assumed-role/ForRadEdgeBrokerRole-dev/5671f7539479ba3b31e2fea3def4a0240c08ed6a`

## 3. Data-Plane Positive & Negative Boundary Results
| Test Case | Target Resource | API Action | Result |
| :--- | :--- | :--- | :--- |
| **Positive Telemetry Write/Read** | `forrad-hybrid-telemetry-state-dev` | `dynamodb:PutItem` / `GetItem` (`KBMX`) | **PASS** (`SYNCED_VIA_ROLES_ANYWHERE_MTLS`) |
| **Negative State Lock Scan** | `corp-platform-tflocks` | `dynamodb:Scan` | **BLOCKED** (`AccessDeniedException`) |
| **Negative State Bucket Enumeration** | `s3://corp-platform-tfstate-*` | `s3:ListAllMyBuckets` | **BLOCKED** (`AccessDenied`) |
