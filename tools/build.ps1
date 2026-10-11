[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug",
    [string]$Level
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$BuildTool = Join-Path $PSScriptRoot "build_rom.py"

$NormalizedLevel = $null
if ($Level) {
    if ($Configuration -ne "Debug") {
        throw "-Level is Debug-only; the canonical Release ROM always boots the campaign entry."
    }
    $NormalizedLevel = $Level.Trim().ToUpperInvariant()
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Project-local Python is missing. Run .\tools\bootstrap.ps1 first."
}

$Arguments = @($BuildTool, "--root", $ProjectRoot, "--configuration", $Configuration)
if ($NormalizedLevel) {
    $Arguments += @("--start-encounter", $NormalizedLevel)
}
$Process = Start-Process -FilePath $PythonExe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
if ($Process.ExitCode -ne 0) {
    throw "RECURSION 94 $Configuration build failed with exit code $($Process.ExitCode)."
}

Write-Host "RECURSION 94 $Configuration build passed."
