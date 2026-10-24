FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_KNOWLEDGE_ROOT=/app/knowledge \
    MCP_WORKSPACE_ROOT=/app/workspace \
    MCP_REPOSITORY_ROOT=/app \
    MCP_RUNTIME_ROOT=/app/.runtime \
    MCP_SANDBOX=local \
    MCP_COMMAND_OUTPUT_BYTES=20000 \
    MCP_WEB_DIST=/app/web/dist

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY knowledge ./knowledge
COPY workspace ./workspace
COPY eval ./eval
COPY docs ./docs
COPY --from=web /web/dist ./web/dist
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 mcp \
    && mkdir -p /app/.runtime \
    && chown -R mcp:mcp /app/knowledge /app/workspace /app/.runtime /app/docs
USER mcp

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import json,urllib.request; json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2))"

CMD ["knowledge-mcp"]
