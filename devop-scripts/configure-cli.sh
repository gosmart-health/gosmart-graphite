#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_FILE="${SCRIPT_DIR}/llm_config.template.json"

usage() {
  cat <<EOF
Usage: $(basename "$0") --configure {dicom|fhir|llm} [--file <config.json>] [--assign <group_name_or_id>] [additional CLI options]

Registers or overwrites a configuration node and assigns the node
to a specified group.

Options:
  -c, --configure {dicom | fhir | llm}  The configuration target (required)
  -f, --file <path>     Path to a single configuration JSON object (default depends on target)
  -a, --assign <group>  Target Group ID, name, or code (default: 'GoSmart Health')
  -h, --help            Show this help message

Examples:
  $(basename "$0") --configure llm --file devop-scripts/llm_config.template.json
  $(basename "$0") --configure dicom --file devop-scripts/dicom_config.template.json
  $(basename "$0") --configure fhir --file /path/to/fhir_config.json --assign "GoSmart Health"
EOF
}

FILE_ARG=""
CONFIG_ARG=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -c|--configure)
      if [[ $# -lt 2 ]]; then
        echo "Error: $1 requires a configuration target argument. Valid values are: dicom, fhir, llm." >&2
        exit 1
      fi
      case "$2" in
        dicom)
          CONFIG_ARG="dicom-config"
          DEFAULT_FILE="${SCRIPT_DIR}/dicom_config.template.json"
          ;;
        fhir)
          CONFIG_ARG="fhir-config"
          DEFAULT_FILE="${SCRIPT_DIR}/fhir_config.template.json"
          ;;
        llm)
          CONFIG_ARG="llm-config"
          DEFAULT_FILE="${SCRIPT_DIR}/llm_config.template.json"
          ;;
        *)
          echo "Error: Invalid configuration target '$2'. Valid values are: dicom, fhir, llm." >&2
          exit 1
          ;;
      esac
      shift 2
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

if [[ -z "${CONFIG_ARG}" ]]; then
  echo "Error: --configure (-c) is required. Valid values are: dicom, fhir, llm." >&2
  usage >&2
  exit 1
fi

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
  exec uv run gosmart-graphite configure "${CONFIG_ARG}" --file "${FILE_ARG}" "${EXTRA_ARGS[@]}"
else
  exec uv run gosmart-graphite configure "${CONFIG_ARG}" --file "${FILE_ARG}"
fi
