[CmdletBinding()]
param(
    [string]$Rom,
    [string]$ExpectedSha256 = "",
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 90,
    [ValidateRange(10, 600)]
    [int]$LockTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$ProbeTool = Join-Path $PSScriptRoot "m3_runtime_evidence.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\m3-runtime.json"
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
    "--timeout-seconds", $TimeoutSeconds,
    "--lock-timeout-seconds", $LockTimeoutSeconds
)
if ($ExpectedSha256) {
    $Arguments += @("--expected-sha256", $ExpectedSha256)
}

$Process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $Arguments `
    -NoNewWindow `
    -Wait `
    -PassThru
if ($Process.ExitCode -ne 0) {
    throw "Milestone 3 runtime evidence failed; see artifacts\reports\m3-runtime.json."
}

Write-Host "Milestone 3 two-session deterministic runtime evidence passed."
