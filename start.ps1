$env:GOSMART_GS_PORT = "8086"

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

Remove-Item Env:\VIRTUAL_ENV -ErrorAction SilentlyContinue

uv run python -m gosmart_graphite.main
