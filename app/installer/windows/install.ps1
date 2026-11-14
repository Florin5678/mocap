<#
mocapapp installer for Windows (PROJECT.md D11, section 9 Packaging).

Bootstraps a private, pinned environment - nothing is installed system-wide and no admin rights are needed:
  1. uv (pinned version, SHA-256 checked)            -> <Prefix>\uv
  2. the app source (a tagged release from GitHub)    -> <Prefix>\src
  3. Python 3.12 + locked dependencies (uv.lock)      -> <Prefix>\python, <Prefix>\venv
  4. the pose-estimation models (once; then offline)  -> %USERPROFILE%\.cache\rtmlib
  5. Start-menu and desktop shortcuts
Re-running it updates/repairs the installation. Session folders are never touched.
#>
param(
    [string]$Ref = "mocapapp-v0.1.0rc8",           # git tag (or branch) of https://github.com/Florin5678/mocap
    [string]$Source = "",                          # use a local checkout instead of downloading (testing)
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA "mocapapp"),
    [switch]$NoModels,
    [switch]$NoShortcuts,
    [switch]$Pause                                 # keep the window open at the end (used by the setup .exe)
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"            # Invoke-WebRequest is very slow with its progress bar
# Started from PowerShell 7 (pwsh), this Windows PowerShell inherits pwsh's module path and then cannot
# load its own cmdlets (Get-FileHash, Expand-Archive, ...). Use Windows PowerShell's own module path.
$env:PSModulePath = (@((Join-Path ([Environment]::GetFolderPath("MyDocuments")) "WindowsPowerShell\Modules"),
    [Environment]::GetEnvironmentVariable("PSModulePath", "Machine")) | Where-Object { $_ }) -join ";"
$UvVersion = "0.13.4"
$UvSha256 = "ccec7508a07d9933d7cb1261880633efcf1f30d6e6eddab21823f1dae6f23225"
$Repo = "https://github.com/Florin5678/mocap"
# Pose2Sim takes its version from git (setuptools-scm); a source zip has no .git folder, so it is
# given here: the upstream release this fork is based on, marked as the mocapapp fork.
$Pose2SimVersion = "0.10.48+mocapapp"
$Steps = 6
$script:Step = 0

# .NET instead of Expand-Archive and Get-FileHash: those come from script modules that fail to load when
# this script is started from PowerShell 7 (see PSModulePath above).
Add-Type -AssemblyName System.IO.Compression.FileSystem
function Expand-Zip([string]$Zip, [string]$Dest) {
    if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Dest)
}

function Step([string]$Text) {
    $script:Step++
    Write-Host ""
    Write-Host "[$script:Step/$Steps] $Text" -ForegroundColor Cyan
}

function Finish([int]$Code) {
    try { Stop-Transcript | Out-Null } catch {}
    if ($Pause) { Read-Host "Press Enter to close this window" | Out-Null }
    exit $Code
}

New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
try { Start-Transcript -Path (Join-Path $Prefix "install.log") -Force | Out-Null } catch {}
Write-Host "Installing mocapapp ($Ref) into $Prefix" -ForegroundColor Green
Write-Host "This needs an internet connection and takes about 10-30 minutes. Research use only."

