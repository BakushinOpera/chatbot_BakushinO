# 把 pj1 的 Python 后端打成独立的 phal-server.exe（PyInstaller，onedir），输出到 pj2\build\sidecar\
# 用法（在 pj2 里）：npm run build:sidecar
$ErrorActionPreference = 'Stop'
$pj2 = Split-Path -Parent $PSScriptRoot
$pj1 = Join-Path (Split-Path -Parent $pj2) 'pj1'
$py  = Join-Path $pj1 '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "找不到 $py（pj1 的虚拟环境）" }

Push-Location $pj1
try {
    & $py -m pip install pyinstaller | Out-Null
    $out = Join-Path $pj2 'build\sidecar'
    if (Test-Path $out) { Remove-Item -Recurse -Force $out }
    & $py -m PyInstaller --noconfirm --onedir --console --name phal-server `
        --distpath $out --workpath (Join-Path $pj1 '.tmp\pyi') --specpath (Join-Path $pj1 '.tmp') `
        --add-data "$pj1\web;web" --add-data "$pj1\app\default_persona.md;app" `
        --collect-submodules uvicorn --collect-submodules websockets --hidden-import h11 --hidden-import httptools --hidden-import websockets `
        run.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }
} finally {
    Pop-Location
}
Write-Host "后端已打好：$out\phal-server\phal-server.exe"
