FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=prod

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/
COPY i18n/ ./i18n/
COPY alembic.ini ./
COPY alembic/ ./alembic/

# SQLite lives here in dev; mount a volume in prod or set DATABASE_URL.
VOLUME ["/app/data"]
ENV DATABASE_URL=sqlite:////app/data/frontdesk.db

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
