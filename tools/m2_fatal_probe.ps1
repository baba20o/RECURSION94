[CmdletBinding()]
param(
    [string]$Rom,
    [string]$ExpectedSha256 = "",
    [ValidateRange(10, 180)]
    [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$Probe = Join-Path $PSScriptRoot "m2_fatal_evidence.py"
if (-not $Rom) {
    $Rom = Join-Path $ProjectRoot "build\recursion94-debug.bin"
}
$Arguments = @(
    $Probe,
    "--root", $ProjectRoot,
    "--rom", $Rom,
    "--timeout-seconds", $TimeoutSeconds,
    "--report", (Join-Path $ProjectRoot "artifacts\reports\m2-fatal.json")
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
    throw "Milestone 2 fatal evidence failed; see artifacts\reports\m2-fatal.json."
}
Write-Host "Milestone 2 real-input fatal-screen evidence passed."
