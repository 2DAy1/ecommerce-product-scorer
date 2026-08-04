FROM python:3.13-slim AS backend-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN addgroup --system app \
    && adduser --system --ingroup app --home /home/app app \
    && mkdir -p /home/app \
    && chown app:app /home/app

FROM backend-base AS backend

COPY . .
RUN chmod +x /app/docker/entrypoint.sh && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]

FROM backend-base AS worker

USER root

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements-worker.txt ./
RUN pip install --no-cache-dir -r requirements-worker.txt \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY . .
RUN chmod +x /app/docker/entrypoint.sh && chown -R app:app /app

USER app

CMD ["celery", "-A", "config", "worker", "--loglevel=INFO"]
