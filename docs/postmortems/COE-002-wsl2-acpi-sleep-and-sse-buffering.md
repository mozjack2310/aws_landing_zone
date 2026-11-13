# Correction of Error (COE-002): WSL2 ACPI Sleep Termination, Split-Horizon DNS Hairpinning & SSE Buffering

| COE ID | Date | Incident Classification | AWS Well-Architected Alignment | Impacted Layer |
| :--- | :--- | :--- | :--- | :--- |
| **COE-002** | 2026-11-12 | Edge Runtime Availability & Ingress Latency | `REL12-BP02`, `REL11-BP03`, `OPS07-BP03`, `PERF04-BP05` | Layer 5 (`RHEL 10 WSL2`, `pfSense DNS`, `sse_broadcaster.py`) |

---

## 1. Summary & Observed Symptoms
1. **Unexpected Container Uptime Reset:** All three rootless Podman Quadlets (`forrad-redis`, `forrad-edge`, `forrad-cloudflared`) showed fresh start timestamps and an `8,866`-frame (`10,010` stream length) backlog in `telemetry:stream`.
2. **Browser HUD Handshake Stalls:** Local LAN browsers visiting `https://forradwx.app` experienced TLS/connection hangs, and initial SSE connections waited up to 15 seconds before rendering connection state.

## 2. Root Cause Analysis
1. **Host ACPI Sleep & Hyper-V VM Termination:** The containers did not crash internally (`0` OOM kills). Windows host ACPI sleep suspended the laptop, and WSL2 terminated the lightweight Hyper-V utility VM (`PID 181111` -> `PID 357`) upon power state transition.
2. **Stale pfSense Split-Horizon Host Override:** A legacy pfSense DNS Resolver override pointed `forradwx.app` to a decommissioned internal IP (`192.168.50.101`), blackholing local LAN clients while external cellular clients succeeded via Cloudflare Anycast.
3. **Proxy Response Buffering on SSE:** Reverse proxies hold chunked HTTP responses until initial bytes flush, delaying `EventSource.onopen` if the server waits for the first radar tick before writing bytes.

## 3. Corrective Actions & Verification
* **ACPI Wake Hardening (`powercfg /devicedisablewake`):** Disarmed peripheral ACPI wake triggers in Windows while keeping physical USB controllers active for YubiKey FIDO2 tokens, disaster recovery boot media, and Phase 4 Cisco serial console cables.
* **Autonomous Backlog Drain Proof (`REL11-BP03`):** Upon WSL2 boot, rootless systemd Quadlets automatically restarted, preserved all `10,010` frames via Redis AOF (`appendonly yes`), and drained `pel_pending` and `unread_lag` to `0`.
* **DNS Cleanup & SSE v2.2 Anti-Buffering:** Removed the stale `192.168.50.101` pfSense override and upgraded `sse_broadcaster.py` (`v2.2.0`) to yield `: connected\nretry: 3000\n\n` in `<1ms` with `X-Accel-Buffering: no`, `Cache-Control: no-cache, no-transform`, and `15s` keepalive heartbeats.
