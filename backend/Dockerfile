FROM python:3.12-slim

WORKDIR /app

# Install system dependencies including curl for healthcheck, Node.js for Boltz claim signing, ffmpeg for thumbnails
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    curl \
    libmagic1 \
    ca-certificates \
    gnupg \
    ffmpeg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
       | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
       > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install Node.js dependencies for Boltz claim signing (Taproot Musig2)
RUN cd scripts && npm install --omit=dev && cd ..

# Create non-root user for running the application
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Pre-create volume mount points for media library (outside /app which is a read-only bind mount)
RUN mkdir -p /data/media_cache /data/tool_outputs/acestep-music-generation \
    /data/tool_outputs/qwen3-tts-voice /data/tool_outputs/zimage-generation \
    /data/tool_outputs/seedvr2-upscaler /data/tool_outputs/canary-stt \
    /data/tool_outputs/ltx-video-generation /data/tool_outputs/audiosr-enhance \
    /data/tool_outputs/media-toolkit /data/tool_outputs/realesrgan-cpu-upscaler \
    /data/tool_outputs/docling-parser

# Ensure the app user owns the application directory and upload volume mount point
RUN chown -R appuser:appuser /app /data

# Expose port
EXPOSE 8000

# Switch to non-root user
USER appuser

# Disable core dumps — prevents memory extraction of secrets (macaroons, API keys)
# via /proc/pid/mem or crash dumps.
ENV PYTHONDONTWRITEBYTECODE=1

# Run migrations, initialize tools catalog, and start server
# ulimit -c 0 disables core dump file creation at the process level.
CMD ["sh", "-c", "ulimit -c 0 && alembic upgrade head && python init_tools_catalog.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
