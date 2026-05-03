FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip


FROM base AS dev
COPY . .
RUN pip install -e ".[dev]"
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS prod
COPY . .
RUN pip install -e .

# Run as non-root. /data and /backups are pre-created so named volumes
# inherit app:app ownership on first init.
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /data /backups \
    && chown -R app:app /app /data /backups
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/system/health').read()" \
        || exit 1

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1"]
