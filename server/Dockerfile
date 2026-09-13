# Stateless server for the hybrid scanned-PDF OCR path (POST /detect).
# See docs/superpowers/specs/2026-09-13-hybrid-scanned-pdf-ocr-design.md.
#
# Build from the REPO ROOT, not from inside server/ -- this needs engine/
# and the root requirements.txt, both of which live outside server/:
#   docker build -f server/Dockerfile -t <tag> .
# (server/deploy.sh, Task 4, does this for you.)
FROM python:3.12-slim

# Matches .github/workflows/ci.yml's system Tesseract install -- pytesseract
# (engine/scan_cv/ocr.py) shells out to the system binary; pip alone does
# not provide it. libgl1 is required by opencv-python (engine/scan_cv/
# preprocess.py's `import cv2`) -- python:3.12-slim omits it, and cv2 fails
# to import at runtime without it (ImportError: libGL.so.1).
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY server/requirements.txt ./server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY engine/ ./engine/
COPY server/ ./server/

ENV PYTHONPATH=/app
EXPOSE 8080

# Cloud Run sets $PORT at runtime; shell form so that substitution happens.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --timeout 180 --workers 1 server.app:app"]
