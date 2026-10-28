FROM ghcr.io/astral-sh/uv:0.9.13 AS uv
FROM python:3.13.9-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --frozen --no-dev --no-install-project && useradd --uid 10001 --create-home lab
COPY services ./services
COPY scripts ./scripts
USER 10001
EXPOSE 8080 8081
CMD ["uvicorn", "services.order.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
