# Independent Platform image: no NautilusTrader or Engine implementation.
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m pip install --no-cache-dir uv==0.13.9
COPY platform/pyproject.toml platform/uv.lock ./platform/
RUN uv sync --project platform --locked --no-default-groups
COPY api/ ./api/
COPY core/ ./core/
COPY migrations/ ./migrations/
COPY strategy/ ./strategy/
COPY alembic.ini ./
COPY scripts/container_api.sh ./scripts/container_api.sh
RUN groupadd --system seealpha && useradd --system --gid seealpha --home-dir /app seealpha \
  && mkdir -p /app/.local/state/api /app/.local/config/api /app/logs /app/data \
  && chown -R seealpha:seealpha /app/.local /app/logs /app/data
USER seealpha
EXPOSE 8000
CMD ["sh", "/app/scripts/container_api.sh"]
