FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY README.md ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY app ./app
COPY alembic.ini ./

CMD ["sh", "-c", "alembic upgrade head && python -m app.main"]
