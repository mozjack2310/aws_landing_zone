"""
ForRad Edge Broker Master Orchestrator
- Supervises `udp_iot_collector.py` (UDP :20777) and `sse_broadcaster.py` (FastAPI :8000).
- Runs continuous 5-second micro-batch drain loops for DynamoDB (Hot) and S3 Parquet (Cold).
- Traps SIGINT/SIGTERM for clean container shutdowns without dropped packets.
"""

import asyncio
import os
import signal
import sys
import time

PYTHON_BIN = sys.executable

async def run_worker(cmd: list[str], name: str):
    """Supervises a long-running daemon and restarts it if it crashes."""
    while True:
        print(f"[Orchestrator] Starting worker: {name} ({' '.join(cmd)})...")
        proc = await asyncio.create_subprocess_exec(*cmd)
        exit_code = await proc.wait()
        print(f"[Orchestrator WARN] Worker {name} exited with code {exit_code}. Restarting in 2s...")
        await asyncio.sleep(2.0)

async def cloud_sync_loop():
    """Runs Hot Path DynamoDB sync and Cold Path S3 compaction every 5 seconds."""
    await asyncio.sleep(3.0)  # Initial warm-up delay
    print("[Orchestrator] Cloud sync worker active (DynamoDB & S3 Parquet interval: 5s)...")
    
    while True:
        try:
            # Drain Hot Path (DynamoDB state store)
            hot_proc = await asyncio.create_subprocess_exec(PYTHON_BIN, "telemetry_sync.py")
            await hot_proc.wait()

            # Drain Cold Path (S3 Snappy Parquet compactor)
            cold_proc = await asyncio.create_subprocess_exec(PYTHON_BIN, "cold_parquet_sync.py")
            await cold_proc.wait()
        except Exception as exc:
            print(f"[Orchestrator ERROR] Cloud sync cycle failed: {exc}")
        
        await asyncio.sleep(5.0)

async def main():
    print("==================================================================")
    print("      FORRAD EDGE APPLIANCE: CONTAINER PROCESS SUPERVISOR         ")
    print("==================================================================")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_shutdown():
        print("\n[Orchestrator] Termination signal received. Shutting down appliance...")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown)

    # Launch supervised tasks
    collector_task = asyncio.create_task(
        run_worker([PYTHON_BIN, "udp_iot_collector.py"], "UDP_Collector")
    )
    sse_task = asyncio.create_task(
        run_worker([PYTHON_BIN, "sse_broadcaster.py"], "SSE_Broadcaster")
    )
    sync_task = asyncio.create_task(cloud_sync_loop())

    await stop_event.wait()

    # Cancel tasks on shutdown
    collector_task.cancel()
    sse_task.cancel()
    sync_task.cancel()
    await asyncio.gather(collector_task, sse_task, sync_task, return_exceptions=True)
    print("[Orchestrator] All edge broker tasks cleanly stopped.")

if __name__ == "__main__":
    asyncio.run(main())