FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    NOT_CR_CONFIG_PATH=/data/config.json \
    NOT_CR_DOCS_DIR=/data/docs \
    NOT_CR_OUTPUT_DIR=/data/output \
    NOT_CR_HOST=0.0.0.0 \
    NOT_CR_PORT=5000 \
    PATH="/app/.venv/bin:$PATH"

# poppler: PDF → imágenes. LibreOffice (sin GUI) + fuentes con las métricas de
# las de Office: DOCX/PPTX → PDF.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        poppler-utils \
        libreoffice-writer-nogui \
        libreoffice-impress-nogui \
        fonts-liberation \
        fonts-crosextra-carlito \
        fonts-crosextra-caladea \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/config', timeout=4)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "app.py"]