try {
    # 1. uv ---------------------------------------------------------------------------------------
    Step "Getting the installer tool (uv $UvVersion) ..."
    $uvDir = Join-Path $Prefix "uv"
    $uv = Join-Path $uvDir "uv.exe"
    $haveUv = (Test-Path $uv) -and ((& $uv --version) -match [regex]::Escape($UvVersion))
    if (-not $haveUv) {
        $zip = Join-Path $env:TEMP "mocapapp-uv.zip"
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $stream = [System.IO.File]::OpenRead($zip)
        try { $hash = (($sha.ComputeHash($stream) | ForEach-Object { $_.ToString("x2") }) -join "") } finally { $stream.Dispose() }
        if ($hash -ne $UvSha256) { throw "The downloaded uv does not match its checksum (got $hash). Try again later." }
        if (Test-Path $uvDir) { Remove-Item -Recurse -Force $uvDir }
        Expand-Zip $zip $uvDir
        Remove-Item $zip -Force
        $found = Get-ChildItem -Path $uvDir -Recurse -Filter "uv.exe" | Select-Object -First 1
        if ($found.FullName -ne $uv) { Move-Item -Force $found.FullName $uv }
    }

    # 2. source -----------------------------------------------------------------------------------
    Step "Getting the app ($Ref) ..."
    $src = Join-Path $Prefix "src"
    if ($Source) {
        $app = Join-Path (Resolve-Path $Source) "app"
        if (-not (Test-Path (Join-Path $app "pyproject.toml"))) { throw "$Source is not a checkout of the mocap repository." }
    } else {
        $zip = Join-Path $env:TEMP "mocapapp-src.zip"
        $kind = if ($Ref -match "^mocapapp-v") { "tags" } else { "heads" }
        Invoke-WebRequest -UseBasicParsing -Uri "$Repo/archive/refs/$kind/$Ref.zip" -OutFile $zip
        $new = Join-Path $Prefix "src-new"
        if (Test-Path $new) { Remove-Item -Recurse -Force $new }
        Expand-Zip $zip $new
        Remove-Item $zip -Force
        $top = Get-ChildItem -Path $new -Directory | Select-Object -First 1
        if (Test-Path $src) { Remove-Item -Recurse -Force $src }
        Move-Item $top.FullName $src
        Remove-Item -Recurse -Force $new
        $app = Join-Path $src "app"
    }

    # 3. Python + dependencies --------------------------------------------------------------------
    Step "Installing Python and the app's components (the longest step) ..."
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Prefix "python"
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $Prefix "venv"
    if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $Prefix "cache" }
    if (-not (Test-Path (Join-Path $app "..\.git"))) { $env:SETUPTOOLS_SCM_PRETEND_VERSION_FOR_POSE2SIM = $Pose2SimVersion }
    & $uv sync --frozen --no-dev --python-preference only-managed --project $app
    if ($LASTEXITCODE -ne 0) { throw "Installing the components failed (see the messages above)." }
    $exe = Join-Path $Prefix "venv\Scripts\mocapapp.exe"
    $gui = Join-Path $Prefix "venv\Scripts\mocapapp-gui.exe"
    $ErrorActionPreference = "Continue"            # tidying the download cache is optional
    & $uv cache prune *> $null
    $ErrorActionPreference = "Stop"

    # 4. models -----------------------------------------------------------------------------------
    Step "Downloading the pose-estimation models (about 145 MB, once) ..."
    if ($NoModels) { Write-Host "Skipped (-NoModels); the app offers the download at first start." }
    else {
        & $exe models --download
        if ($LASTEXITCODE -ne 0) { Write-Host "The models could not be downloaded now; the app will offer it again at first start." -ForegroundColor Yellow }
    }

    # 5. check ------------------------------------------------------------------------------------
    Step "Checking the installation ..."
    & $exe doctor --dir $env:USERPROFILE
    $doctorOk = ($LASTEXITCODE -eq 0)

    # 6. shortcuts --------------------------------------------------------------------------------
    Step "Creating shortcuts ..."
    if (-not $NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $places = @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))
        foreach ($dir in $places) {
            $lnk = $shell.CreateShortcut((Join-Path $dir "mocapapp.lnk"))
            $lnk.TargetPath = $gui
            $lnk.WorkingDirectory = $env:USERPROFILE
            $lnk.Description = "Markerless motion capture (research use only)"
            $lnk.Save()
        }
    }
    $version = (& $exe --version)
    @{ ref = $Ref; version = "$version"; installed = (Get-Date -Format s); prefix = $Prefix } |
        ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Prefix "install.json")

    Write-Host ""
    $where = if ($NoShortcuts) { "with $gui" } else { "from the Start menu or the desktop: mocapapp" }
    if ($doctorOk) { Write-Host "Done: $version is installed. Start it $where." -ForegroundColor Green }
    else { Write-Host "Installed, but the check above found problems; the app explains them when it starts." -ForegroundColor Yellow }
    Finish 0
}
catch {
    Write-Host ""
    Write-Host "Installation stopped at step $script:Step : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "What to do: check the internet connection and free disk space (about 10 GB), then run the installer again;"
    Write-Host "finished steps are reused. The full log is $(Join-Path $Prefix 'install.log')."
    Finish 1
}
