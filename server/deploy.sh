#!/usr/bin/env bash
# Builds server/Dockerfile against the repo root (needed for engine/ and
# requirements.txt, both outside server/), pushes it to Artifact Registry,
# and deploys it to Cloud Run. Run from the repo root:
#
#   PROJECT_ID=<your-gcp-project> ./server/deploy.sh
#
# One-time setup before the first run: see Task 4, Step 1 of
# docs/superpowers/plans/2026-09-13-hybrid-scanned-pdf-ocr.md (gcloud auth
# login, project selection, enabling APIs, creating the Artifact Registry
# repo, `gcloud auth configure-docker`).
#
# This deploys real, billed cloud infrastructure. Run it only when you have
# decided to -- never as an unattended or automatic step.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID to your GCP project id}"
REGION=us-central1
REPO=fillpdf
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/ocr:$(git rev-parse --short HEAD)"

if [ ! -f requirements.txt ] || [ ! -d server ]; then
  echo "run this from the repo root, not from inside server/" >&2
  exit 1
fi

docker build --platform linux/amd64 -f server/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"

gcloud run deploy fillpdf-ocr \
  --image "$IMAGE" \
  --region "$REGION" \
  --allow-unauthenticated \
  --max-instances 3 \
  --concurrency 1 \
  --timeout 180 \
  --memory 2Gi \
  --cpu 2 \
  --set-env-vars ALLOWED_ORIGIN=https://fillpdf.ryanxu.dev

echo "Deployed. Copy the Service URL printed above -- Task 5 needs it."
