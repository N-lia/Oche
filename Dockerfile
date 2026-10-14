FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    MEM0_DATA_DIR=/data/oyi-memory \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY config.py main.py agent_service.py memory.py privacy.py router.py telegram_support.py TG.py README.md ./
RUN uv sync --locked --no-dev

RUN mkdir -p /data/oyi-memory && chown -R 65532:65532 /app /data

USER 65532:65532

CMD ["python", "TG.py"]