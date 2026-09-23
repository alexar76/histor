# HISTOR: the Python service and the WARDEN scanner sidecar in one image. The sidecar is a
# Node subprocess per batch, so the base is Node and Python comes from Debian (3.11 on bookworm,
# which pyproject allows): one libc, one libstdc++, no copied binaries.
FROM node:22-bookworm-slim
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY scanner/package.json scanner/package-lock.json ./scanner/
RUN npm ci --prefix scanner --omit=dev --no-audit --no-fund && npm cache clean --force
COPY scanner/scan.mjs ./scanner/
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY histor ./histor
COPY docs/landing ./docs/landing
# Installed from uv.lock, the set CI tests — not whatever pip resolves on build day. CI runs the
# suite on 3.11 as well, the Python this Debian base ships.
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir uv==0.11.17 \
 && UV_PROJECT_ENVIRONMENT=/opt/venv /opt/venv/bin/uv sync --frozen --no-dev --no-editable \
      --extra postgres --extra pqc --python /opt/venv/bin/python \
 && /opt/venv/bin/pip uninstall -y -q uv && rm -rf /root/.cache \
 && mkdir -p /data && chown 65532:65532 /data
ENV PATH="/opt/venv/bin:${PATH}" \
    HISTOR_HOST=0.0.0.0 \
    HISTOR_PORT=9490 \
    HISTOR_DATA_DIR=/data \
    HISTOR_SCANNER_DIR=/app/scanner \
    HISTOR_LANDING_DIR=/app/docs/landing \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 65532:65532
VOLUME ["/data"]
EXPOSE 9490
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9490/health', timeout=4).read()"
CMD ["python", "-m", "histor", "serve"]
