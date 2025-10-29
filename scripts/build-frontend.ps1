#!/usr/bin/env pwsh
param()

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir '..')
$frontendDir = Resolve-Path (Join-Path $repoRoot 'src' 'frontend')

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required to build the frontend. Install Node.js (>= 18)."
}

Push-Location $frontendDir
try {
    npm install
    npm run build
}
finally {
    Pop-Location
}
