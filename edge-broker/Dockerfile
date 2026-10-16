# ForRad Phase 2 HDEF Zero-Trust Edge Broker Dockerfile (Red Hat UBI 9 Minimal)
# Security Invariant: Private keys (certs/*.key) are NEVER baked into the image;
# they are mounted read-only (-v ./certs:/certs:ro) at container runtime.

FROM registry.access.redhat.com/ubi9/python-312-minimal:9.5

USER 0
RUN microdnf upgrade -y && \
    microdnf install -y ca-certificates curl && \
    curl -sSL "https://rolesanywhere.amazonaws.com/releases/1.8.5/X86_64/Linux/Amzn2023/aws_signing_helper" \
      -o /usr/local/bin/aws_signing_helper && \
    chmod +x /usr/local/bin/aws_signing_helper && \
    microdnf clean all

USER 1001
WORKDIR /app
RUN pip install --no-cache-dir boto3
COPY --chown=1001:0 telemetry_sync.py /app/telemetry_sync.py

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import os, sys; sys.exit(0 if os.path.exists('/app/telemetry_sync.py') else 1)"

ENTRYPOINT ["python3", "/app/telemetry_sync.py"]
