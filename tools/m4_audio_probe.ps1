[CmdletBinding()]
param(
    [string]$Rom,
    [string]$ExpectedSha256 = "",
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 90,
    [ValidateRange(10, 900)]
    [int]$LockTimeoutSeconds = 600,
    [switch]$RecheckExisting
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$ProbeTool = Join-Path $PSScriptRoot "m4_audio_evidence.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\m4-audio-capture.json"
$Trace = Join-Path $ProjectRoot "artifacts\traces\m4-audio-gdb-transcript.log"
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
    "--trace", $Trace,
    "--timeout-seconds", $TimeoutSeconds,
    "--lock-timeout-seconds", $LockTimeoutSeconds
)
if ($ExpectedSha256) {
    $Arguments += @("--expected-sha256", $ExpectedSha256)
}
if ($RecheckExisting) {
    $Arguments += "--recheck-existing"
}

$Process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $Arguments `
    -NoNewWindow `
    -Wait `
    -PassThru
if ($Process.ExitCode -ne 0) {
    throw "Milestone 4 audio evidence failed; see artifacts\reports\m4-audio-capture.json."
}

Write-Host "Milestone 4 hash-bound echo-layer VGM/GDB evidence passed."
