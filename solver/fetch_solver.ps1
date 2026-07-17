# Installs the TexasSolver console solver (the Phase-2 GTO anchor) into
# solver/texassolver/ from the official v0.2.0 release binaries.
# Run:  powershell -ExecutionPolicy Bypass -File solver/fetch_solver.ps1
$ErrorActionPreference = "Stop"

$dest = Join-Path $PSScriptRoot "texassolver"
$url = "https://github.com/bupticybee/TexasSolver/releases/download/v0.2.0/TexasSolver-v0.2.0-Windows.zip"
$zip = Join-Path $env:TEMP "TexasSolver-v0.2.0-Windows.zip"
$extract = Join-Path $env:TEMP "texassolver-extract"

Write-Host "Downloading TexasSolver v0.2.0 (39 MB) from GitHub releases..."
Invoke-WebRequest -Uri $url -OutFile $zip
Expand-Archive $zip -DestinationPath $extract -Force

$src = Join-Path $extract "TexasSolver-v0.2.0-Windows"
New-Item -ItemType Directory -Force (Join-Path $dest "resources\compairer") | Out-Null
Copy-Item (Join-Path $src "console_solver.exe") $dest -Force
Copy-Item (Join-Path $src "resources\compairer\card5_dic_sorted.txt") (Join-Path $dest "resources\compairer") -Force

Remove-Item $zip -Force
Remove-Item $extract -Recurse -Force
Write-Host "console_solver.exe installed to $dest"
