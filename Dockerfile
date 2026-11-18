# OUSSAMA Cutter — production container (v8.10)
#
# One-command setup: docker compose up --build
# Runs the Gradio WebUI on http://localhost:7860 with everything bundled
# (Python 3.11, ffmpeg, yt-dlp, whisperx CPU path, OpenCV, Gradio).
#
# NOTE: InsightFace is intentionally NOT installed here (heavy native deps);
# the app falls back to MediaPipe → OpenCV Haar automatically, and the
# tracking report records which backend actually ran.
#
# Multi-stage build: gcc/g++/make/python3-dev live only in the builder stage;
# the final image installs prebuilt wheels and runs as a non-root user.

FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System deps for building native wheels only (numpy/onnx fallbacks)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        make \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRALCUTTER_SKIP_PREFLIGHT=1 \
    VIRALCUTTER_HOST=0.0.0.0

WORKDIR /app

# Runtime system deps only: ffmpeg (video) and fonts (Arabic)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install from prebuilt wheels (no compilers in the final image)
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# App source
COPY . .

# VIRALS project root (host-mountable for persistence)
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/VIRALS /app/models /app/.venv \
    && chown -R app:app /app

USER app

EXPOSE 7860

# 200 and 401 both prove the server is up (401 = auth enabled, probe unauthenticated)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python3", "/app/healthcheck.py"]

CMD ["python3", "webui/app.py", "--preflight", "off"]
