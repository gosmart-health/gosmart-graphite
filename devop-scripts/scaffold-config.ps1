#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Alias entrypoint for scaffold_configs.ps1.
#>

[CmdletBinding()]
param(
    [string]$Group = "GoSmart Health"
)

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

& (Join-Path $ScriptDir "scaffold_configs.ps1") -Group $Group
