[CmdletBinding()]
param(
    [string]$Rom,
    [string]$Symbols,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{64}$")]
    [string]$ExpectedSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{64}$")]
    [string]$ExpectedSymbolSha256,
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 90,
    [ValidateRange(10, 600)]
    [int]$LockTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PinnedRomSha256 = "140039e336ca8e1bc1a6042025d41a4190596c244e6a4cce98f1a3a325e7265e"
$PinnedSymbolSha256 = "b1010589d79ac0594d146001833185f1f4747a480e3929c80b71f9c6dd5fa8b0"
if ($ExpectedSha256 -cne $PinnedRomSha256) {
    throw "Milestone 5 visual probe is pinned to B03 ROM SHA-256 $PinnedRomSha256."
}
if ($ExpectedSymbolSha256 -cne $PinnedSymbolSha256) {
    throw "Milestone 5 visual probe is pinned to B03 symbol SHA-256 $PinnedSymbolSha256."
}
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$ProbeTool = Join-Path $PSScriptRoot "m5_visual_evidence.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\m5-b03-visual.json"
$Trace = Join-Path $ProjectRoot "artifacts\traces\m5-b03-visual-gdb-transcript.log"
if (-not $Rom) {
    $Rom = Join-Path $ProjectRoot "build\level-B03\recursion94-debug.bin"
}
if (-not $Symbols) {
    $Symbols = Join-Path $ProjectRoot "build\level-B03\recursion94-debug.sym"
}
foreach ($Required in @($PythonExe, $ProbeTool, $Rom, $Symbols)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Milestone 5 visual evidence input is missing: $Required"
    }
}

$Arguments = @(
    $ProbeTool,
    "--root", $ProjectRoot,
    "--rom", $Rom,
    "--symbols", $Symbols,
    "--report", $Report,
    "--trace", $Trace,
    "--expected-sha256", $ExpectedSha256,
    "--expected-symbol-sha256", $ExpectedSymbolSha256,
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
    throw "Milestone 5 B03 native visual capture failed; see artifacts\reports\m5-b03-visual.json."
}
$Evidence = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
if (
    $Evidence.status -ne "pass" -or
    $Evidence.acceptance_status -ne "evidence_ready" -or
    $Evidence.objective_only.subjective_scores_emitted -ne $false -or
    $Evidence.objective_only.critic_handoff_required -ne $true
) {
    throw "Milestone 5 B03 visual evidence is not ready for independent review."
}

Write-Host "Milestone 5 B03 native screenshots are ready for independent visual review."
