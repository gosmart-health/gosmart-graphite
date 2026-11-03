#!/bin/bash
export GOSMART_GS_PORT=8086
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi
unset VIRTUAL_ENV
uv run python main.py
