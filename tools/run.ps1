[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug",
    [switch]$NoBuild,
    [switch]$Wait,
    [ValidateRange(0, 36000)]
    [int]$Frames = 0,
    [string]$EventLog,
    [string]$Level,
    [string]$Replay
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PowerShellExe = (Get-Process -Id $PID).Path
$BuildScript = Join-Path $PSScriptRoot "build.ps1"
$Emulator = Join-Path $ProjectRoot ".toolcache\blastem\blastem.exe"
$NormalizedLevel = $null
if ($Level) {
    if ($Configuration -ne "Debug") {
        throw "-Level is Debug-only; the canonical Release ROM always boots the campaign entry."
    }
    $NormalizedLevel = $Level.Trim().ToUpperInvariant()
}
$Rom = if ($NormalizedLevel) {
    Join-Path $ProjectRoot (
        "build\level-" + $NormalizedLevel + "\recursion94-debug.bin"
    )
} else {
    Join-Path $ProjectRoot (
        "build\recursion94-" + $Configuration.ToLowerInvariant() + ".bin"
    )
}

if ($Replay) {
    throw "-Replay is reserved for the future runtime replay-entry ABI and is not silently ignored."
}

if (-not $NoBuild) {
    $BuildArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $BuildScript,
        "-Configuration", $Configuration
    )
    if ($NormalizedLevel) {
        $BuildArguments += @("-Level", $NormalizedLevel)
    }
    $BuildProcess = Start-Process -FilePath $PowerShellExe -ArgumentList $BuildArguments -NoNewWindow -Wait -PassThru
    if ($BuildProcess.ExitCode -ne 0) {
        throw "Build failed before emulator launch with exit code $($BuildProcess.ExitCode)."
    }
}

if (-not (Test-Path -LiteralPath $Rom -PathType Leaf)) {
    throw "ROM is missing: $Rom"
}
if (-not (Test-Path -LiteralPath $Emulator -PathType Leaf)) {
    throw "BlastEm is missing. Run .\tools\bootstrap.ps1 first."
}

$EmulatorArguments = @()
if ($Frames -gt 0) {
    $EmulatorArguments += @("-b", $Frames)
}
if ($EventLog) {
    $EventPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $EventLog))
    $EventParent = Split-Path -Parent $EventPath
    New-Item -ItemType Directory -Force -Path $EventParent | Out-Null
    $EmulatorArguments += @("-e", $EventPath)
}
$EmulatorArguments += $Rom

$ShouldWait = $Wait -or ($Frames -gt 0)
$ProcessArguments = @{
    FilePath = $Emulator
    ArgumentList = $EmulatorArguments
    WorkingDirectory = (Split-Path -Parent $Emulator)
    PassThru = $true
}
if ($ShouldWait) {
    $ProcessArguments["Wait"] = $true
    $ProcessArguments["NoNewWindow"] = $true
}
$Process = Start-Process @ProcessArguments
if ($ShouldWait -and $Process.ExitCode -ne 0) {
    $ExitBytes = [System.BitConverter]::GetBytes([int32]$Process.ExitCode)
    $UnsignedExit = [System.BitConverter]::ToUInt32($ExitBytes, 0)
    throw ("BlastEm exited with code 0x{0:X8}." -f $UnsignedExit)
}

if ($ShouldWait) {
    Write-Host "BlastEm run completed."
} else {
    Write-Host "BlastEm launched with process id $($Process.Id)."
}
