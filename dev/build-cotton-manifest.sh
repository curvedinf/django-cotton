#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${COTTON_PYTHON:-python}"
OUTPUT_PATH="${COTTON_MANIFEST_OUTPUT:-$PROJECT_ROOT/cotton-manifest.json}"

echo "Compiling cotton templates into $OUTPUT_PATH"
"$PYTHON_BIN" manage.py cotton_compile --output "$OUTPUT_PATH"

echo "Cotton manifest written to $OUTPUT_PATH"
