<#
Removes what install.ps1 created: the private Python, environment, app source and shortcuts.
Session folders (your data) are never touched. The pose models in %USERPROFILE%\.cache\rtmlib are kept
unless -Models is given (other Pose2Sim/rtmlib installations may share them).
#>
param(
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA "mocapapp"),
    [switch]$Models
)
$ErrorActionPreference = "Continue"
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dir "mocapapp.lnk")
}
foreach ($name in @("venv", "python", "uv", "src", "src-new", "cache", "install.json", "install.log")) {
    $path = Join-Path $Prefix $name
    if (Test-Path $path) { Remove-Item -Recurse -Force $path }
}
# the folder itself only if nothing else is in it
if ((Test-Path $Prefix) -and -not (Get-ChildItem -Force $Prefix)) { Remove-Item -Force $Prefix }
if ($Models) {
    $cache = Join-Path $env:USERPROFILE ".cache\rtmlib"
    if (Test-Path $cache) { Remove-Item -Recurse -Force $cache }
}
Write-Host "mocapapp was removed. Your session folders were not touched."
