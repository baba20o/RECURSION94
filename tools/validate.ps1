[CmdletBinding()]
param(
    [ValidateSet("Milestone1", "Milestone2", "Milestone3", "Milestone4", "Milestone5", "Release")]
    [string]$Scope = "Release"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PowerShellExe = (Get-Process -Id $PID).Path
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$CleanScript = Join-Path $PSScriptRoot "clean.ps1"
$BuildScript = Join-Path $PSScriptRoot "build.ps1"
$TestScript = Join-Path $PSScriptRoot "test.ps1"
$ToolchainTool = Join-Path $PSScriptRoot "verify_toolchain.py"
$RomTool = Join-Path $PSScriptRoot "validate_rom.py"
$EmulatorTool = Join-Path $PSScriptRoot "emulator_probe.py"
$ReportTool = Join-Path $PSScriptRoot "validation_report.py"
$ReportRoot = Join-Path $ProjectRoot "artifacts\reports"
$TraceRoot = Join-Path $ProjectRoot "artifacts\traces"
$RequiresMilestone5 = $Scope -in @("Milestone5", "Release")
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
New-Item -ItemType Directory -Force -Path $TraceRoot | Out-Null

function Invoke-ChildPowerShell {
    param([string]$Script, [string[]]$Arguments)
    $ProcessArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $Script
    ) + $Arguments
    $Process = Start-Process -FilePath $PowerShellExe -ArgumentList $ProcessArguments -NoNewWindow -Wait -PassThru
    return $Process.ExitCode
}

function Invoke-Python {
    param([string[]]$Arguments)
    $Process = Start-Process -FilePath $PythonExe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
    return $Process.ExitCode
}

$CleanExit = Invoke-ChildPowerShell $CleanScript @()
$ToolchainExit = Invoke-Python @(
    $ToolchainTool,
    "--root", $ProjectRoot,
    "--verify-archives",
    "--report", (Join-Path $ReportRoot "toolchain-validation.json")
)
$DebugBuildExit = Invoke-ChildPowerShell $BuildScript @("-Configuration", "Debug")
$ReleaseBuildExit = Invoke-ChildPowerShell $BuildScript @("-Configuration", "Release")
$B03DebugBuildExit = 0
if ($RequiresMilestone5) {
    $B03DebugBuildExit = Invoke-ChildPowerShell $BuildScript @(
        "-Configuration", "Debug",
        "-Level", "B03"
    )
}
$HostTestsExit = Invoke-ChildPowerShell $TestScript @("-Suite", "All")

$DebugRom = Join-Path $ProjectRoot "build\recursion94-debug.bin"
$ReleaseRom = Join-Path $ProjectRoot "build\recursion94-release.bin"
$B03DebugRom = Join-Path $ProjectRoot "build\level-B03\recursion94-debug.bin"
$DebugStaticReport = if ($RequiresMilestone5) {
    Join-Path $ReportRoot "rom-debug-milestone5.json"
} else {
    Join-Path $ReportRoot "rom-debug-validation.json"
}
$ReleaseStaticReport = if ($RequiresMilestone5) {
    Join-Path $ReportRoot "rom-release-milestone5.json"
} else {
    Join-Path $ReportRoot "rom-release-validation.json"
}
$DebugStaticExit = Invoke-Python @(
    $RomTool,
    $DebugRom,
    "--report", $DebugStaticReport
)
$ReleaseStaticExit = Invoke-Python @(
    $RomTool,
    $ReleaseRom,
    "--report", $ReleaseStaticReport
)
$B03DebugStaticExit = 0
if ($RequiresMilestone5) {
    $B03DebugStaticExit = Invoke-Python @(
        $RomTool,
        $B03DebugRom,
        "--report", (Join-Path $ReportRoot "rom-b03-milestone5.json")
    )
}
$EmulatorProbeExit = Invoke-Python @(
    $EmulatorTool,
    "--root", $ProjectRoot,
    "--rom", $DebugRom,
    "--frames", "120",
    "--mode", "smoke",
    "--report", (Join-Path $ReportRoot "emulator-smoke.json"),
    "--event-log", (Join-Path $TraceRoot "emulator-smoke.events")
)

$ValidationReportName = switch ($Scope) {
    "Release" { "validation.json" }
    "Milestone5" { "validation-milestone5.json" }
    "Milestone4" { "validation-milestone4.json" }
    "Milestone3" { "validation-milestone3.json" }
    "Milestone2" { "validation-milestone2.json" }
    default { "validation-milestone1.json" }
}
$ValidationReport = Join-Path $ReportRoot $ValidationReportName
$ReportExit = Invoke-Python @(
    $ReportTool,
    "--root", $ProjectRoot,
    "--scope", $Scope,
    "--report", $ValidationReport,
    "--clean-exit", $CleanExit,
    "--toolchain-exit", $ToolchainExit,
    "--debug-build-exit", $DebugBuildExit,
    "--release-build-exit", $ReleaseBuildExit,
    "--b03-debug-build-exit", $B03DebugBuildExit,
    "--host-tests-exit", $HostTestsExit,
    "--debug-static-exit", $DebugStaticExit,
    "--release-static-exit", $ReleaseStaticExit,
    "--b03-debug-static-exit", $B03DebugStaticExit,
    "--emulator-probe-exit", $EmulatorProbeExit
)

if ($ReportExit -ne 0) {
    Write-Host "$Scope validation is not complete; see $ValidationReport."
    exit $ReportExit
}

Write-Host "$Scope validation passed."
