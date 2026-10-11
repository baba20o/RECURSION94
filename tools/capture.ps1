[CmdletBinding()]
param(
    [string]$Scene = "Boot",
    [switch]$Audio,
    [ValidateRange(1, 60)]
    [int]$StartupTimeoutSeconds = 45,
    [ValidateRange(2, 30)]
    [int]$AudioSeconds = 4,
    [string]$ExpectedSha256 = "",
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PowerShellExe = (Get-Process -Id $PID).Path
$BuildScript = Join-Path $PSScriptRoot "build.ps1"
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$AudioCaptureTool = Join-Path $PSScriptRoot "m2_audio_capture.py"
$ScreenshotCaptureTool = Join-Path $PSScriptRoot "m2_screenshot_capture.py"
$Emulator = Join-Path $ProjectRoot ".toolcache\blastem\blastem.exe"
$Rom = Join-Path $ProjectRoot "build\recursion94-debug.bin"
$Output = Join-Path $ProjectRoot "artifacts\screenshots\m1-diagnostic-window-post-fix.png"

if (-not $Audio -and $Scene -ne "Boot") {
    throw "Only the Milestone 1 Boot capture state exists; '$Scene' is not silently substituted."
}
if (-not $NoBuild) {
    $BuildArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $BuildScript,
        "-Configuration", "Debug"
    )
    $BuildProcess = Start-Process -FilePath $PowerShellExe -ArgumentList $BuildArguments -NoNewWindow -Wait -PassThru
    if ($BuildProcess.ExitCode -ne 0) {
        throw "Debug build failed before capture."
    }
}
if (-not (Test-Path -LiteralPath $Rom -PathType Leaf)) {
    throw "Debug ROM is missing: $Rom"
}
if (-not (Test-Path -LiteralPath $Emulator -PathType Leaf)) {
    throw "BlastEm is missing. Run .\tools\bootstrap.ps1 first."
}
if ($Audio) {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Project-local Python is missing. Run .\tools\bootstrap.ps1 first."
    }
    $AudioArguments = @(
        $AudioCaptureTool,
        "--root", $ProjectRoot,
        "--rom", $Rom,
        "--duration-seconds", $AudioSeconds,
        "--window-timeout-seconds", $StartupTimeoutSeconds,
        "--report", (Join-Path $ProjectRoot "artifacts\reports\m2-audio-capture.json")
    )
    if ($ExpectedSha256) {
        $AudioArguments += @("--expected-sha256", $ExpectedSha256)
    }
    $AudioProcess = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $AudioArguments `
        -NoNewWindow `
        -Wait `
        -PassThru
    if ($AudioProcess.ExitCode -ne 0) {
        throw "Milestone 2 audio capture failed; see artifacts\reports\m2-audio-capture.json."
    }
    Write-Host "Captured and validated hash-bound Milestone 2 VGM audio evidence."
    exit 0
}

$ScreenshotArguments = @(
    $ScreenshotCaptureTool,
    "--root", $ProjectRoot,
    "--rom", $Rom,
    "--window-timeout-seconds", $StartupTimeoutSeconds,
    "--screenshot", (Join-Path $ProjectRoot "artifacts\screenshots\m2-diagnostic.png"),
    "--boot-report", (Join-Path $ProjectRoot "artifacts\reports\boot-screenshot.json"),
    "--m2-report", (Join-Path $ProjectRoot "artifacts\reports\m2-screenshot.json")
)
if ($ExpectedSha256) {
    $ScreenshotArguments += @("--expected-sha256", $ExpectedSha256)
}
$ScreenshotProcess = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $ScreenshotArguments `
    -NoNewWindow `
    -Wait `
    -PassThru
if ($ScreenshotProcess.ExitCode -ne 0) {
    throw "Current normal screenshot capture failed; see artifacts\reports\m2-screenshot.json."
}
Write-Host "Captured and validated current isolated normal diagnostic screenshot."
