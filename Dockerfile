# Node security baseline: 22.23.4 (2026-10-21 security release).
# HISTOR: the Python service and the WARDEN scanner sidecar in one image. The sidecar is a
# Node subprocess per batch, so the base is Node and Python comes from Debian (3.11 on bookworm,
# which pyproject allows): one libc, one libstdc++, no copied binaries.
# The deployment target can retain the accepted live scanner and its complete dependency tree.
# Ordinary builds use the bundled scanner; they do not need a production image.
ARG HISTOR_SCANNER_IMAGE=node:22.23.4-bookworm-slim
FROM ${HISTOR_SCANNER_IMAGE} AS accepted-scanner
# Legacy builders also visit unused stages. Give the ordinary Node fallback an empty source.
USER root
RUN mkdir -p /app/scanner/node_modules
FROM node:22.23.4-bookworm-slim AS application
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY scanner/package.json scanner/package-lock.json ./scanner/
COPY scanner/vendor ./scanner/vendor
RUN npm ci --prefix scanner --omit=dev --no-audit --no-fund && npm cache clean --force
COPY scanner/scan.mjs ./scanner/
COPY scanner/quality ./scanner/quality
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY histor ./histor
COPY docs/landing ./docs/landing
# Installed from uv.lock, the set CI tests — not whatever pip resolves on build day. CI runs the
# suite on 3.11 as well, the Python this Debian base ships.
# The o200k_base table for /api/v1/.../cost is fetched here, at build time, into the image: the
# running service never downloads it.
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir uv==0.11.17 \
 && UV_PROJECT_ENVIRONMENT=/opt/venv /opt/venv/bin/uv sync --frozen --no-dev --no-editable \
      --extra postgres --extra pqc --extra cost --python /opt/venv/bin/python \
 && TIKTOKEN_CACHE_DIR=/opt/tiktoken /opt/venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')" \
 && /opt/venv/bin/pip uninstall -y -q uv && rm -rf /root/.cache \
 && mkdir -p /data && chown 65532:65532 /data
ENV PATH="/opt/venv/bin:${PATH}" \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
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

FROM application AS with-live-scanner
USER root
RUN rm -rf /app/scanner/node_modules
COPY --from=accepted-scanner /app/scanner/node_modules /app/scanner/node_modules
USER 65532:65532

# Keep the regular, reproducible vendored build as the default target.
FROM application AS bundled
