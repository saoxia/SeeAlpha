# Build from the repository root: docker build -f docker/engine.Dockerfile -t seealpha-engine:latest .
FROM rust:1.91-slim AS converter
WORKDIR /build
COPY engine/pipeline/rust_converter/ ./rust_converter/
RUN cargo build --locked --release --manifest-path rust_converter/Cargo.toml

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-default-groups --group api --group research --no-install-project
COPY . .
COPY --from=converter /build/rust_converter/target/release/nautilus-data-converter /app/engine/pipeline/rust_converter/target/release/nautilus-data-converter
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8766
CMD ["python", "-m", "engine"]
