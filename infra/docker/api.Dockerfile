# One image runs the API, the worker and the one-shot migration job (different commands).
# Build context: repository root.
FROM python:3.12-slim AS build
# Optional integrations: --build-arg EXTRAS=pinecone,embeddings,aws  (embeddings pulls in PyTorch)
ARG EXTRAS=""
# CPU-only PyTorch keeps the embeddings image small (NOT verified in this repo's sandbox: the
# index was unreachable there; the default CUDA build of the embeddings extra is ~9.8 GB):  --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_INDEX_URL=""
WORKDIR /build
COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/app ./app
# Runtime dependencies only (no dev extras); installed into an isolated prefix.
# Behind a TLS-inspecting proxy, supply its CA:  docker build --secret id=ca,src=ca-bundle.crt ...
RUN --mount=type=secret,id=ca,target=/run/secrets/ca.crt,required=false \
    set -eu; \
    if [ -s /run/secrets/ca.crt ]; then export PIP_CERT=/run/secrets/ca.crt; fi; \
    if [ -n "$TORCH_INDEX_URL" ]; then pip install --no-cache-dir --prefix=/install torch --index-url "$TORCH_INDEX_URL"; fi; \
    pip install --no-cache-dir --prefix=/install ".${EXTRAS:+[$EXTRAS]}"

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/apps/api:/app/apps/worker
# No OS packages needed: psycopg[binary] bundles libpq and every dependency ships a wheel.
RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin hospitality
COPY --from=build /install /usr/local
WORKDIR /app/apps/api
COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/scripts ./scripts
COPY apps/api/seed_data ./seed_data
COPY apps/worker/worker /app/apps/worker/worker
# Uploaded documents / local vector data (dev only) live here; mount a volume to keep them.
RUN mkdir -p /app/apps/api/var && chown -R hospitality /app/apps/api/var
USER hospitality
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
