[CmdletBinding()]
param(
    [ValidateSet("All", "Host", "Toolchain", "Rom", "Milestone2", "Milestone3", "Milestone4", "Milestone5", "Render", "Assets", "Replay", "Save", "Content", "CampaignRuntime", "Audio", "Evidence", "Emulator")]
    [string]$Suite = "All"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$TestTool = Join-Path $PSScriptRoot "run_host_tests.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\host-tests.json"

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Project-local Python is missing. Run .\tools\bootstrap.ps1 first."
}

if ($Suite -eq "Emulator") {
    $ProbeTool = Join-Path $PSScriptRoot "emulator_probe.py"
    $Rom = Join-Path $ProjectRoot "build\recursion94-debug.bin"
    $EmulatorReport = Join-Path $ProjectRoot "artifacts\reports\emulator-smoke.json"
    $EventLog = Join-Path $ProjectRoot "artifacts\traces\emulator-smoke.events"
    if (-not (Test-Path -LiteralPath $Rom -PathType Leaf)) {
        throw "Debug ROM is missing. Run .\tools\build.ps1 -Configuration Debug first."
    }
    $ProbeArguments = @(
        $ProbeTool,
        "--root", $ProjectRoot,
        "--rom", $Rom,
        "--frames", "120",
        "--mode", "smoke",
        "--report", $EmulatorReport,
        "--event-log", $EventLog
    )
    $ProbeProcess = Start-Process -FilePath $PythonExe -ArgumentList $ProbeArguments -NoNewWindow -Wait -PassThru
    if ($ProbeProcess.ExitCode -ne 0) {
        throw "Bounded emulator smoke failed; see $EmulatorReport."
    }
    Write-Host "Bounded emulator process passed; heartbeat evidence is a separate gate."
    exit 0
}

$Arguments = @($TestTool, "--root", $ProjectRoot, "--suite", $Suite, "--report", $Report)
$Process = Start-Process -FilePath $PythonExe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
if ($Process.ExitCode -eq 2) {
    throw "Test suite '$Suite' is a pending release gate; see $Report."
}
if ($Process.ExitCode -ne 0) {
    throw "RECURSION 94 test suite '$Suite' failed; see $Report."
}

Write-Host "RECURSION 94 available '$Suite' tests passed."
