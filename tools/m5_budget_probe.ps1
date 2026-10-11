[CmdletBinding()]
param(
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 90,
    [ValidateRange(10, 600)]
    [int]$LockTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".toolcache\python\python.exe"
$Collector = Join-Path $PSScriptRoot "m5_budget_evidence.py"
$Report = Join-Path $ProjectRoot "artifacts\reports\m5-b03-budget.json"
$Trace = Join-Path $ProjectRoot "artifacts\traces\m5-b03-budget-gdb-transcript.log"
$PrerequisiteReport = Join-Path $ProjectRoot "artifacts\reports\m5-b03-runtime.json"
$PrerequisiteTrace = Join-Path $ProjectRoot "artifacts\traces\m5-b03-runtime-gdb-transcript.log"
$PrerequisiteReplay = Join-Path $ProjectRoot "artifacts\replays\m5-b03-live-replay-ram.bin"
$Rom = Join-Path $ProjectRoot "build\level-B03\recursion94-debug.bin"
$Symbols = Join-Path $ProjectRoot "build\level-B03\recursion94-debug.sym"
$Emulator = Join-Path $ProjectRoot ".toolcache\blastem\blastem.exe"
$DefaultConfig = Join-Path $ProjectRoot ".toolcache\blastem\default.cfg"

foreach ($Required in @(
    $PythonExe,
    $Collector,
    $PrerequisiteReport,
    $PrerequisiteTrace,
    $PrerequisiteReplay,
    $Rom,
    $Symbols,
    $Emulator,
    $DefaultConfig
)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Milestone 5 B03 budget input is missing: $Required"
    }
}

$PinnedRomSha256 = "140039e336ca8e1bc1a6042025d41a4190596c244e6a4cce98f1a3a325e7265e"
$PinnedSymbolSha256 = "b1010589d79ac0594d146001833185f1f4747a480e3929c80b71f9c6dd5fa8b0"
$PinnedRuntimeReportSha256 = "605f9118498908e1c263688abca72d833e993b27d22d00a08676577e7842798f"
$PinnedRuntimeTraceSha256 = "472e90d2508f0afa8c10797bf3fc4dd58304982e8e93245c13ee353ce6d6ceb9"
$PinnedRuntimeReplaySha256 = "01ec5267ec497ceafd81879d449ec05ea44aef78112b5945f198e4a3bf5370e5"
$PinnedEmulatorSha256 = "22b46b9476f5fe208455d7823bb403eb6e49c467296475bca300efabaf757803"
$PinnedDefaultConfigSha256 = "27cf0b11a6e551f4427608ee32cc94018f82f9c655ba6eb5a5e69ecf8b1aba4e"
$PinnedBlastEmSourceNode = "732f5689d4381862da283c420b68f37ac320c1d8"
$PinnedBlastEmSourceHashes = @{
    "bindings.c" = "d6f2e4d5d7b38a1180654ed7354776a3f1d2879b1e454733687d906f3810a429"
    "blastem.c" = "dc88e812d70d1ed3baffa4f488e5e1af3df3fb47245103792474eb4ce83c946c"
    "gdb_remote.c" = "9eda587f9d11c6a8a624dc2f17c2293ba5600a8cfa6370639841b6fb6708cf92"
    "genesis.c" = "8b02bb53c0105c29af88008b674f3105a9d487a83fbc50f3d500579bb4c85364"
    "render_sdl.c" = "c969bb16e1d83c76602835a9dc2caa5dde6899b5810e3260de5a7588d795b85e"
    "vdp.c" = "21fc02bca5ad4f2e16e2be364195e9f02907cc68e0538aa6be8c8e436a9498e2"
}

