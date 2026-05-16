# hw-router-service — self-hosted FreeRouting microservice
#
# Headless HTTP API: POST DSN, get SES. Stateless, runs anywhere
# Docker runs (Fly.io, Railway, Docker, k8s, your VPS, your laptop).
#
# Build (single arch):
#   docker build -t hw-router-service:0.1.0 .
#
# Build (multi-arch, recommended):
#   docker buildx build --platform linux/amd64,linux/arm64 \
#       -t hw-router-service:0.1.0 . --load
#
# Run as a single container (no queue, blocks on each job):
#   docker run --rm -p 8000:8000 hw-router-service:0.1.0
#
# Run with full queue (recommended) — see docker-compose.yml at repo root:
#   docker compose up --build
#
# Then:  curl http://localhost:8000/health
# Or:    POST DSN to http://localhost:8000/jobs

# ─── Stage 1: pull Java 25 JRE from Eclipse Temurin ─────────────────────────
# FreeRouting v2.2.x is compiled with Java 25 (class file version 69).
# Debian's default-jre is still on Java 21 (class version 65), which can't
# load FR's main class. We multi-stage Temurin's official JRE to get JDK 25
# without polluting the runtime image with their full Ubuntu base.

FROM eclipse-temurin:25-jre-noble AS jre

# ─── Stage 2: runtime ───────────────────────────────────────────────────────

FROM python:3.12-slim AS base

LABEL org.opencontainers.image.title="hw-router-service" \
      org.opencontainers.image.description="Self-hosted FreeRouting headless HTTP API for Freight Flow AI" \
      org.opencontainers.image.source="https://github.com/example/freight-flow-ai" \
      org.opencontainers.image.licenses="GPL-3.0"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8

# ─── System packages ────────────────────────────────────────────────────────
# tini: PID-1 signal handling so SIGTERM cleanly stops the JVM
# curl: healthchecks + downloading FreeRouting JAR

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Copy Eclipse Temurin's JRE (Java 25). The image puts it under /opt/java
# in temurin's image layout. We adopt the same path for ease of upgrade.
COPY --from=jre /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/java/openjdk/bin:$PATH \
    JAVA_BIN=/opt/java/openjdk/bin/java

RUN ln -sf /opt/java/openjdk/bin/java /usr/local/bin/java

# ─── FreeRouting JAR ────────────────────────────────────────────────────────
# Pinned to v2.2.2 (latest as of May 2026). Bump ARG to upgrade.
# The single .jar asset (no `-cli` suffix in 2.2.x) supports headless
# mode via `java -jar freerouting-X.Y.Z.jar -de in.dsn -do out.ses`.

ARG FREEROUTING_VERSION=2.2.2
RUN mkdir -p /app && \
    curl -fsSL --retry 3 --retry-delay 2 \
        -o /app/freerouting.jar \
        "https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar" \
    && test -s /app/freerouting.jar \
    && echo "FreeRouting JAR: $(ls -lh /app/freerouting.jar | awk '{print $5}')"
ENV FREEROUTING_JAR=/app/freerouting.jar

# ─── Python service ─────────────────────────────────────────────────────────

WORKDIR /service
COPY pyproject.toml /service/
COPY src /service/src
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ─── Job data dir + non-root user ───────────────────────────────────────────
# Run as a non-root user. Owns the job-data dir so DSN/SES files written
# by the worker can be read by the API process.

RUN mkdir -p /data/jobs && \
    useradd --system --uid 1000 --home /service --no-create-home routerd && \
    chown -R routerd:routerd /data/jobs /service

USER routerd

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# tini = PID 1; cleanly forwards signals to the Python process
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default command runs the API. The worker container in docker-compose
# overrides this with `rq worker router` to consume the queue.
CMD ["hw-router-service"]
