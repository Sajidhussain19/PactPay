# Build from the repo root so the demo sites and seeded contract are included:
#   docker build -f backend/Dockerfile -t pactpay-api .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEMO_DIR=/app/demo \
    ARTIFACTS_DIR=/app/artifacts

WORKDIR /app

# git: shallow-clones submitted repositories (repo_check). Chromium + its system libraries:
# Playwright checks the live sites.
# libatomic1: needed by the Node.js driver bundled with Playwright.
RUN apt-get update && apt-get install -y --no-install-recommends git libatomic1 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install -r requirements.txt && python -m playwright install --with-deps chromium

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
COPY demo ./demo

# Run as non-root; it only needs to write evidence screenshots.
RUN useradd --create-home pactpay && mkdir -p /app/artifacts /app/data && chown -R pactpay /app/artifacts /app/data
USER pactpay

EXPOSE 8000
# One worker: verification progress streams (SSE) live in process memory.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
