#!/usr/bin/env bash
set -euo pipefail
test "$*" = smoke
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v ${PROJECT_DIR:?}:/app:ro -w /app python:3.12.10-slim python -m unittest discover -s tests -v