$ObservedBindings = @{
    "B03 ROM" = (Get-FileHash -LiteralPath $Rom -Algorithm SHA256).Hash.ToLowerInvariant()
    "B03 symbols" = (Get-FileHash -LiteralPath $Symbols -Algorithm SHA256).Hash.ToLowerInvariant()
    "B03 runtime report" = (Get-FileHash -LiteralPath $PrerequisiteReport -Algorithm SHA256).Hash.ToLowerInvariant()
    "B03 runtime trace" = (Get-FileHash -LiteralPath $PrerequisiteTrace -Algorithm SHA256).Hash.ToLowerInvariant()
    "B03 runtime replay" = (Get-FileHash -LiteralPath $PrerequisiteReplay -Algorithm SHA256).Hash.ToLowerInvariant()
    "BlastEm executable" = (Get-FileHash -LiteralPath $Emulator -Algorithm SHA256).Hash.ToLowerInvariant()
    "BlastEm default config" = (Get-FileHash -LiteralPath $DefaultConfig -Algorithm SHA256).Hash.ToLowerInvariant()
}
$ExpectedBindings = @{
    "B03 ROM" = $PinnedRomSha256
    "B03 symbols" = $PinnedSymbolSha256
    "B03 runtime report" = $PinnedRuntimeReportSha256
    "B03 runtime trace" = $PinnedRuntimeTraceSha256
    "B03 runtime replay" = $PinnedRuntimeReplaySha256
    "BlastEm executable" = $PinnedEmulatorSha256
    "BlastEm default config" = $PinnedDefaultConfigSha256
}
foreach ($Name in $ExpectedBindings.Keys) {
    if ($ObservedBindings[$Name] -ne $ExpectedBindings[$Name]) {
        throw "$Name SHA-256 does not match the root-approved M5 B03 prerequisite."
    }
}

$RuntimeEvidence = Get-Content -LiteralPath $PrerequisiteReport -Raw |
    ConvertFrom-Json
if (
    $RuntimeEvidence.status -ne "pass" -or
    $RuntimeEvidence.acceptance_status -ne "pass" -or
    $RuntimeEvidence.rom_sha256 -ne $PinnedRomSha256 -or
    $RuntimeEvidence.symbol_map_sha256 -ne $PinnedSymbolSha256
) {
    throw "Canonical M5 B03 runtime evidence is not PASS/PASS with the pinned ROM and symbols."
}

$Arguments = @(
    $Collector,
    "--root", $ProjectRoot,
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
    throw "Milestone 5 B03 budget capture failed; see artifacts\reports\m5-b03-budget.json."
}

