# 给朋友的免安装版：把 dist\win-unpacked 打成一个 zip，放到仓库根的 release\（不进 git，发布时传到 GitHub Releases）。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/make-release.ps1
#
# 先跑 npm run build:sidecar && npx electron-builder --win nsis。包里只有程序和房间素材，不带任何人的数据（数据在 %APPDATA%\AI 聊天\data）。
param(
  [string]$Source = (Join-Path $PSScriptRoot "..\dist\win-unpacked"),
  [string]$OutDir = (Join-Path $PSScriptRoot "..\..\release")
)
$ErrorActionPreference = "Stop"
$Source = (Resolve-Path $Source).Path
$OutDir = [IO.Path]::GetFullPath($OutDir)
if (-not (Test-Path (Join-Path $Source "resources\sidecar\phal-server\phal-server.exe"))) { throw "no sidecar in $Source - build first" }
foreach ($bad in @(".env", "chat.db", "config.json")) {
  $hit = Get-ChildItem $Source -Recurse -Force -Filter $bad -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($hit) { throw "found $bad in the package ($($hit.FullName)) - not releasing" }
}
New-Item -ItemType Directory -Force $OutDir | Out-Null
$zip = Join-Path $OutDir ("AI聊天-portable-" + (Get-Date -Format "yyyyMMdd") + ".zip")
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $Source "*") -DestinationPath $zip -CompressionLevel Optimal
"release = $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
