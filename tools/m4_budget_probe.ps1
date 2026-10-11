[CmdletBinding()]
param(
    [string]$Rom,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string]$ExpectedSha256,
    [ValidateRange(15, 600)]
    [int]$TimeoutSeconds = 60,
    [ValidateRange(10, 900)]
    [int]$LockTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$ProbeTool = Join-Path $PSScriptRoot "m4_budget_evidence.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\m4-renderer-budget.json"
$Trace = Join-Path $ProjectRoot "artifacts\traces\m4-renderer-budget-gdb-transcript.log"

# Invalidate prior canonical evidence before any preflight can fail. A missing
# report is pending/fail-closed; an old pass must never survive a new attempt.
foreach ($Artifact in @($Report, $Trace)) {
    if (Test-Path -LiteralPath $Artifact) {
        Remove-Item -LiteralPath $Artifact -Force
    }
}

if (-not $Rom) {
    $Rom = Join-Path $ProjectRoot "build\recursion94-debug.bin"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Project-local Python is missing. Run .\tools\bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath $Rom -PathType Leaf)) {
    throw "Debug ROM is missing: $Rom"
}

$Arguments = @(
    $ProbeTool,
    "--root", $ProjectRoot,
    "--rom", $Rom,
    "--expected-sha256", $ExpectedSha256.ToLowerInvariant(),
    "--report", $Report,
    "--trace", $Trace,
    "--timeout-seconds", $TimeoutSeconds,
    "--lock-timeout-seconds", $LockTimeoutSeconds
)

$Process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $Arguments `
    -NoNewWindow `
    -Wait `
    -PassThru
if ($Process.ExitCode -ne 0) {
    throw "M4 renderer/VBlank/cross-clock budget evidence failed; see artifacts\reports\m4-renderer-budget.json."
}

Write-Host "M4 two-session uninterrupted renderer/VBlank/cross-clock budget evidence passed."