$ValidationArguments = @(
    $Collector,
    "--root", $ProjectRoot,
    "--report", $Report,
    "--trace", $Trace,
    "--validate-existing"
)
$ValidationProcess = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $ValidationArguments `
    -NoNewWindow `
    -Wait `
    -PassThru
if ($ValidationProcess.ExitCode -ne 0) {
    throw "Milestone 5 B03 budget report failed full schema/semantic revalidation."
}

$Evidence = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
if (
    $Evidence.schema -ne "r94-m5-b03-budget-evidence-v3" -or
    $Evidence.evidence_type -ne "blastem_gdb_m5_b03_combined_room_budget_v3" -or
    $Evidence.status -ne "pass" -or
    $Evidence.acceptance_status -ne "pass" -or
    $Evidence.rom_sha256 -ne $PinnedRomSha256 -or
    $Evidence.symbol_map_sha256 -ne $PinnedSymbolSha256 -or
    $Evidence.debugger_write_packets.Count -ne 0
) {
    throw "Milestone 5 B03 budget report did not satisfy its public PASS contract."
}

if ($Evidence.sessions.Count -ne 2) {
    throw "Milestone 5 B03 budget report does not contain two sessions."
}
$SourceContract = $Evidence.provenance.frame_advance_source_contract
if (
    $SourceContract.status -ne "pass" -or
    $SourceContract.source_node -ne $PinnedBlastEmSourceNode -or
    $SourceContract.default_config_sha256 -ne $PinnedDefaultConfigSha256 -or
    $SourceContract.bindings.frame_advance_matches -ne 1 -or
    $SourceContract.bindings.pause_matches -ne 1
) {
    throw "Milestone 5 B03 budget report has a mismatched BlastEm frame-advance source contract."
}
foreach ($SourceFile in $PinnedBlastEmSourceHashes.Keys) {
    $ObservedSourceHash = $SourceContract.source_sha256.PSObject.Properties[$SourceFile].Value
    if ($ObservedSourceHash -ne $PinnedBlastEmSourceHashes[$SourceFile]) {
        throw "Milestone 5 B03 budget report has a mismatched BlastEm source hash for $SourceFile."
    }
}

$ExpectedSessionIds = @("budget_session_1", "budget_session_2")
$ObservedProcessIds = @()
$ObservedRuntimeDirectories = @()
$ObservedBudgetSignatures = @($Evidence.budget_signatures | ForEach-Object { $_.sha256 })
if (
    $ObservedBudgetSignatures.Count -ne 2 -or
    $ObservedBudgetSignatures[0] -ne $ObservedBudgetSignatures[1]
) {
    throw "Milestone 5 B03 budget sessions do not have one exact shared signature."
}

$SessionIndex = 0
foreach ($Session in $Evidence.sessions) {
    $Audit = $Session.metadata.frame_advance_audit
    $Rows = $Session.route.cadence_rows
    $TerminalPost = $Rows[1731]
    $TerminalWait = $Session.route.terminal_wait_observation
    if (
        $Session.session_id -ne $ExpectedSessionIds[$SessionIndex] -or
        $Rows.Count -ne 1732 -or
        $Session.route.scenario_snapshots.Count -ne 27 -or
        $Rows[0].authoritative_total -ne 1 -or
        $TerminalPost.authoritative_total -ne 1732 -or
        $Session.metadata.sentinel_handshakes.Count -ne 1736 -or
        $Session.metadata.ui_advance_events.Count -ne 1737 -or
        $Session.metadata.ui_advance_events[0].index -ne 1 -or
        $Session.metadata.ui_advance_events[1736].index -ne 1737 -or
        $Audit.status -ne "pass" -or
        -not $Audit.exact_event_order -or
        -not $Audit.exact_post_total_sequence -or
        -not $Audit.exact_pc_sequence -or
        -not $Audit.exact_sentinel_boundary_sequence -or
        -not $Audit.exact_ui_advance_sequence -or
        $Audit.post_breakpoint_insertions -ne 1 -or
        $Audit.vblank_breakpoint_insertions -ne 1 -or
        $Audit.vblank_breakpoint_removals -ne 1 -or
        $Audit.post_breakpoint_removals -ne 1 -or
        $Audit.finalizer_breakpoint_insertions -ne 1 -or
        $Audit.finalizer_breakpoint_removals -ne 1 -or
        $Audit.wait_breakpoint_insertions -ne 1 -or
        $Audit.wait_breakpoint_removals -ne 1 -or
        $Audit.post_stops -ne 1732 -or
        $Audit.finalizer_stops -ne 1 -or
        $Audit.wait_stops -ne 1 -or
        $Audit.sentinel_handshakes -ne 1736 -or
        $Audit.pause_overwrites -ne 1736 -or
        $Audit.ui_advance_pulses -ne 1737 -or
        $Audit.memory_read_sections -ne 1871 -or
        $Audit.memory_read_pairs -ne 8121 -or
        $Audit.trace_report_binding_errors.Count -ne 0 -or
        -not $Audit.trace_report_bindings_exact -or
        $Audit.transcript_command_count -ne 11600 -or
        $Audit.protocol_event_count -ne 14031 -or
        $Audit.expected_protocol_event_count -ne 14031 -or
        $Audit.malformed_protocol_lines.Count -ne 0 -or
        $Audit.forbidden_control.Count -ne 0 -or
        $Audit.debugger_write_packets.Count -ne 0 -or
        $TerminalWait.authoritative_total -ne 1732 -or
        $TerminalWait.observed.engine.state -ne 2 -or
        $TerminalWait.observed.engine.attempt -ne 2 -or
        $TerminalWait.observed.engine.tick -ne 292 -or
        $TerminalWait.observed.engine.terminal_reason -ne 256 -or
        $TerminalWait.observed.debug.sequence -ne 3472 -or
        $TerminalWait.observed.debug.frame_counter -ne 1736 -or
        $TerminalWait.observed.debug.heartbeat -ne 1736 -or
        $TerminalWait.observed.debug.frame_token -ne 0 -or
        $TerminalWait.observed.debug.vblank_overrun_count -ne 0 -or
        $TerminalWait.blocks.engine_control.sha256 -ne $TerminalPost.blocks.engine_control.sha256 -or
        $TerminalWait.blocks.debug_budget.sha256 -ne $TerminalPost.blocks.debug_budget.sha256 -or
        $TerminalWait.blocks.input_state.sha256 -ne $TerminalPost.blocks.input_state.sha256 -or
        $Session.route.terminal_replay.sha256 -ne $PinnedRuntimeReplaySha256
    ) {
        throw "Milestone 5 B03 budget session cadence/replay contract failed."
    }
    $ObservedProcessIds += $Session.metadata.process_id
    $ObservedRuntimeDirectories += $Session.metadata.runtime_copy.temporary_directory
    $SessionIndex++
}
if (
    @($ObservedProcessIds | Select-Object -Unique).Count -ne 2 -or
    @($ObservedRuntimeDirectories | Select-Object -Unique).Count -ne 2
) {
    throw "Milestone 5 B03 budget evidence did not use two fresh owned sessions."
}

Write-Host "Milestone 5 B03 combined-room hardware-budget evidence passed."
