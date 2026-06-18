# AgentsHive MCP Server — Glama-compatible Dockerfile
# This Dockerfile is specifically for the MCP server (stdio transport).
# It is separate from the root Dockerfile which runs the FastAPI web server.
FROM python:3.11-slim

WORKDIR /app

# Copy only what the MCP server needs
COPY classifier.py .
COPY mcp/requirements.txt mcp/
RUN pip install --no-cache-dir -r mcp/requirements.txt
COPY mcp/server.py mcp/

# The MCP server runs via stdio — Glama connects to it for introspection
CMD ["python", "mcp/server.py", "--stdio"]
