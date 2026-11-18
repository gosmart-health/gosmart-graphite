#!/usr/bin/env bash
# Start the Ollama server in the background on macOS/Linux, restart it if already
# running, keep at most one model loaded in memory indefinitely, and preload the
# configured model (default: nomic-embed-text:latest in embedding mode).

set -euo pipefail

# Default configuration (override via env vars or CLI flags)
# Embedding models like nomic-embed-text need IS_EMBEDDING=true (/api/embed),
# otherwise Ollama will fail to load the model via /api/generate.
MODEL_NAME="${MODEL_NAME:-embeddinggemma-2:270m}"
IS_EMBEDDING="${IS_EMBEDDING:-true}"
OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:--1}"
OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
OLLAMA_HOST="${OLLAMA_HOST:-0.0.0.0:11434}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Stops any running Ollama instance, starts 'ollama serve' in the background with
OLLAMA_KEEP_ALIVE=-1 and OLLAMA_MAX_LOADED_MODELS=1, and preloads a single
model into memory.

Options:
  -m, --model <name>   Model name to preload (default: ${MODEL_NAME})
  -e, --embedding      Preload using /api/embed (embedding mode, default: ${IS_EMBEDDING})
      --no-embedding   Preload using /api/generate (chat/completion models)
      --host <addr>    Bind address for OLLAMA_HOST (default: ${OLLAMA_HOST})
  -h, --help           Show this help message

Environment overrides:
  MODEL_NAME, IS_EMBEDDING (true|false), OLLAMA_HOST, OLLAMA_KEEP_ALIVE,
  OLLAMA_MAX_LOADED_MODELS
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -m|--model)
      if [[ $# -lt 2 ]]; then
        echo "Error: $1 requires a model name argument." >&2
        exit 1
      fi
      MODEL_NAME="$2"
      shift 2
      ;;
    -e|--embedding)
      IS_EMBEDDING="true"
      shift
      ;;
    --no-embedding)
      IS_EMBEDDING="false"
      shift
      ;;
    --host)
      if [[ $# -lt 2 ]]; then
        echo "Error: $1 requires a host:port argument." >&2
        exit 1
      fi
      OLLAMA_HOST="$2"
      shift 2
      ;;
    *)
      echo "Error: Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "${IS_EMBEDDING}" in
  true|TRUE|1|yes|YES) IS_EMBEDDING="true" ;;
  false|FALSE|0|no|NO) IS_EMBEDDING="false" ;;
  *)
    echo "Error: IS_EMBEDDING must be 'true' or 'false' (got '${IS_EMBEDDING}')." >&2
    exit 1
    ;;
esac

if ! command -v ollama >/dev/null 2>&1; then
  echo "Error: 'ollama' binary not found in PATH." >&2
  exit 1
fi
OLLAMA_BIN="$(command -v ollama)"

if ! command -v curl >/dev/null 2>&1; then
  echo "Error: 'curl' is required but not found in PATH." >&2
  exit 1
fi

# Derive local client URI from OLLAMA_HOST (strip optional scheme, replace 0.0.0.0/:: with 127.0.0.1)
CLEAN_HOST="${OLLAMA_HOST#http://}"
CLEAN_HOST="${CLEAN_HOST#https://}"
if [[ "${CLEAN_HOST}" == *:* ]]; then
  HOST_PART="${CLEAN_HOST%:*}"
  PORT_PART="${CLEAN_HOST##*:}"
else
  HOST_PART="${CLEAN_HOST}"
  PORT_PART="11434"
fi
if [[ -z "${HOST_PART}" || "${HOST_PART}" == "0.0.0.0" || "${HOST_PART}" == "::" || "${HOST_PART}" == "[::]" ]]; then
  CLIENT_HOST="127.0.0.1"
else
  CLIENT_HOST="${HOST_PART}"
fi
BASE_URI="http://${CLIENT_HOST}:${PORT_PART}"

LOG_DIR="${TMPDIR:-/tmp}"
LOG_DIR="${LOG_DIR%/}"
OUT_LOG="${LOG_DIR}/ollama-serve.out.log"
ERR_LOG="${LOG_DIR}/ollama-serve.err.log"
PID_FILE="${LOG_DIR}/ollama-serve.pid"

