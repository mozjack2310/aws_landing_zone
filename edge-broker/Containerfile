# syntax=docker/dockerfile:1
FROM registry.access.redhat.com/ubi9/python-312:1

# Security baseline: Run as non-root user (CKV_DOCKER_8)
USER 1001
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Install runtime dependencies
RUN pip install --no-cache-dir boto3 redis pyarrow fastapi uvicorn httpx

# Copy edge runtime daemons and orchestrator with non-root ownership
COPY --chown=1001:0 telemetry_sync.py /app/telemetry_sync.py
COPY --chown=1001:0 cold_parquet_sync.py /app/cold_parquet_sync.py
COPY --chown=1001:0 udp_iot_collector.py /app/udp_iot_collector.py
COPY --chown=1001:0 sse_broadcaster.py /app/sse_broadcaster.py
COPY --chown=1001:0 orchestrator.py /app/orchestrator.py
COPY --chown=1001:0 static/ /app/static/

# Expose UDP telemetry port (Hop 1) and FastAPI SSE streaming port (Hop 3)
EXPOSE 20777/udp 8000/tcp

# Healthcheck verifying supervisor process readiness (CKV_DOCKER_2)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request, sys; r = urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3); sys.exit(0 if r.status == 200 else 1)"

CMD ["python3", "/app/orchestrator.py"]
