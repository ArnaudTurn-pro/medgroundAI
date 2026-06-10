# Builds and runs the medground MCP server (stdio) for inspection, e.g. by Glama.
# The server starts and lists its tools with no corpus and no API key; data and
# credentials are only needed when a tool is actually called.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Install locked dependencies first for layer caching, then the project itself.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Writable data dir; the server creates its DuckDB/Kuzu files here on demand.
ENV MG_DATA_DIR=/data
RUN mkdir -p /data

# medground-mcp speaks the MCP stdio transport. An inspector starts it and runs
# the initialize + tools/list introspection handshake over stdin/stdout.
ENTRYPOINT ["uv", "run", "--no-dev", "medground-mcp"]
