#!/usr/bin/env bash

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 /path/to/blazed_deals" >&2
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$1"

if [ ! -d "$TARGET_DIR" ]; then
    echo "Target directory does not exist: $TARGET_DIR" >&2
    exit 1
fi

export PYENV_VERSION="${COTTON_PYENV:-django-cotton}"

cd "$PROJECT_ROOT"

mkdir -p dist
rm -f dist/django_cotton-*.whl

python -m build --wheel --no-isolation

LATEST_WHEEL="$(ls -1t dist/django_cotton-*.whl | head -n1)"
if [ -z "$LATEST_WHEEL" ]; then
    echo "No wheel was produced, build failed." >&2
    exit 1
fi

WHEEL_BASENAME="$(basename "$LATEST_WHEEL")"
LIBS_DIR="$TARGET_DIR/libs"

mkdir -p "$LIBS_DIR"
cp "$LATEST_WHEEL" "$LIBS_DIR/$WHEEL_BASENAME"

REQ_FILE="$TARGET_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
    python - "$REQ_FILE" "$WHEEL_BASENAME" <<'PY'
import sys
from pathlib import Path

requirements_path = Path(sys.argv[1])
wheel_name = sys.argv[2]
needle = "./libs/django_cotton-"
replacement = f"./libs/{wheel_name}"

lines = requirements_path.read_text().splitlines()
updated = []
modified = False

for line in lines:
    if line.strip().startswith(needle):
        updated.append(replacement)
        modified = True
    else:
        updated.append(line)

if modified:
    requirements_path.write_text("\n".join(updated) + "\n")
PY
fi

echo "Built $WHEEL_BASENAME and copied to $LIBS_DIR"
