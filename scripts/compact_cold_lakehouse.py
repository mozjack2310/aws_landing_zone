#!/usr/bin/env python3
import argparse
import io
import os
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config
import pyarrow as pa
import pyarrow.parquet as pq

PARTITION_RE = re.compile(r"^(telemetry/station_id=[^/]+/day=\d{4}-\d{2}-\d{2})/.+\.parquet$")
BOTO_CONFIG = Config(
    region_name=os.getenv("AWS_REGION", "us-east-1"),
    retries={"max_attempts": 9, "mode": "adaptive"},
    max_pool_connections=32,
    connect_timeout=5,
    read_timeout=30,
)


def list_partitions(s3, bucket: str, prefix: str) -> dict[str, list[dict]]:
    paginator = s3.get_paginator("list_objects_v2")
    partitions: dict[str, list[dict]] = defaultdict(list)
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            match = PARTITION_RE.match(obj["Key"])
            if match:
                partitions[match.group(1)].append({"Key": obj["Key"], "Size": obj["Size"]})
    return partitions


def fetch_table(s3, bucket: str, key: str) -> pa.Table:
    return pq.read_table(io.BytesIO(s3.get_object(Bucket=bucket, Key=key)["Body"].read()))


def compact_partition(s3, bucket: str, part: str, objs: list[dict], workers: int, apply: bool) -> dict:
    keys = sorted(o["Key"] for o in objs)
    total_bytes = sum(o["Size"] for o in objs)
    if len(keys) <= 1:
        return {"partition": part, "status": "SKIP", "old_files": len(keys)}
    if not apply:
        return {"partition": part, "status": "DRY_RUN", "old_files": len(keys), "old_bytes": total_bytes}

    tables, expected_rows = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(fetch_table, s3, bucket, k) for k in keys]):
            tbl = fut.result()
            expected_rows += tbl.num_rows
            tables.append(tbl)

    merged = pa.concat_tables(tables, promote_options="default")
    if merged.num_rows != expected_rows:
        raise RuntimeError(f"Row mismatch in {part}: {merged.num_rows} != {expected_rows}")
    if "frame_timestamp" in merged.column_names:
        merged = merged.sort_by([("frame_timestamp", "ascending")])

    buf = io.BytesIO()
    pq.write_table(merged, buf, compression="SNAPPY")
    payload = buf.getvalue()
    new_key = f"{part}/compacted_{int(time.time())}_rows{merged.num_rows}.parquet"

    s3.put_object(Bucket=bucket, Key=new_key, Body=payload, ServerSideEncryption="AES256")
    head = s3.head_object(Bucket=bucket, Key=new_key)
    if head["ContentLength"] != len(payload):
        raise RuntimeError(f"HeadObject size mismatch on {new_key}")

    for i in range(0, len(keys), 1000):
        resp = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in keys[i : i + 1000]], "Quiet": True},
        )
        if resp.get("Errors"):
            raise RuntimeError(f"DeleteObjects errors in {part}: {resp['Errors'][:3]}")

    return {
        "partition": part,
        "status": "COMPACTED",
        "old_files": len(keys),
        "rows": merged.num_rows,
        "old_bytes": total_bytes,
        "new_bytes": len(payload),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bucket", default=os.getenv("COLD_LAKEHOUSE_BUCKET", ""))
    p.add_argument("--prefix", default="telemetry/")
    p.add_argument("--profile", default=os.getenv("AWS_PROFILE", ""))
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--max-partitions", type=int, default=0)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3", config=BOTO_CONFIG)
    if not args.bucket:
        acct = session.client("sts", config=BOTO_CONFIG).get_caller_identity()["Account"]
        env = os.getenv("FORRAD_ENV", "dev")
        args.bucket = f"forrad-cold-lakehouse-{env}-{acct}"

    parts = list_partitions(s3, args.bucket, args.prefix)
    targets = sorted(((k, v) for k, v in parts.items() if len(v) > 1), key=lambda x: x[0])
    if args.max_partitions > 0:
        targets = targets[: args.max_partitions]

    print(
        f"[Plan] Mode={'APPLY' if args.apply else 'DRY-RUN'} | "
        f"Partitions to compact: {len(targets)}/{len(parts)} | "
        f"Shards: {sum(len(v) for _, v in targets)}"
    )
    t0 = time.perf_counter()
    for idx, (part, objs) in enumerate(targets, 1):
        res = compact_partition(s3, args.bucket, part, objs, args.workers, args.apply)
        if res["status"] == "COMPACTED":
            pct = (1.0 - res["new_bytes"] / max(1, res["old_bytes"])) * 100
            print(
                f"  [{idx}/{len(targets)}] {part}: {res['old_files']} -> 1 file "
                f"({res['rows']} rows, {res['old_bytes']//1024} KB -> {res['new_bytes']//1024} KB, -{pct:.1f}%)"
            )
        else:
            print(f"  [{idx}/{len(targets)}] {part}: {res['status']} ({res['old_files']} files, {res.get('old_bytes', 0)//1024} KB)")
    print(f"[Done] Elapsed: {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
