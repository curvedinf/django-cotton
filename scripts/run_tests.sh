#!/usr/bin/env bash

set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage: scripts/run_tests.sh

Runs the Cotton test suite with the correct Django settings module and
ensures the project root is on PYTHONPATH so pytest-django can bootstrap
properly. Pass any pytest arguments directly, for example:

  scripts/run_tests.sh -k slots

USAGE
  exit 0
fi

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"

export PYTHONPATH="${project_root}:${PYTHONPATH:-}"
export DJANGO_SETTINGS_MODULE="django_cotton.tests.test_settings"
cd "${project_root}"

pytest_args=("$@")

if [[ ${#pytest_args[@]} -eq 0 ]]; then
  pytest_args=("django_cotton/tests")
fi

if [[ -n "${PYTEST_TIMEOUT:-}" && "${PYTEST_TIMEOUT:-0}" != "0" ]]; then
  timeout "${PYTEST_TIMEOUT}s" pytest "${pytest_args[@]}"
else
  pytest "${pytest_args[@]}"
fi
