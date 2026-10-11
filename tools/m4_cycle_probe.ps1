[CmdletBinding()]
param(
    [string]$Rom,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string]$ExpectedSha256,
    [ValidateRange(30, 600)]
    [int]$TimeoutSeconds = 120,
    [ValidateRange(10, 900)]
    [int]$LockTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$ProbeTool = Join-Path $PSScriptRoot "m4_cycle_evidence.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\m4-cycle-evidence.json"
$Trace = Join-Path $ProjectRoot "artifacts\traces\m4-cycle-native-debugger.log"

# A requested recapture invalidates old canonical evidence before preflight.
foreach ($Artifact in @($Report, $Trace)) {
    if (Test-Path -LiteralPath $Artifact) {
        Remove-Item -LiteralPath $Artifact -Force
    }
}

if (-not $Rom) {
    $Rom = Join-Path $ProjectRoot "build\recursion94-debug.bin"
}
foreach ($Required in @($PythonExe, $ProbeTool, $Rom)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Required cycle-evidence input is missing: $Required"
    }
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
    throw "M4 native 68000 cycle evidence failed; see the cycle report/trace."
}

Write-Host "M4 native 68000 main/VBlank cycle evidence passed."
