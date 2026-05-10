# EDAC Production Server
# Build: docker build -t edac .
# Run:   docker run -p 8000:8000 -e EDAC_OLLAMA_BASE_URL=http://host.docker.internal:11434 edac

FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir -e ".[all]" \
    && pip install --no-cache-dir fastapi uvicorn click httpx aiosqlite

# Copy source
COPY edac/ ./edac/
COPY README.md ./

# Install in editable mode
RUN pip install --no-cache-dir -e .

# Data directory
RUN mkdir -p /app/data /app/skills
ENV EDAC_DATA_DIR=/app/data
ENV EDAC_SKILLS_DIR=/app/skills
ENV EDAC_DATABASE_URL=sqlite+aiosqlite:///app/data/edac.db

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Run server
CMD ["edac", "serve", "--host", "0.0.0.0", "--port", "8000"]
