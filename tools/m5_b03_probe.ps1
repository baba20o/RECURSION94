[CmdletBinding()]
param(
    [string]$Rom,
    [string]$Symbols,
    [string]$ExpectedSha256 = "",
    [string]$ExpectedSymbolSha256 = "",
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 90,
    [ValidateRange(10, 600)]
    [int]$LockTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$ProbeTool = Join-Path $PSScriptRoot "m5_b03_runtime_evidence.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\m5-b03-runtime.json"
$Trace = Join-Path $ProjectRoot "artifacts\traces\m5-b03-runtime-gdb-transcript.log"
$ReplayRam = Join-Path $ProjectRoot "artifacts\replays\m5-b03-live-replay-ram.bin"
if (-not $Rom) {
    $Rom = Join-Path $ProjectRoot "build\level-B03\recursion94-debug.bin"
}
if (-not $Symbols) {
    $Symbols = Join-Path $ProjectRoot "build\level-B03\recursion94-debug.sym"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Project-local Python is missing. Run .\tools\bootstrap.ps1 first."
}
foreach ($Required in @($Rom, $Symbols, $ProbeTool)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Milestone 5 B03 evidence input is missing: $Required"
    }
}

$Arguments = @(
    $ProbeTool,
    "--root", $ProjectRoot,
    "--rom", $Rom,
    "--symbols", $Symbols,
    "--report", $Report,
    "--trace", $Trace,
    "--replay-ram", $ReplayRam,
    "--timeout-seconds", $TimeoutSeconds,
    "--lock-timeout-seconds", $LockTimeoutSeconds
)
if ($ExpectedSha256) {
    $Arguments += @("--expected-sha256", $ExpectedSha256)
}
if ($ExpectedSymbolSha256) {
    $Arguments += @(
        "--expected-symbol-sha256",
        $ExpectedSymbolSha256
    )
}

$Process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $Arguments `
    -NoNewWindow `
    -Wait `
    -PassThru
if ($Process.ExitCode -ne 0) {
    throw "Milestone 5 B03 runtime evidence failed; see artifacts\reports\m5-b03-runtime.json."
}
$Evidence = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
if (
    $Evidence.status -ne "pass" -or
    $Evidence.acceptance_status -ne "pass"
) {
    throw "Milestone 5 B03 runtime evidence or full acceptance did not pass."
}

Write-Host "Milestone 5 B03 live evidence and full acceptance passed."
