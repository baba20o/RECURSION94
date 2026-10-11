[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug",
    [ValidateRange(1, 36000)]
    [int]$Frames = 600,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PowerShellExe = (Get-Process -Id $PID).Path
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$BuildScript = Join-Path $PSScriptRoot "build.ps1"
$ProbeTool = Join-Path $PSScriptRoot "emulator_probe.py"
$Stem = "recursion94-" + $Configuration.ToLowerInvariant()
$Rom = Join-Path $ProjectRoot ("build\" + $Stem + ".bin")
$Report = Join-Path $ProjectRoot ("artifacts\reports\benchmark-" + $Configuration.ToLowerInvariant() + ".json")
$EventLog = Join-Path $ProjectRoot ("artifacts\traces\benchmark-" + $Configuration.ToLowerInvariant() + ".events")

if (-not $NoBuild) {
    $BuildArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $BuildScript,
        "-Configuration", $Configuration
    )
    $BuildProcess = Start-Process -FilePath $PowerShellExe -ArgumentList $BuildArguments -NoNewWindow -Wait -PassThru
    if ($BuildProcess.ExitCode -ne 0) {
        throw "$Configuration build failed before benchmark."
    }
}

$Arguments = @(
    $ProbeTool,
    "--root", $ProjectRoot,
    "--rom", $Rom,
    "--frames", $Frames,
    "--mode", "benchmark",
    "--report", $Report,
    "--event-log", $EventLog
)
$Process = Start-Process -FilePath $PythonExe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
if ($Process.ExitCode -ne 0) {
    throw "Bounded emulator benchmark failed; see $Report."
}

Write-Host "Emulator-throughput benchmark completed; this is not a hardware-budget pass."
