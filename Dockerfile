FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY config.py main.py TG.py README.md ./
RUN uv sync --locked --no-dev

RUN chown -R 65532:65532 /app

USER 65532:65532

CMD ["python", "TG.py"]