find_ollama_pids() {
  ps -axo pid=,comm=,args= 2>/dev/null | awk -v self_pid="$$" -v parent_pid="$PPID" '
    {
      pid = $1
      if (pid == self_pid || pid == parent_pid) next
      if ($0 ~ /ollama-start/) next
      comm = $2
      if (comm ~ /(^|\/)Ollama$/ || $0 ~ /(^|[[:space:]\/])ollama[[:space:]]+serve([[:space:]]|$)/ || $0 ~ /lib\/ollama\/llama-server/) {
        print pid
      }
    }
  '
}

api_is_up() {
  curl -sf --max-time 2 "${BASE_URI}/api/tags" >/dev/null 2>&1
}

stop_existing_ollama() {
  local pids
  local brew_status=""
  local need_stop="false"

  if api_is_up; then
    need_stop="true"
  fi

  if command -v brew >/dev/null 2>&1; then
    brew_status="$(brew services list 2>/dev/null | awk '$1 == "ollama" {print $2}' || true)"
    if [[ "${brew_status}" == "started" ]]; then
      need_stop="true"
    fi
  fi

  pids="$(find_ollama_pids || true)"
  if [[ -n "${pids}" ]]; then
    need_stop="true"
  fi

  if [[ "${need_stop}" != "true" ]]; then
    echo "No running Ollama server found."
    return 0
  fi

  echo "Existing Ollama process/service detected. Stopping it for a clean restart..."

  if [[ "${brew_status}" == "started" ]]; then
    echo "Stopping Homebrew 'ollama' service..."
    brew services stop ollama >/dev/null 2>&1 || true
  fi

  # Quit macOS Ollama desktop app cleanly if running (otherwise it respawns ollama serve)
  if pgrep -x "Ollama" >/dev/null 2>&1 && command -v osascript >/dev/null 2>&1; then
    osascript -e 'tell application "Ollama" to quit' >/dev/null 2>&1 || true
    sleep 1
  fi

  pids="$(find_ollama_pids || true)"
  if [[ -n "${pids}" ]]; then
    # shellcheck disable=SC2086
    kill -TERM ${pids} 2>/dev/null || true
  fi

  local waited=0
  while [[ ${waited} -lt 20 ]]; do
    pids="$(find_ollama_pids || true)"
    if [[ -z "${pids}" ]] && ! api_is_up; then
      break
    fi
    sleep 0.5
    waited=$((waited + 1))
  done

  pids="$(find_ollama_pids || true)"
  if [[ -n "${pids}" ]]; then
    echo "Force-killing remaining Ollama processes: ${pids//$'\n'/ }"
    # shellcheck disable=SC2086
    kill -KILL ${pids} 2>/dev/null || true
    sleep 1
  fi

  rm -f "${PID_FILE}"
  echo "Existing Ollama stopped."
}

escape_json() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# 1. Check if Ollama is already running; stop it before restarting
echo "Checking if Ollama is already running..."
stop_existing_ollama

# 2. Export environment variables and start 'ollama serve' detached in the background
export OLLAMA_KEEP_ALIVE
export OLLAMA_MAX_LOADED_MODELS
export OLLAMA_HOST

echo "Starting Ollama server in background (OLLAMA_HOST=${OLLAMA_HOST}, OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAX_LOADED_MODELS}, OLLAMA_KEEP_ALIVE=${OLLAMA_KEEP_ALIVE})..."
nohup "${OLLAMA_BIN}" serve </dev/null >"${OUT_LOG}" 2>"${ERR_LOG}" &
OLLAMA_PID=$!
disown "${OLLAMA_PID}" 2>/dev/null || true
echo "${OLLAMA_PID}" >"${PID_FILE}"

# 3. Wait for the Ollama server API to become ready
MAX_RETRIES=30
RETRY_COUNT=0
SERVER_UP="false"
printf "Waiting for Ollama API at %s to be ready" "${BASE_URI}"

