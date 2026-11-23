#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Scaffolds configuration nodes for GoSmart Graphite.
.DESCRIPTION
    Registers initial configuration JSON definitions for FHIR, DICOM, and LLM services.
#>

[CmdletBinding()]
param(
    [string]$Group = "GoSmart Health"
)

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$ConfigureCli = Join-Path $ScriptDir "configure-cli.ps1"
$Root = Join-Path $ScriptDir "examples"

& $ConfigureCli -c fhir -f (Join-Path $Root "fhir_config.json") -a $Group

# & $ConfigureCli -c dicom -f (Join-Path $Root "dicom_mdicom_config.json") -a $Group
# & $ConfigureCli -c dicom -f (Join-Path $Root "dicom_mock_config.json") -a $Group
# & $ConfigureCli -c llm -f (Join-Path $Root "llm_embedding_config.json") -a $Group
# & $ConfigureCli -c llm -f (Join-Path $Root "llm_inference_config.json") -a $Group
