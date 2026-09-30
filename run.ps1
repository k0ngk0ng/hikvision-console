$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force .cache, .tmp | Out-Null
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".cache/uv"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot ".cache/python"
$env:TEMP = Join-Path $PSScriptRoot ".tmp"
$env:TMP = $env:TEMP
if (-not (Test-Path ".venv/Scripts/python.exe")) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --python ">=3.11" .venv
        uv pip install --python .venv/Scripts/python.exe -e .
    } else {
        py -3 -m venv .venv
        & .venv/Scripts/python.exe -m pip install --cache-dir "$PSScriptRoot/.cache/pip" -e .
    }
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
}
& .venv/Scripts/python.exe -m hikvision_console @args
exit $LASTEXITCODE
