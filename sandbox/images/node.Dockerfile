# The image HISTOR starts npm packages in, built on each sandbox host by install.sh.
#
# Debian, not Alpine: most users run Node on glibc (macOS, Windows, Debian/Ubuntu), and the
# prebuilt binaries packages ship — optional dependencies with libc: glibc, N-API addons,
# downloaded servers — do not load on Alpine's musl. Plus the programs a developer's machine has
# and MCP servers start: bash, git, Python 3, a Java 21 runtime. Never Docker: the socket would be
# a way out of the sandbox.
ARG NODE=22
FROM eclipse-temurin:21-jre AS java
FROM node:${NODE}-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=java /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LABEL org.modelmarket.histor.sandbox="npm"
