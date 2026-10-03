# MusicContext container image.
#
# Multi-stage: the builder produces a wheel, the runtime installs it and nothing else,
# so no compiler or build backend ships in the final image.
#
# No HEALTHCHECK on purpose. MusicContext is a CLI (and a stdio MCP server), not a
# long-running service: there is no endpoint to poll, and a container that exits 0 after
# doing its job is the expected outcome, not an unhealthy one.

FROM python:3.12-slim AS builder

WORKDIR /src
# Only what the build backend needs; .dockerignore keeps the context small.
RUN pip install --no-cache-dir build
COPY . .
RUN python -m build --wheel --outdir /wheels


FROM python:3.12-slim AS runtime

# FFmpeg is the one hard runtime dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

# Non-root. /data holds the writable state so the image itself stays read-only.
RUN useradd --create-home --uid 10001 musiccontext \
    && mkdir -p /data/musiccontext \
    && chown -R musiccontext:musiccontext /data

ENV MUSICCONTEXT_HOME=/data/musiccontext \
    MUSICCONTEXT_CACHE_DIR=/data/musiccontext/cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

VOLUME ["/data/musiccontext"]
USER musiccontext
WORKDIR /work

ENTRYPOINT ["musiccontext"]
CMD ["--help"]
