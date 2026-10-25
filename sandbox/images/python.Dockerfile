# Node security baseline: 22.23.4 (2026-10-21 security release).
# The image HISTOR starts PyPI packages in, built on each sandbox host by install.sh, one per
# Python a package may require (3.12 by default, 3.13 and 3.14 when Requires-Python says so).
#
# The official slim image plus the programs a developer's machine has and MCP servers start:
# git, Node 22 (servers that wrap a local JavaScript file), a Java 21 runtime. Never Docker: the
# socket would be a way out of the sandbox.
ARG PYTHON=3.12
FROM eclipse-temurin:21-jre AS java
FROM node:22.23.4-bookworm-slim AS node
FROM python:${PYTHON}-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=java /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LABEL org.modelmarket.histor.sandbox="pypi"
