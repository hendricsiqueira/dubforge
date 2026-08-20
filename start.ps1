$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $env:ZAST_TRANSLATE_PATH) {
    $env:ZAST_TRANSLATE_PATH = Join-Path (Split-Path -Parent $ProjectRoot) "ZastTranslate"
}

$Python = Join-Path $env:ZAST_TRANSLATE_PATH ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "ZastTranslate ou o ambiente .venv não foi encontrado em:" -ForegroundColor Red
    Write-Host "  $env:ZAST_TRANSLATE_PATH" -ForegroundColor Yellow
    Write-Host "Defina ZAST_TRANSLATE_PATH para a pasta correta e execute novamente."
    exit 1
}

$VenvScripts = Split-Path -Parent $Python
$env:PATH = "$VenvScripts;$env:PATH"

Set-Location $ProjectRoot
& $Python app.py
