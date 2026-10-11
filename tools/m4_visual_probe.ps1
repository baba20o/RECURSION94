[CmdletBinding()]
param(
    [string]$Rom,
    [string]$ExpectedSha256 = "c689adf93f646c34809649d103cc7759d4b5adf19f5315b3ab99bafe883d2460",
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 90,
    [ValidateRange(10, 900)]
    [int]$LockTimeoutSeconds = 600,
    [switch]$FinalizeExisting,
    [switch]$MinimalCapture
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$ProbeTool = Join-Path $PSScriptRoot "m4_visual_evidence.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\m4-screenshot.json"
$QaReport = Join-Path $ProjectRoot "artifacts\reports\m4-gameplay-qa.json"
if ($MinimalCapture) {
    $Trace = Join-Path $ProjectRoot "artifacts\traces\m4-visual-minimal-gdb.log"
    $TraceJson = Join-Path $ProjectRoot "artifacts\traces\m4-visual-minimal.json"
}
else {
    $Trace = Join-Path $ProjectRoot "artifacts\traces\m4-visual-gdb-transcript.log"
    $TraceJson = Join-Path $ProjectRoot "artifacts\traces\m4-visual-session.json"
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
    "--report", $Report,
    "--qa-report", $QaReport,
    "--trace", $Trace,
    "--trace-json", $TraceJson,
    "--expected-sha256", $ExpectedSha256,
    "--timeout-seconds", $TimeoutSeconds,
    "--lock-timeout-seconds", $LockTimeoutSeconds
)
if ($FinalizeExisting) {
    $Arguments += "--finalize-existing"
}
if ($MinimalCapture) {
    $Arguments += "--minimal-capture"
}

$Process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $Arguments `
    -NoNewWindow `
    -Wait `
    -PassThru
if ($Process.ExitCode -ne 0) {
    throw "M4 visual/playability evidence failed; see artifacts\reports."
}

if ($MinimalCapture) {
    Write-Host "M4 native tick-8 screenshot proof passed."
}
else {
    Write-Host "M4 native visual and controller-driven QA evidence passed."
}
