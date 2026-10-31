# 免安装版：把 dist\win-unpacked 整个复制到一个固定的文件夹，试启动一次确认能打开，再把桌面快捷方式指过去。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy-portable.ps1 [-Target <文件夹>]（默认是仓库根下的 Phal）
#
# 为什么不直接用 dist\win-unpacked：每次打包都会重写里面的 exe，刚打包出来的 exe 偶尔一启动就退出（返回码 3，
# 字节完全一样的副本却能正常运行），而且应用开着的时候没法打包。复制出去的是新文件，用的时候也不会被打包动到。
# 数据目录 / 素材目录不跟着 exe 走（见 main.js 的 resolveDataDir：%APPDATA%\AI 聊天\config.json），换位置不影响数据。
param(
  [string]$Target = (Join-Path $PSScriptRoot "..\..\Phal"),
  [string]$Source = (Join-Path $PSScriptRoot "..\dist\win-unpacked")
)
$ErrorActionPreference = "Stop"
$Source = (Resolve-Path $Source).Path
$Target = [IO.Path]::GetFullPath($Target)
$exeName = (Get-ChildItem $Source -Filter *.exe | Select-Object -First 1).Name
if (-not $exeName) { throw "no exe in $Source (build it first: npm run build:sidecar; npx electron-builder --win nsis)" }
# 后端没打好（build:sidecar 失败）时，打出来的包里没有 phal-server.exe：不部署，免得把能用的免安装版换成打不开的
if (-not (Test-Path (Join-Path $Source "resources\sidecar\phal-server\phal-server.exe"))) { throw "no sidecar in $Source (npm run build:sidecar failed?) - not deploying" }
$exe = Join-Path $Target $exeName

function Stop-Under([string]$dir) {
  $d = $dir.TrimEnd("\") + "\"
  Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
}

# 试启动：用临时数据目录 + 离线模式，等后端报健康就关掉（不碰真正的数据）
function Test-Launch {
  $probe = Join-Path $env:TEMP "phal-portable-probe"
  New-Item -ItemType Directory -Force $probe | Out-Null
  Remove-Item (Join-Path $probe "server.json") -Force -ErrorAction SilentlyContinue
  $env:PHAL_DATA_DIR = $probe; $env:CHAT_OFFLINE = "1"
  try {
    $p = Start-Process -FilePath $exe -WorkingDirectory $Target -PassThru
    for ($t = 0; $t -lt 60; $t++) {
      Start-Sleep -Milliseconds 750
      if ($p.HasExited) { return "exited code=$($p.ExitCode)" }
      $sj = Join-Path $probe "server.json"
      if (Test-Path $sj) {
        try {
          $url = (Get-Content -Raw $sj | ConvertFrom-Json).url
          if ((Invoke-RestMethod "$url/api/health" -TimeoutSec 2).ok) { return "ok" }
        } catch {}
      }
    }
    return "timeout"
  } finally {
    Remove-Item Env:\PHAL_DATA_DIR, Env:\CHAT_OFFLINE -ErrorAction SilentlyContinue
    Stop-Under $Target
    Start-Sleep -Seconds 2
  }
}

"source = $Source"
"target = $Target"
Stop-Under $Target                       # 正在用的免安装版要先关掉，不然文件被占着
for ($w = 0; $w -lt 30; $w++) {          # 等进程真的退干净（后端 / GPU 子进程会晚一点），最多 15 秒
  $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Target.TrimEnd("\") + "\", [StringComparison]::OrdinalIgnoreCase) })
  if (-not $left) { break }
  Stop-Under $Target
  Start-Sleep -Milliseconds 500
}
New-Item -ItemType Directory -Force $Target | Out-Null
robocopy $Source $Target /MIR /NFL /NDL /NJH /NJS /NP /R:10 /W:2 | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

$result = ""
for ($try = 1; $try -le 3; $try++) {
  $result = Test-Launch
  "launch test $try : $result"
  if ($result -eq "ok") { break }
  # 起不来：把 exe 换成一份新复制的（同样的字节，新文件），再试
  $bak = "$exe.bak"
  Move-Item $exe $bak -Force
  Copy-Item $bak $exe -Force
  Remove-Item $bak -Force
}
if ($result -ne "ok") { throw "portable app failed to start: $result" }

# 桌面快捷方式：已有的（指向旧位置或这里的同名 exe）就改，没有就新建
$desktop = [Environment]::GetFolderPath("Desktop")
$sh = New-Object -ComObject WScript.Shell
$lnkPath = $null
foreach ($f in Get-ChildItem $desktop -Filter *.lnk) {
  $l = $sh.CreateShortcut($f.FullName)
  if ($l.TargetPath -and ([IO.Path]::GetFileName($l.TargetPath) -eq $exeName)) { $lnkPath = $f.FullName; break }
}
if (-not $lnkPath) { $lnkPath = Join-Path $desktop ([IO.Path]::GetFileNameWithoutExtension($exeName) + ".lnk") }
$lnk = $sh.CreateShortcut($lnkPath)
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $Target
$lnk.IconLocation = "$exe,0"
$lnk.Save()
"shortcut = $lnkPath -> $exe"
"done"
