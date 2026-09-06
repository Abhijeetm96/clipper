# Universal Video Clipper - Production Dockerfile
FROM python:3.11-slim

# Install system dependencies: FFmpeg, aria2c, nodejs (for YouTube n-challenge solving)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    aria2 \
    nodejs \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create non-root system user for security
RUN useradd -m -u 1001 appuser

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure storage directories exist and assign ownership
RUN mkdir -p /app/downloads /app/clips && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose standard port
EXPOSE 5000

# Environment variables
ENV PORT=5000 \
    HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1 \
    PRODUCTION=1

# Container Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# Production WSGI launch
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "3600", "app:app"]
