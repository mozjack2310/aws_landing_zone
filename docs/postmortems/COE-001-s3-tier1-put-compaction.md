# Correction of Error (COE-001): S3 Tier-1 PUT Micro-Batch Exhaustion & Watermark Compaction

| COE ID | Date | Incident Classification | AWS Well-Architected Alignment | Impacted Layer |
| :--- | :--- | :--- | :--- | :--- |
| **COE-001** | 2026-11-12 | FinOps Near-Miss (`WXDash-Dollar-Tripwire` Forecast Alert) | `REL12-BP02`, `COST01-BP09`, `COST02-BP02`, `SUS02-BP01`, `SUS04-BP03` | Layer 4 & 5 (`edge-broker/cold_parquet_sync.py`, S3 Lakehouse) |

---

## 1. Summary & Impact
* **What Happened:** During Phase 3–4 continuous operation, the AWS Budgets circuit breaker (`WXDash-Dollar-Tripwire`, threshold `$1.00`) triggered a forecasted spend alert of **`$1.29 USD`** (`$0.51 USD` actual MTD).
* **Billing Forensics (`us-east-1`):** Amazon S3 `Requests-Tier1` (`PutObject`) accounted for **`$0.36 USD` (66.8% of total spend)** across `~67,480` `PUT` requests, while storage volume (`TimedStorage-ByteHrs`) cost `$0.00`.

## 2. Root Cause Analysis (5 Whys)
1. **Why did S3 cost `$0.36` when storage was under 5 MB?** AWS Free Tier includes only `2,000` S3 Tier-1 `PUT` requests per month (`$0.005` per `1,000` `PUT`s thereafter).
2. **Why were `~67,480` `PUT` requests generated?** `cold_parquet_sync.py` (`v2.1`) polled `XREADGROUP` every 60 seconds and immediately wrote one Snappy Parquet object per station (`KBMX`, `KHTX`, `KMXX`, `KMOB`) containing `rows=1` (`~3 KB` per file), generating **`5,760 PUTs/day`** (`172,800 PUTs/month`).
3. **Why did the compactor flush `rows=1` every 60s?** The initial loop optimized for minimum cold-path latency rather than batching across the 15-minute Athena drift audit window.

## 3. Corrective Actions & Quantified ROI (`v2.3` Watermark Engine)
* **Stream-Cursor Watermark Gate (`v2.3`):** Refactored `cold_parquet_sync.py` to inspect the Redis stream backlog via `XINFO GROUPS` (`last-delivered-id`) and hold compaction until either **15 minutes (`900s`)** have elapsed or **60+ frames** accumulate (`--force` bypass retained for on-demand testing).
* **Quantified Resource Reduction:** Reduced S3 `PutObject` velocity by **93.3%** (`5,760 PUTs/day` -> `384 PUTs/day`, or `11,520 PUTs/month`), lowering projected S3 request spend from `~$0.86/mo` to **`< $0.06/mo`** while writing denser, more Athena-efficient Parquet row groups.
