#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CRATE_DIR="${PROJECT_ROOT}/django_cotton/_fastcompiler"

if ! command -v cargo >/dev/null 2>&1; then
    echo "cargo is required to build the Rust accelerator." >&2
    exit 1
fi

pushd "${CRATE_DIR}" >/dev/null

echo "Building Rust accelerator with cargo..."
cargo build --release

case "$(uname -s)" in
    Darwin*)
        LIB_PREFIX="lib"
        LIB_EXT=".dylib"
        ;;
    MINGW*|MSYS*|CYGWIN*|Windows*)
        LIB_PREFIX=""
        LIB_EXT=".dll"
        ;;
    *)
        LIB_PREFIX="lib"
        LIB_EXT=".so"
        ;;
esac

SOURCE_PATH="target/release/${LIB_PREFIX}_fastcompiler${LIB_EXT}"
if [ ! -f "${SOURCE_PATH}" ]; then
    echo "Compiled library not found at ${SOURCE_PATH}" >&2
    exit 1
fi

PYTHON_BIN="${PYTHON:-python}"
EXT_SUFFIX="$("${PYTHON_BIN}" - <<'PY'
import importlib.machinery
suffixes = importlib.machinery.EXTENSION_SUFFIXES
abi3 = next((s for s in suffixes if "abi3" in s), None)
print(abi3 or suffixes[0])
PY
)"

TARGET_PATH="${PROJECT_ROOT}/django_cotton/_fastcompiler${EXT_SUFFIX}"

echo "Copying accelerator to ${TARGET_PATH}"
cp "${SOURCE_PATH}" "${TARGET_PATH}"

popd >/dev/null

echo "Rust accelerator built successfully."
