#!/usr/bin/env pwsh
# A script to start the ollama server, keep the model in memory
# Allow (remind) the use of Embedding mode
# $ModelName = "gemma4:e2b" # <-- CHANGE THIS to your model name (e.g., gemma2:2b)
# $ModelName = "qwen3-embedding:4b"
# $ModelName = "qwen3.5:0.8b"
# Embedding models like nomic needs  IS_EMBEDDING = $true, otherwise it will
# fail to load the model
$ModelName = "nomic-embed-text:latest" 
$IS_EMBEDDING = $true 

# Ensure environment variables are applied to this session
$Env:OLLAMA_KEEP_ALIVE = "-1"
$Env:OLLAMA_MAX_LOADED_MODELS = "1"
$Env:OLLAMA_HOST = "0.0.0.0:11434"

$BaseUri = "http://127.0.0.1:11434"

# 1. Check if Ollama API is already responding, otherwise start the process
Write-Host "Checking if Ollama is running..." -ForegroundColor Cyan

$ServerUp = $false
try {
    $null = Invoke-RestMethod -Uri "$BaseUri/api/tags" -Method Get -TimeoutSec 2 -ErrorAction Stop
    $ServerUp = $true
    Write-Host "Ollama server is already running and reachable." -ForegroundColor Green
} catch {
    # If not responding, check if process exists under either 'ollama' or 'ollama app'
    if (-not (Get-Process -Name "ollama*", "ollama app*" -ErrorAction SilentlyContinue)) {
        Write-Host "Starting Ollama server in background..." -ForegroundColor Yellow
        $startParams = @{
            FilePath     = "ollama"
            ArgumentList = "serve"
        }
        if ($IsWindows) {
            $startParams.WindowStyle = "Hidden"
        } else {
            # -WindowStyle is not supported on macOS/Linux; redirect output to keep background logs out of the terminal
            $logDir = [System.IO.Path]::GetTempPath()
            $startParams.RedirectStandardOutput = Join-Path $logDir "ollama-serve.out.log"
            $startParams.RedirectStandardError  = Join-Path $logDir "ollama-serve.err.log"
        }
        Start-Process @startParams
    } else {
        Write-Host "Ollama process found, waiting for API port to become ready..." -ForegroundColor Yellow
    }
}

# 2. Wait for the Ollama server API to become ready
if (-not $ServerUp) {
    $MaxRetries = 30
    $RetryCount = 0
    Write-Host "Waiting for Ollama API to be ready..." -NoNewline

    while (-not $ServerUp -and $RetryCount -lt $MaxRetries) {
        try {
            $null = Invoke-RestMethod -Uri "$BaseUri/api/tags" -Method Get -TimeoutSec 2 -ErrorAction Stop
            $ServerUp = $true
            Write-Host " Ready!" -ForegroundColor Green
        } catch {
            Write-Host "." -NoNewline
            Start-Sleep -Seconds 1
            $RetryCount++
        }
    }

    if (-not $ServerUp) {
        Write-Error "Ollama server failed to respond at $BaseUri after $MaxRetries attempts."
        exit 1
    }
}

# 3. Check if the specified model is installed
try {
    $installed = (Invoke-RestMethod -Uri "$BaseUri/api/tags" -Method Get -TimeoutSec 5).models.name
    if ($installed -notcontains $ModelName -and $installed -notcontains "${ModelName}:latest") {
        Write-Warning "Model '$ModelName' was not found in installed models."
        Write-Host "Installed models: $(($installed -join ', '))" -ForegroundColor Yellow
        Write-Host "If this model is not installed, run: ollama pull $ModelName" -ForegroundColor Yellow
    }
} catch {
    # Non-fatal check; continue to preload attempt
}

# 4. Preload the model into memory and keep it alive forever (-1)
Write-Host "Preloading '$ModelName' into memory (keep_alive = -1)..." -ForegroundColor Cyan

if ($IS_EMBEDDING) {
    # For embedding models, use /api/embed (or /api/embeddings) to preload/test
    $Body = @{
        model      = $ModelName
        keep_alive = -1
        input      = "test"
    } | ConvertTo-Json

    try {
        $response = Invoke-RestMethod -Uri "$BaseUri/api/embed" -Method Post -Body $Body -ContentType "application/json" -TimeoutSec 120
        Write-Host "Successfully preloaded embedding model '$ModelName' into memory!" -ForegroundColor Green
    } catch {
        Write-Error "Failed to preload embedding model '$ModelName': $_"
    }
} else {
    $Body = @{
        model      = $ModelName
        keep_alive = -1
        stream     = $false   # Crucial: prevents Invoke-RestMethod from hanging on chunked streaming
    } | ConvertTo-Json

    try {
        $response = Invoke-RestMethod -Uri "$BaseUri/api/generate" -Method Post -Body $Body -ContentType "application/json" -TimeoutSec 120
        Write-Host "Successfully preloaded '$ModelName' into memory!" -ForegroundColor Green
    } catch {
        Write-Error "Failed to preload model '$ModelName': $_"
    }
}

