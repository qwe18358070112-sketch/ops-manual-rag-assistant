FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    poppler-utils \
    libreoffice-core \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY app /app/app
COPY scripts /app/scripts
COPY deploy /app/deploy
COPY docs /app/docs

RUN python -m venv /app/.venv && /app/.venv/bin/pip install --upgrade pip && /app/.venv/bin/pip install -e '.[dev]'

ENV OPS_ASSISTANT_HOST=0.0.0.0 \
    OPS_ASSISTANT_PORT=8000 \
    OPS_ASSISTANT_DATA_DIR=/var/lib/ops-assistant-data

EXPOSE 8000

CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
