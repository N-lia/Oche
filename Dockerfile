FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120 \
    HOME=/data/oyi-memory \
    MEM0_DATA_DIR=/data/oyi-memory \
    MEM0_DIR=/data/oyi-memory/.mem0 \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY config.py main.py agent_service.py memory.py privacy.py router.py telegram_support.py telegram_format.py TG.py voice.py subagent.py tool.py asr_client.py oyi_asr_pb2.py oyi_asr_pb2_grpc.py docker-entrypoint.sh ./
COPY --chown=65532:65532 tavily_response/ ./tavily_response/

RUN mkdir -p /data/oyi-memory/workspace \
    && chmod 0555 /app/docker-entrypoint.sh \
    && chown -R 65532:65532 /app /data

USER 65532:65532

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "TG.py"]