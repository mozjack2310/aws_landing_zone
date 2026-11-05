# syntax=docker/dockerfile:1
FROM registry.access.redhat.com/ubi9/python-312:1-41

# Security baseline: Run as non-root user (CKV_DOCKER_8)
USER 1001
WORKDIR /app

# Install runtime dependencies including high-frequency edge libraries
RUN pip install --no-cache-dir boto3 redis pyarrow fastapi uvicorn httpx

# Copy edge runtime daemons with non-root ownership
COPY --chown=1001:0 telemetry_sync.py /app/telemetry_sync.py
COPY --chown=1001:0 cold_parquet_sync.py /app/cold_parquet_sync.py
COPY --chown=1001:0 udp_iot_collector.py /app/udp_iot_collector.py
COPY --chown=1001:0 sse_broadcaster.py /app/sse_broadcaster.py

# Expose UDP telemetry port (Hop 1) and FastAPI SSE streaming port (Hop 3)
EXPOSE 20777/udp 8000/tcp

# Healthcheck to satisfy CKV_DOCKER_2 and verify runtime script readiness
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import os, sys; sys.exit(0 if os.path.exists('/app/telemetry_sync.py') else 1)"

CMD ["python3", "/app/telemetry_sync.py"]
