# GemCode tenant runtime — one pod per user on GKE.
#
# Default: install from repo (includes GEMCODE_HOSTED_TENANT_ROOT locking).
# Pure PyPI: docker build --build-arg GEMCODE_SOURCE=pypi .

FROM python:3.12-slim-bookworm

ARG GEMCODE_PIP_VERSION=0.4.23
ARG GEMCODE_SOURCE=repo

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GEMCODE_WEB_API_HOST=0.0.0.0 \
    GEMCODE_WEB_API_PORT=3001 \
    GEMCODE_HOSTED_TENANT_ROOT=/mnt/workspace \
    GEMCODE_WEB_PROJECT_ROOT=/mnt/workspace \
    GEMCODE_HOME=/mnt/workspace/.gemcode \
    GEMCODE_SUPER_MODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Repo source (hosted multi-tenant patches included)
COPY gemcode/pyproject.toml gemcode/README.md gemcode/LICENSE /build/gemcode/
COPY gemcode/src /build/gemcode/src/

RUN if [ "$GEMCODE_SOURCE" = "pypi" ]; then \
      pip install "gemcode==${GEMCODE_PIP_VERSION}"; \
    else \
      pip install /build/gemcode; \
    fi

COPY deploy/gcp/docker/gemcode-tenant/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN useradd -m -u 1000 gemcode && mkdir -p /mnt/workspace && chown -R gemcode:gemcode /mnt/workspace

USER gemcode
WORKDIR /mnt/workspace
EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:3001/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
