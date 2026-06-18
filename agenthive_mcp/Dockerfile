# AgentsHive MCP Server — Glama-compatible Dockerfile
# Directory renamed from mcp/ to agenthive_mcp/ to avoid collision with pip 'mcp' package
FROM python:3.11-slim

WORKDIR /app

# Install MCP dependencies
RUN pip install --no-cache-dir mcp requests

# Copy only what the MCP server needs (classifier.py from parent)
COPY classifier.py .

# Copy and run MCP server
COPY agenthive_mcp/server.py .

# The MCP server runs via stdio — Glama connects for introspection
CMD ["python", "server.py", "--stdio"]