while [[ ${RETRY_COUNT} -lt ${MAX_RETRIES} ]]; do
  if ! kill -0 "${OLLAMA_PID}" 2>/dev/null; then
    echo ""
    echo "Error: Ollama server process (PID ${OLLAMA_PID}) exited unexpectedly." >&2
    if [[ -s "${ERR_LOG}" ]]; then
      echo "--- ${ERR_LOG} ---" >&2
      tail -n 20 "${ERR_LOG}" >&2
    fi
    exit 1
  fi

  if api_is_up; then
    SERVER_UP="true"
    echo " Ready! (PID: ${OLLAMA_PID})"
    break
  fi

  printf "."
  sleep 1
  RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [[ "${SERVER_UP}" != "true" ]]; then
  echo ""
  echo "Error: Ollama server failed to respond at ${BASE_URI} after ${MAX_RETRIES} attempts." >&2
  if [[ -s "${ERR_LOG}" ]]; then
    echo "--- ${ERR_LOG} ---" >&2
    tail -n 20 "${ERR_LOG}" >&2
  fi
  exit 1
fi

# 4. Check if the specified model is installed
TAGS_JSON="$(curl -sf --max-time 5 "${BASE_URI}/api/tags" || true)"
if [[ -n "${TAGS_JSON}" ]]; then
  if command -v jq >/dev/null 2>&1; then
    INSTALLED_MODELS="$(printf '%s' "${TAGS_JSON}" | jq -r '.models[]?.name // empty' 2>/dev/null || true)"
  else
    INSTALLED_MODELS="$(printf '%s' "${TAGS_JSON}" | grep -o '"name":"[^"]*"' | sed 's/"name":"//;s/"$//' || true)"
  fi

  MODEL_FOUND="false"
  while IFS= read -r m; do
    [[ -z "${m}" ]] && continue
    if [[ "${m}" == "${MODEL_NAME}" || "${m}" == "${MODEL_NAME}:latest" ]]; then
      MODEL_FOUND="true"
      break
    fi
  done <<< "${INSTALLED_MODELS}"

  if [[ "${MODEL_FOUND}" != "true" ]]; then
    echo "Warning: Model '${MODEL_NAME}' was not found in installed models." >&2
    if [[ -n "${INSTALLED_MODELS}" ]]; then
      echo "Installed models: ${INSTALLED_MODELS//$'\n'/, }" >&2
    fi
    echo "If this model is not installed, run: ollama pull ${MODEL_NAME}" >&2
  fi
fi

# 5. Preload the model into memory and keep it alive forever (-1)
MODEL_JSON="$(escape_json "${MODEL_NAME}")"
echo "Preloading '${MODEL_NAME}' into memory (keep_alive = ${OLLAMA_KEEP_ALIVE}, embedding = ${IS_EMBEDDING})..."

RESP_FILE="$(mktemp)"
cleanup_tmp() {
  rm -f "${RESP_FILE}"
}
trap cleanup_tmp EXIT

if [[ "${IS_EMBEDDING}" == "true" ]]; then
  ENDPOINT="${BASE_URI}/api/embed"
  PAYLOAD="{\"model\":\"${MODEL_JSON}\",\"keep_alive\":-1,\"input\":\"test\"}"
else
  ENDPOINT="${BASE_URI}/api/generate"
  PAYLOAD="{\"model\":\"${MODEL_JSON}\",\"keep_alive\":-1,\"stream\":false}"
fi

HTTP_CODE="$(curl -sS -o "${RESP_FILE}" -w "%{http_code}" \
  --max-time 120 \
  -X POST "${ENDPOINT}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}")"

if [[ "${HTTP_CODE}" -lt 200 || "${HTTP_CODE}" -ge 300 ]]; then
  echo "Error: Failed to preload model '${MODEL_NAME}' via ${ENDPOINT} (HTTP ${HTTP_CODE}):" >&2
  cat "${RESP_FILE}" >&2
  echo "" >&2
  exit 1
fi

echo "Successfully preloaded '${MODEL_NAME}' into memory!"
echo "Logs: ${OUT_LOG} | ${ERR_LOG}"
echo "PID file: ${PID_FILE}"

# Show currently loaded models in memory
OLLAMA_HOST="${CLIENT_HOST}:${PORT_PART}" "${OLLAMA_BIN}" ps || true
