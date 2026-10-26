FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH="/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .

# The folders exist in the image, owned by the app user, so the volumes mounted over them in
# production start out writable.
RUN useradd --system --no-create-home app \
    && mkdir -p /app/uploads /app/staticfiles \
    && chown -R app /app /venv
USER app

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
