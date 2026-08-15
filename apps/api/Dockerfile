FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv for dependency management.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY uv.lock* ./
RUN uv sync --no-dev --no-install-project

# Then the application source.
COPY nomadata ./nomadata
RUN uv sync --no-dev

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "nomadata.main:app", "--host", "0.0.0.0", "--port", "8000"]
