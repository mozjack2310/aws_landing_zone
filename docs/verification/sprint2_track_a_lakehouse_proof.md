# Sprint 2 (Track A) Verification: S3 Parquet Cold Lakehouse, Glue Partition Projection & Tenant Lock

## 1. Layer 5 Tenant Partition Isolation (`dynamodb:LeadingKeys`)
- **IAM Role:** `ForRadEdgeBrokerRole-dev`
- **Condition:** `ForAllValues:StringEquals` on `dynamodb:LeadingKeys = ["KBMX", "KHTX", "KMXX", "KMOB"]`
- **Empirical Negative Test (`StationID = KXYZ`):**
```text
[PASS - TENANT ISOLATION VERIFIED] AWS blocked unapproved StationID 'KXYZ' with AccessDeniedException
```

## 2. Consumer Group 2 (`cold_parquet_compact`) -> S3 Snappy Parquet Compaction
- **Bucket:** `s3://forrad-cold-lakehouse-dev-052535487590` (`SSE-S3` / `AES256`, 90-day lifecycle)
- **Compaction Trace:**
```text
[S3ParquetPut] s3://forrad-cold-lakehouse-dev-052535487590/telemetry/station_id=KBMX/day=2026-11-03/batch_1793744362806_0_to_1793744362806_0.parquet (rows=1, bytes=3018, codec=SNAPPY)
[S3ParquetPut] s3://forrad-cold-lakehouse-dev-052535487590/telemetry/station_id=KBMX/day=2026-11-04/batch_1793827549959_0_to_1793827549959_0.parquet (rows=1, bytes=3018, codec=SNAPPY)
[ColdDrainComplete] Compacted 8 frames into 8 Parquet objects & XACKed 'cold_parquet_compact' in 1381.7 ms
```

## 3. Serverless Amazon Athena SQL Verification (Zero Glue Crawlers via Partition Projection)
- **Database / Table:** `forrad_telemetry_lakehouse_dev.radar_sweeps_cold`
- **Execution Metrics:** `state=SUCCEEDED | Scanned=4.00 KB | Time=583 ms` ($0.00/mo idle cost)
```text
station_id         | day                | frame_timestamp    | radar_status       | latency_seconds    | queue_lag_seconds  | redis_stream_id
---------------------------------------------------------------------------------------------------------------------------------------
KBMX               | 2026-11-04         | 1793827549         | Operate_Operational| 0.0                | 845                | 1793827549959-0
KHTX               | 2026-11-04         | 1793827549         | Operate_Operational| 0.0                | 845                | 1793827550240-0
KMOB               | 2026-11-04         | 1793827549         | Operate_Operational| 0.0                | 845                | 1793827550638-0
KMXX               | 2026-11-04         | 1793827549         | Operate_Operational| 0.0                | 845                | 1793827550445-0
KBMX               | 2026-11-03         | 1793744362         | Operate_Operational| 0.0                | 84032              | 1793744362806-0
KHTX               | 2026-11-03         | 1793744362         | Operate_Operational| 0.0                | 84032              | 1793744362986-0
KMOB               | 2026-11-03         | 1793744362         | Operate_Operational| 0.0                | 84032              | 1793744363337-0
KMXX               | 2026-11-03         | 1793744362         | Operate_Operational| 0.0                | 84032              | 1793744363169-0
```
