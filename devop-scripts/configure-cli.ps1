#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Registers or overwrites a configuration node and assigns the node to a specified group.
.DESCRIPTION
    Wraps 'uv run gosmart-graphite configure {dicom-config|fhir-config|llm-config}'.
.PARAMETER Configure
    The configuration target: dicom, fhir, or llm.
.PARAMETER File
    Path to a single configuration JSON object.
.PARAMETER Assign
    Target Group ID, name, or code (default: 'GoSmart Health').
.EXAMPLE
    .\configure-cli.ps1 -c llm -f devop-scripts/llm_config.template.json
.EXAMPLE
    .\configure-cli.ps1 -c dicom -f devop-scripts/dicom_config.template.json
.EXAMPLE
    .\configure-cli.ps1 -c fhir -f ./devop-scripts/examples/fhir_config.json -a "GoSmart Health"
#>

[CmdletBinding(PositionalBinding = $false)]
param(
    [Alias("c")]
    [Parameter(Mandatory = $false)]
    [ValidateSet("dicom", "fhir", "llm")]
    [string]$Configure,

    [Alias("f")]
    [Parameter(Mandatory = $false)]
    [string]$File,

    [Alias("a")]
    [Parameter(Mandatory = $false)]
    [string]$Assign,

    [Alias("h")]
    [Parameter(Mandatory = $false)]
    [switch]$Help,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$ProjectRoot = Split-Path -Parent $ScriptDir

function Show-Usage {
    $scriptName = "configure-cli.ps1"
    @"
Usage: .\$scriptName -c|--configure {dicom|fhir|llm} [-f|--file <config.json>] [-a|--assign <group_name_or_id>] [additional CLI options]

Registers or overwrites a configuration node and assigns the node
to a specified group.

Options:
  -c, -Configure {dicom | fhir | llm}  The configuration target (required)
  -f, -File <path>                     Path to a single configuration JSON object (default depends on target)
  -a, -Assign <group>                  Target Group ID, name, or code (default: 'GoSmart Health')
  -h, -Help                            Show this help message

Examples:
  .\$scriptName -c llm -f devop-scripts/llm_config.template.json
  .\$scriptName -c dicom -f devop-scripts/dicom_config.template.json
  .\$scriptName -c fhir -f ./devop-scripts/examples/fhir_config.json -a "GoSmart Health"
"@
}

if ($Help) {
    Show-Usage
    exit 0
}

if (-not $Configure) {
    Write-Error "Error: -Configure (-c) is required. Valid values are: dicom, fhir, llm."
    Show-Usage
    exit 1
}

switch ($Configure) {
    "dicom" {
        $ConfigCmd = "dicom-config"
        $DefaultFile = Join-Path $ScriptDir "dicom_config.template.json"
    }
    "fhir" {
        $ConfigCmd = "fhir-config"
        $DefaultFile = Join-Path $ScriptDir "fhir_config.template.json"
    }
    "llm" {
        $ConfigCmd = "llm-config"
        $DefaultFile = Join-Path $ScriptDir "llm_config.template.json"
    }
}

$ResolvedFile = if ($File) {
    if ([System.IO.Path]::IsPathRooted($File)) {
        $File
    } else {
        $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($File)
    }
} else {
    $DefaultFile
}

$cliArgs = @("run", "gosmart-graphite", "configure", $ConfigCmd, "--file", $ResolvedFile)

if ($Assign) {
    $cliArgs += @("--assign", $Assign)
}

if ($ExtraArgs) {
    $cliArgs += $ExtraArgs
}

Push-Location $ProjectRoot
try {
    & uv @cliArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
