#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_FILE="${SCRIPT_DIR}/llm_config.template.json"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--file <llm_config.json>] [--assign <group_name_or_id>] [additional CLI options]

Registers or overwrites a purpose-tagged LLM_Config node in Neo4j via:
  uv run gosmart-graphite configure llm-config

Options:
  -f, --file <path>     Path to a single LLM_Config JSON object (default: ${DEFAULT_FILE})
  -a, --assign <group>  Target Group ID, name, or code (default: 'GoSmart Health')
  -h, --help            Show this help message

Examples:
  $(basename "$0") --file devop-scripts/llm_config.template.json
  $(basename "$0") --file /path/to/vertex_embed.json --assign "GoSmart Health"
EOF
}

FILE_ARG=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -f|--file)
      if [[ $# -lt 2 ]]; then
        echo "Error: $1 requires a file path argument." >&2
        exit 1
      fi
      FILE_ARG="$2"
      shift 2
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -z "${FILE_ARG}" ]]; then
  if [[ ${#EXTRA_ARGS[@]} -gt 0 && "${EXTRA_ARGS[0]}" != -* ]]; then
    FILE_ARG="${EXTRA_ARGS[0]}"
    EXTRA_ARGS=("${EXTRA_ARGS[@]:1}")
  else
    FILE_ARG="${DEFAULT_FILE}"
  fi
fi

cd "${PROJECT_ROOT}"
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  exec uv run gosmart-graphite configure llm-config --file "${FILE_ARG}" "${EXTRA_ARGS[@]}"
else
  exec uv run gosmart-graphite configure llm-config --file "${FILE_ARG}"
fi
