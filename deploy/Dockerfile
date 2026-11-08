# syntax=docker/dockerfile:1
# Build from the repo root with the content submodule checked out:
#   docker build -f deploy/Dockerfile --build-arg DOCKER_GID=$(getent group docker | cut -d: -f3) \
#     --build-arg CONTENT_COMMIT=$(git rev-parse HEAD:content) .
FROM docker:29-cli AS docker-cli
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.13-slim

# Only the docker CLI: sandboxes run on the daemon behind /var/run/docker.sock (or DOCKER_HOST).
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app
# --chmod keeps the image readable for the non-root user regardless of the builder's umask.
COPY --chmod=a+rX pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --chmod=a+rX dbot ./dbot
RUN uv sync --frozen --no-dev --no-editable

# content/ has no .git in the image; the submodule commit is passed in instead.
ARG CONTENT_COMMIT=unknown
COPY --chmod=a+rX content ./content

# GID of the host's docker group so the non-root user can use the mounted socket.
ARG DOCKER_GID=999
RUN groupadd --system --gid "${DOCKER_GID}" dockerhost \
    && useradd --system --uid 10001 --gid dockerhost --create-home dbot \
    && install -d -o dbot -g dockerhost /var/lib/dbot /var/cache/dbot

ENV PATH=/app/.venv/bin:$PATH \
    DBOT_CONTENT_ROOT=/app/content \
    DBOT_CONTENT_COMMIT=${CONTENT_COMMIT} \
    DBOT_CONFIG=/etc/dbot/dbot.yaml \
    DBOT_DATA_DIR=/var/lib/dbot \
    DBOT_CACHE_DIR=/var/cache/dbot/assembled

# Not /app: libraries such as logfire read pyproject.toml from the working directory.
WORKDIR /var/lib/dbot
USER dbot
ENTRYPOINT ["dbot"]
CMD ["serve"]
