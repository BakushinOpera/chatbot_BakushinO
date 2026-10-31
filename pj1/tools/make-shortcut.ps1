#Requires -Version 5.1
<#
    生成「AI 聊天」快捷方式（图标用的是 assets\whale-girl.ico（DeepSeek 鲸鱼娘））

    用法：
      powershell -ExecutionPolicy Bypass -File tools\make-shortcut.ps1            # 桌面 + 项目文件夹
      powershell -ExecutionPolicy Bypass -File tools\make-shortcut.ps1 -Where Desktop
      powershell -ExecutionPolicy Bypass -File tools\make-shortcut.ps1 -Where Here

    项目被移动过之后，重新跑一次这个脚本，快捷方式就会指向新位置。
#>
[CmdletBinding()]
param(
    [ValidateSet('Desktop', 'Here', 'Both')]
    [string]$Where = 'Both',
    [string]$Name = 'AI 聊天'
)

$ErrorActionPreference = 'Stop'

$Root     = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $Root 'launch.ps1'
$Icon     = Join-Path $Root 'assets\whale-girl.ico'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $Launcher)) { throw "找不到 launch.ps1：$Launcher" }
if (-not (Test-Path -LiteralPath $PowerShellExe)) { $PowerShellExe = 'powershell.exe' }

$destinations = @()
if ($Where -eq 'Desktop' -or $Where -eq 'Both') {
    $destinations += (Join-Path ([Environment]::GetFolderPath('Desktop')) "$Name.lnk")
}
if ($Where -eq 'Here' -or $Where -eq 'Both') {
    $destinations += (Join-Path $Root "$Name.lnk")
}

$shell = New-Object -ComObject WScript.Shell
foreach ($path in $destinations) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath       = $PowerShellExe
    $lnk.Arguments        = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
    $lnk.WorkingDirectory = $Root
    $lnk.Description      = '启动本地 AI 聊天（已在运行则直接打开浏览器）'
    if (Test-Path -LiteralPath $Icon) { $lnk.IconLocation = "$Icon,0" }
    $lnk.Save()
    Write-Host "已生成：$path"
}

[void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
