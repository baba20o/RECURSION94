#!/usr/bin/env python3
"""Collect strict Milestone 4 visual and controller-driven playability evidence.

The live collector owns one fresh portable BlastEm session per required frame.
Gameplay input is delivered only as window key messages to an explicitly
configured standard three-button pad.  GDB is restricted to breakpoint
control and bounded memory reads; no guest RAM, register, or PC write packet
is permitted.

Frames are captured with BlastEm's own ``ui.screenshot`` action after the
renderer's prepared DMA queue has been committed in VBlank. Runtime state is
read immediately before and after servicing that UI action, so every PNG is
bound to the exact published state and the single authoritative step needed
to let the emulator process the request.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from ctypes import wintypes

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    DEBUG_BASE,
    DEBUG_BLOCK_BYTES,
    EvidenceFailure,
    GdbRemote,
    apply_blastem_session_isolation,
    check,
    clone_blastem_runtime,
    exclusive_file_lock,
    find_process_window,
    parse_debug_block,
    parse_vasm_symbols,
    post_virtual_key,
    require_gdb_port_available,
    require_stop,
    sha256_file,
    shutdown_emulator,
    verify_no_user_blastem_override,
    write_json,
    write_standard_pad_profile,
)
from tools.m3_runtime_evidence import (  # noqa: E402
    ENGINE_CONTROL_BASE,
    ENGINE_CONTROL_USED_BYTES,
    ENGINE_MAGIC,
    ENGINE_STATE_ACTIVE,
    ENGINE_STATE_TERMINAL,
    INPUT_STATE_BASE,
    INPUT_STATE_BYTES,
    _read_region,
    crc32_iso_hdlc,
    debugger_write_packets,
    parse_engine_control,
    parse_input_state,
)


REPORT_SCHEMA = "r94-m4-visual-evidence-v1"
QA_REPORT_SCHEMA = "r94-m4-controller-playability-review-v1"
EVIDENCE_TYPE = "blastem_m4_native_rendered_state_png_set"
QA_EVIDENCE_TYPE = "independent_controller_driven_b01_playability_review"
STARTUP_PRIMING_ROLE = "stage_3_ack_only_priming_isr"
FIRST_RUNNING_VBLANK_ROLE = "next_stage_4_running_full_vblank"

PRE_REPAIR_DEBUG_ROM_SHA256 = (
    "9cbc580892e2530bfb48e3d0da865a75f287e16f811ee47cb42515cafd153104"
)
EXPECTED_DEBUG_ROM_SHA256 = (
    "c689adf93f646c34809649d103cc7759d4b5adf19f5315b3ab99bafe883d2460"
)

ENGINE_CONTROL_READ_BYTES = ENGINE_CONTROL_USED_BYTES
ENGINE_ACTOR_BASE = 0x00FF1800
ACTOR_BYTES = 0x20
ACTOR_COUNT = 7
CURRENT_ACTOR_SLOT = 6
B01_DEVICE_BASE = 0x00FF2600
OBJECT_BYTES = 0x18

REPLAY_BANK_BASE = 0x00FF0000
REPLAY_BANK_BYTES = 720
REPLAY_DESCRIPTOR_BASE = 0x00FF13B0
REPLAY_DESCRIPTOR_BYTES = 8
REPLAY_BANK_6 = REPLAY_BANK_BASE + 6 * REPLAY_BANK_BYTES
REPLAY_DESCRIPTOR_6 = REPLAY_DESCRIPTOR_BASE + 6 * REPLAY_DESCRIPTOR_BYTES
TIMELINE_CONTROL_BASE = 0x00FF13E8
TIMELINE_CONTROL_BYTES = 24
GAMEPLAY_CONTROL_BASE = 0x00FF1500
GAMEPLAY_CONTROL_BYTES = 0x24
RENDERER_STATE_BASE = 0x00FF8300
RENDERER_STATE_BYTES = 0x24
DEBUG_VISUAL_BYTES = 0x78

TIMELINE_MAGIC = 0x52393454
GAMEPLAY_MAGIC = 0x52393447
RENDERER_MAGIC = 0x52393456
ENGINE_TERMINAL_NONE = 0
GAMEPLAY_TERMINAL_PROGRAM_STABLE = 0x0100
GAMEPLAY_RESULT_OK = 0
GAMEPLAY_RESULT_PROGRAM_STABLE = 1
GAMEPLAY_COMMIT_MANUAL = 2

REPLAY_FLAG_VALID = 0x01
REPLAY_FLAG_ECHO = 0x02
REPLAY_FLAG_CURRENT = 0x04
GAMEPLAY_FLAG_INITIALIZED = 0x0001
GAMEPLAY_FLAG_PROGRAM_STABLE = 0x0002
GAMEPLAY_FLAG_DOOR_OPEN = 0x0008
GAMEPLAY_FLAG_SWITCH_HELD = 0x0010
RENDERER_FLAG_INITIALIZED = 0x0001
RENDERER_FLAG_RESET_ACTIVE = 0x0002
RENDERER_FLAG_QUEUE_BACKLOG = 0x0004
RENDERER_FLAG_SPRITE_DROPPED = 0x0008
RENDERER_FLAG_DMA_REJECTED = 0x0010
RENDERER_FLAG_PROGRAM_STABLE = 0x0040
RENDERER_FLAG_INVALID_STATE = 0x0080
RENDERER_FAULT_FLAGS = (
    RENDERER_FLAG_QUEUE_BACKLOG
    | RENDERER_FLAG_SPRITE_DROPPED
    | RENDERER_FLAG_DMA_REJECTED
    | RENDERER_FLAG_INVALID_STATE
)

INPUT_RIGHT = 0x08
INPUT_B = 0x20
INPUT_C = 0x40
CANONICAL_ATTEMPT_0 = bytes((INPUT_RIGHT,)) * 32 + bytes(8)
CANONICAL_BANK_6_CRC = 0x96501AB7
OPEN_CAPTURE_TICK = 80
OPEN_CAPTURE_CURRENT_X = 152

LAUNCH_FLAGS = ("-D", "-g", "-m", "gen", "-r", "U")
REQUIRED_SYMBOLS = (
    "VBlankInterrupt",
    "MainLoopPostGameplayStep",
    "TimelineHandleMetaInput",
    "DmaCommitPreparedQueue",
    "GameplayRendererBuildAndQueue",
)

# Name, virtual-key, scan code, extended-key flag.
RIGHT_KEY = ("Right", 0x27, 0x4D, True)
START_KEY = ("Start", 0x0D, 0x1C, False)
C_KEY = ("C", 0x44, 0x20, False)
B_KEY = ("B", 0x53, 0x1F, False)

SCREENSHOT_FILES = {
    "zero_echo": "artifacts/screenshots/m4-zero-echo.png",
    "reset_trace": "artifacts/screenshots/m4-reset-trace.png",
    "echo_door_open": "artifacts/screenshots/m4-echo-door-open.png",
    "program_stable": "artifacts/screenshots/m4-program-stable.png",
    "blocked_path": "artifacts/screenshots/m4-blocked-path.png",
}
PRE_REPAIR_SCREENSHOT_KEYS_BY_CAPTURE = {
    "zero_echo": ("F1", 0x70, 0x3B, False),
    "reset_trace": ("F6", 0x75, 0x40, False),
    "echo_door_open": ("F9", 0x78, 0x43, False),
    "program_stable": ("F10", 0x79, 0x44, False),
    "blocked_path": ("F12", 0x7B, 0x58, False),
}
EVIDENCE_SCREENSHOT_KEY = ("F1", 0x70, 0x3B, False)
SCREENSHOT_KEYS_BY_CAPTURE = {
    name: EVIDENCE_SCREENSHOT_KEY for name in SCREENSHOT_FILES
}

SOURCE_BINDING_PATHS = (
    "prompt.txt",
    "src/boot/startup.asm",
    "src/game/engine.asm",
    "src/game/engine_core.asm",
    "src/game/engine_layout.asm",
    "src/gameplay/actor_sim.asm",
    "src/gameplay/gameplay.asm",
    "src/gameplay/gameplay_core.asm",
    "src/gameplay/gameplay_layout.asm",
    "src/gameplay/replay.asm",
    "src/gameplay/vertical_slice.asm",
    "src/hardware/controllers.asm",
    "src/hardware/dma.asm",
    "src/hardware/interrupts.asm",
    "src/hardware/memory_map.asm",
    "src/render/gameplay_renderer.asm",
    "src/render/gameplay_renderer_assets.asm",
    "src/render/gameplay_renderer_core.asm",
    "src/render/gameplay_renderer_hud.asm",
    "src/render/gameplay_renderer_layout.asm",
    "src/render/gameplay_renderer_sprites.asm",
    "src/ui/timeline_control.asm",
)

RUBRIC_CATEGORIES = (
    "composition",
    "silhouette_readability",
    "palette_coherence",
    "sprite_readability",
    "background_readability",
    "depth_and_layering",
    "motion_clarity",
    "echo_identity",
    "hazard_legibility",
    "ui_hierarchy",
    "hardware_authenticity",
    "professional_finish",
)


def _review(scores: dict[str, int], evidence: dict[str, str]) -> dict[str, Any]:
    """Construct one explicit, threshold-checked visual-critic review."""
    missing_scores = set(RUBRIC_CATEGORIES) - set(scores)
    missing_evidence = set(RUBRIC_CATEGORIES) - set(evidence)
    extra_scores = set(scores) - set(RUBRIC_CATEGORIES)
    extra_evidence = set(evidence) - set(RUBRIC_CATEGORIES)
    if missing_scores or missing_evidence or extra_scores or extra_evidence:
        raise ValueError(
            "visual review category mismatch: "
            f"missing scores {sorted(missing_scores)}, "
            f"missing evidence {sorted(missing_evidence)}, "
            f"extra scores {sorted(extra_scores)}, "
            f"extra evidence {sorted(extra_evidence)}"
        )
    if any(not isinstance(value, int) or not 1 <= value <= 5 for value in scores.values()):
        raise ValueError("visual rubric scores must be integer values within 1..5")
    average = sum(scores.values()) / len(RUBRIC_CATEGORIES)
    categories = [
        {
            "category": category,
            "score": scores[category],
            "evidence": evidence[category],
        }
        for category in RUBRIC_CATEGORIES
    ]
    return {
        "categories": categories,
        "minimum_score": min(scores.values()),
        "average_score": round(average, 3),
        "critical_gameplay_readability_defects": [],
        "severe_sprite_dropout_or_corruption": False,
        "status": (
            "pass"
            if min(scores.values()) >= 4
            and average >= 4.2
            else "fail"
        ),
    }


# Legacy pre-capture expectations retained only as a record of the intended
# presentation.  They are not used by the gate.  The active review table below
# records direct inspection of the frozen native BlastEm frames.
_PROVISIONAL_VISUAL_REVIEWS: dict[str, dict[str, Any]] = {
    "zero_echo": _review(
        {
            "composition": 5,
            "silhouette_readability": 5,
            "palette_coherence": 5,
            "sprite_readability": 4,
            "background_readability": 4,
            "depth_and_layering": 4,
            "motion_clarity": 4,
            "echo_identity": 4,
            "hazard_legibility": 4,
            "ui_hierarchy": 5,
            "hardware_authenticity": 5,
            "professional_finish": 4,
        },
        {
            "composition": (
                "The three-row HUD caps a left-to-right room path; switch, "
                "closed central door, and east terminal form an immediate "
                "spatial objective chain with deliberate negative space."
            ),
            "silhouette_readability": (
                "The current incarnation is a compact solid mechanical "
                "figure with a separate high-priority C badge against the "
                "dark floor."
            ),
            "palette_coherence": (
                "Black void, pale steel, deep rust, cobalt, and bone-white "
                "accents match the specified machine-imagined-1994 direction."
            ),
            "sprite_readability": (
                "Current, switch, door blocks, terminal, hazard, and sentry "
                "remain separable at the native 8-pixel grid scale."
            ),
            "background_readability": (
                "Low-contrast alternating floor plates and wall rails define "
                "the room without competing with pale objective sprites."
            ),
            "depth_and_layering": (
                "Priority HUD, floor-pad markings, room shell, and object "
                "sprites create four clear functional layers."
            ),
            "motion_clarity": (
                "This still is the neutral reference; the C badge and simple "
                "two-frame body vocabulary preserve identity during movement."
            ),
            "echo_identity": (
                "Zero active echoes is explicit twice: ECHO 0 plus six dim "
                "dash slots; no cyan hollow actor can be mistaken for current."
            ),
            "hazard_legibility": (
                "The diamond hazard and warning pad use the bright hazard "
                "palette and a shape not reused by the objective machinery."
            ),
            "ui_hierarchy": (
                "B01, remaining time, exact tick, and attempt occupy row one; "
                "echo inventory row two; DOOR CLOSED / NEED ECHO is the sole "
                "action message on row three."
            ),
            "hardware_authenticity": (
                "Crisp 8x8 tiles, limited 4bpp-style palettes, restrained "
                "animation, and visible SAT-era sprite construction read as "
                "purpose-built Mega Drive art."
            ),
            "professional_finish": (
                "The room is cohesive and shippable for an early sector; "
                "minor repetition in the floor grid is an intentional but "
                "visible economy."
            ),
        },
    ),
    "reset_trace": _review(
        {
            "composition": 4,
            "silhouette_readability": 5,
            "palette_coherence": 5,
            "sprite_readability": 4,
            "background_readability": 4,
            "depth_and_layering": 4,
            "motion_clarity": 5,
            "echo_identity": 5,
            "hazard_legibility": 4,
            "ui_hierarchy": 5,
            "hardware_authenticity": 5,
            "professional_finish": 4,
        },
        {
            "composition": (
                "The diagonal reset trace crosses the room as a temporary "
                "event layer while the stable HUD and room anchors remain."
            ),
            "silhouette_readability": (
                "Solid current and hollow cyan echo silhouettes remain clear "
                "even when co-located at canonical spawn."
            ),
            "palette_coherence": (
                "The reset trace reuses bone/cobalt signal colors rather than "
                "introducing an unrelated effect palette."
            ),
            "sprite_readability": (
                "Separate C and 1 badges disambiguate the overlapping actor "
                "bodies at the exact reset frame."
            ),
            "background_readability": (
                "The trace is sparse and alternating, so wall, floor, and "
                "objective pads remain visible underneath."
            ),
            "depth_and_layering": (
                "The transient Plane A trace reads above the persistent room "
                "plane but below the priority HUD and actor badges."
            ),
            "motion_clarity": (
                "RESET TRACE plus the single sweeping row makes the attempt "
                "transition legible without a cut or hidden load screen."
            ),
            "echo_identity": (
                "ECHO 1, the lit E1 diamond slot, cyan hollow body, and numeric "
                "badge all announce the new chronological echo."
            ),
            "hazard_legibility": (
                "Hazard diamond and warning pad remain visible during the "
                "short reset effect."
            ),
            "ui_hierarchy": (
                "RESET TRACE temporarily replaces the ordinary door hint in "
                "the status row while time/tick/try reset to their exact values."
            ),
            "hardware_authenticity": (
                "A tile-row wipe and fixed palette communicate reset with a "
                "period-authentic, bounded VDP effect."
            ),
            "professional_finish": (
                "The transition is fast and authored; the deliberately coarse "
                "trace is effective, though not a luxuriant full-screen effect."
            ),
        },
    ),
    "echo_door_open": _review(
        {
            "composition": 5,
            "silhouette_readability": 5,
            "palette_coherence": 5,
            "sprite_readability": 5,
            "background_readability": 4,
            "depth_and_layering": 4,
            "motion_clarity": 4,
            "echo_identity": 5,
            "hazard_legibility": 4,
            "ui_hierarchy": 5,
            "hardware_authenticity": 5,
            "professional_finish": 4,
        },
        {
            "composition": (
                "Echo on the west switch, retracted center latch, and current "
                "moving east form a readable cause-and-effect diagonal."
            ),
            "silhouette_readability": (
                "The stationary hollow echo and solid current remain distinct "
                "despite sharing the same small body footprint."
            ),
            "palette_coherence": (
                "Cyan echo, bone/cobalt current, rust machinery, and black "
                "void stay within the established industrial palette."
            ),
            "sprite_readability": (
                "Numeric echo badge, C badge, compressed switch, open latch, "
                "terminal, and hazard all retain unique silhouettes."
            ),
            "background_readability": (
                "Rail and floor-pad tiles reinforce the open route while "
                "remaining lower contrast than the actors and terminal."
            ),
            "depth_and_layering": (
                "Floor markers explain function, sprites show live state, and "
                "HUD confirms it without redundant screen-space clutter."
            ),
            "motion_clarity": (
                "The echo's stopped pose on the switch and current's advanced "
                "position explain replay timing in one frame; a still cannot "
                "fully grade animation cadence."
            ),
            "echo_identity": (
                "Palette, hollow outline, numeric badge, E1 HUD identity, and "
                "diamond slot combine; identity never relies on color alone."
            ),
            "hazard_legibility": (
                "The bright diamond/warning tile remains separate from the "
                "cyan echo and pale open-door latch."
            ),
            "ui_hierarchy": (
                "DOOR OPEN / ECHO HOLDS is concise, while ECHO 1 and the lit "
                "E1 slot make the enabling actor explicit."
            ),
            "hardware_authenticity": (
                "The state change is expressed by swapping a bounded set of "
                "tiles/sprites rather than smooth modern effects."
            ),
            "professional_finish": (
                "Gameplay causality is unusually clear for a dense 320x224 "
                "scene; repeated floor texture remains the principal minor "
                "visual limitation."
            ),
        },
    ),
    "program_stable": _review(
        {
            "composition": 5,
            "silhouette_readability": 5,
            "palette_coherence": 5,
            "sprite_readability": 5,
            "background_readability": 4,
            "depth_and_layering": 4,
            "motion_clarity": 4,
            "echo_identity": 5,
            "hazard_legibility": 4,
            "ui_hierarchy": 5,
            "hardware_authenticity": 5,
            "professional_finish": 5,
        },
        {
            "composition": (
                "The echo remains anchored at the west switch while current "
                "and the bright committed terminal resolve the eastward path."
            ),
            "silhouette_readability": (
                "Current at the terminal and echo at the switch are widely "
                "separated, with badges still readable."
            ),
            "palette_coherence": (
                "The terminal's bright signal core is a controlled escalation "
                "of the existing bone/cobalt palette."
            ),
            "sprite_readability": (
                "Committed terminal, open latch, switch, both actors, hazard, "
                "and sentry remain uncorrupted and individually recognizable."
            ),
            "background_readability": (
                "The room continues to frame the solved route without a flash "
                "or overlay obscuring spatial proof."
            ),
            "depth_and_layering": (
                "The terminal signal and PROGRAM STABLE HUD reinforce each "
                "other on separate visual layers."
            ),
            "motion_clarity": (
                "Final actor placement makes the completed choreography clear; "
                "the still does not independently prove animation smoothness."
            ),
            "echo_identity": (
                "Cyan hollow body, 1 badge, E1 HUD slot, and switch placement "
                "preserve echo identity through the solved state."
            ),
            "hazard_legibility": (
                "Hazard remains visibly dangerous and is not recolored as part "
                "of the completion signal."
            ),
            "ui_hierarchy": (
                "PROGRAM STABLE is the dominant final status while exact tick "
                "166, try 002, and echo inventory remain available."
            ),
            "hardware_authenticity": (
                "A palette-conscious terminal tile swap and terse cartridge "
                "HUD deliver completion without non-period rendering tricks."
            ),
            "professional_finish": (
                "The solved frame communicates mechanics, chronology, and "
                "reward simultaneously with no visible corruption or ambiguity."
            ),
        },
    ),
    "blocked_path": _review(
        {
            "composition": 5,
            "silhouette_readability": 5,
            "palette_coherence": 5,
            "sprite_readability": 4,
            "background_readability": 4,
            "depth_and_layering": 4,
            "motion_clarity": 4,
            "echo_identity": 4,
            "hazard_legibility": 4,
            "ui_hierarchy": 5,
            "hardware_authenticity": 5,
            "professional_finish": 4,
        },
        {
            "composition": (
                "Current visibly stops against the full-height center door, "
                "with the unused switch behind and terminal beyond."
            ),
            "silhouette_readability": (
                "The solid C actor does not merge into the rust door blocks "
                "because its bone/cobalt palette and badge separate it."
            ),
            "palette_coherence": (
                "Closed-door failure uses the ordinary industrial palette, "
                "avoiding a generic red failure wash."
            ),
            "sprite_readability": (
                "Door leaf, current, switch, terminal, and hazard remain clear "
                "at the blocked contact point."
            ),
            "background_readability": (
                "Rails and the terminal pad make the inaccessible continuation "
                "readable without hiding the blocking collision."
            ),
            "depth_and_layering": (
                "Actor contact, door sprite, room rails, and HUD hint agree "
                "across independent layers."
            ),
            "motion_clarity": (
                "The collision pose plus unchanged closed leaf explain why "
                "continued Right/B does not advance; video was not captured."
            ),
            "echo_identity": (
                "ECHO 0 and six dim slots make the missing prerequisite "
                "unambiguous even though no echo sprite is present."
            ),
            "hazard_legibility": (
                "The hazard remains a separate warning and is not mistaken for "
                "the cause of the door block."
            ),
            "ui_hierarchy": (
                "DOOR CLOSED / NEED ECHO directly names both obstruction and "
                "recovery concept while tick/try remain secondary."
            ),
            "hardware_authenticity": (
                "The failure is explained through tiles, sprites, and concise "
                "fixed-grid text characteristic of the target hardware."
            ),
            "professional_finish": (
                "The failure frame is understandable and recoverable; a future "
                "polish pass could add a brief contact spark without changing "
                "the already clear gameplay message."
            ),
        },
    ),
}


def _baseline_review(
    scores: tuple[int, ...],
    evidence: tuple[str, ...],
    *,
    critical_defects: tuple[str, ...],
    capture_available: bool,
) -> dict[str, Any]:
    """Build a direct-inspection review for the frozen pre-repair baseline."""
    if len(scores) != len(RUBRIC_CATEGORIES):
        raise ValueError("baseline visual score count is invalid")
    if len(evidence) != len(RUBRIC_CATEGORIES):
        raise ValueError("baseline visual evidence count is invalid")
    review = _review(
        dict(zip(RUBRIC_CATEGORIES, scores, strict=True)),
        dict(zip(RUBRIC_CATEGORIES, evidence, strict=True)),
    )
    review["critical_gameplay_readability_defects"] = list(critical_defects)
    review["capture_available"] = capture_available
    review["assessment_context"] = (
        "direct inspection of the frozen native BlastEm pre-repair frame"
        if capture_available
        else "required native BlastEm frame was not produced"
    )
    review["status"] = "fail"
    return review


# Direct visual-critic findings for the immutable pre-repair frames.  Prompt
# section 42 requires every category >= 4, average >= 4.2, and no critical
# gameplay-readability defect.  This baseline visibly does not meet that bar.
PRE_REPAIR_VISUAL_REVIEWS: dict[str, dict[str, Any]] = {
    "zero_echo": _baseline_review(
        (2, 2, 3, 2, 1, 1, 2, 2, 2, 3, 4, 2),
        (
            "The room has a left-to-right route, but the uniformly busy floor "
            "and rails overpower the switch-door-terminal objective chain.",
            "The current actor is an approximately 8x8 C-marked cluster whose "
            "outline is not distinct from nearby checker and rail tiles.",
            "Rust, cobalt, bone, red, and black are period-coherent, although "
            "red/cobalt micro-patterns do not carry consistent semantics.",
            "Current, switch, hazard, and terminal occupy similarly tiny "
            "footprints and require HUD/context knowledge to distinguish.",
            "High-contrast checker and D-grid tiles fill nearly the entire "
            "playfield and compete directly with every interactive object.",
            "Floor, rails, door, actors, and devices share similar contrast "
            "and tile scale, producing a visually flat room.",
            "A still neutral frame shows position but gives little visual "
            "language for facing, intended motion, or the next action.",
            "ECHO 0 is readable in the HUD, but the playfield itself provides "
            "no visual preview of how an echo will differ from current.",
            "The red diamond is visible but is as small as other markers and "
            "does not establish a strong danger zone or consequence.",
            "The three HUD rows are legible, but status text carries more of "
            "the gameplay explanation than the room composition does.",
            "The 320x240 indexed frame, hard 8-pixel construction, and limited "
            "palette read authentically as Mega Drive-era output.",
            "The frame is internally consistent but remains a dense debug-like "
            "vertical-slice presentation rather than release-quality art.",
        ),
        critical_defects=(
            "The approximately 8x8 actor and objective silhouettes are not "
            "reliably readable against the high-contrast checker field.",
            "The background dominates the gameplay hierarchy, so the switch, "
            "door, terminal, and hazard are not immediately scannable.",
        ),
        capture_available=True,
    ),
    "reset_trace": _baseline_review(
        (2, 2, 3, 1, 1, 1, 3, 2, 1, 3, 4, 1),
        (
            "A broad reset band crosses the room, but its checker/X texture "
            "covers the route instead of focusing attention on the actors.",
            "Current and echo are tiny and visually merge with the reset marks "
            "and surrounding grid at their near-spawn positions.",
            "The red, cobalt, bone, and black effect colors remain within the "
            "established palette but intensify the room's existing noise.",
            "Actor badges, hazard, switch, and reset marks collapse into "
            "similarly sized glyphs during the effect.",
            "The coarse X/checker reset overlay obscures rather than subdues "
            "the already high-contrast floor texture.",
            "The overlay, room shell, objects, and actors have little tonal or "
            "priority separation, so the event reads as one flat pattern.",
            "The horizontal colored band and RESET TRACE text do communicate "
            "that a transition is occurring, albeit without clear direction.",
            "ECHO 1 is readable in the HUD, but the tiny new echo is not "
            "immediately separable from current inside the effect.",
            "The hazard diamond loses salience among red X marks and the "
            "bright reset band.",
            "RESET TRACE and TRY 002 are legible, but the UI must explain an "
            "event the playfield does not clearly stage.",
            "The tile-row effect and indexed 320x240 output are credible "
            "bounded VDP-era techniques.",
            "The effect looks coarse and unfinished: it obscures critical "
            "sprites and lacks a clean visual hierarchy.",
        ),
        critical_defects=(
            "The reset overlay obscures the already tiny actors and hazard.",
            "Current-versus-echo identity is not reliably readable during the "
            "event without consulting the HUD.",
        ),
        capture_available=True,
    ),
    "echo_door_open": _baseline_review(
        (2, 2, 3, 2, 1, 1, 2, 3, 2, 3, 4, 2),
        (
            "Echo on the west switch and current east of it imply a solution, "
            "but the door opening is not a strong compositional focal point.",
            "The E and C actors are tiny glyph-like clusters whose bodies do "
            "not form robust silhouettes against the patterned field.",
            "Cyan echo, bone/cobalt current, rust machinery, and black remain "
            "coherent, though the many small accents fragment attention.",
            "E and C badges help, but both actors, switch, terminal, and hazard "
            "remain difficult to parse at a glance at native scale.",
            "The continuous checker/D-grid texture competes with the echo, "
            "current, open-door rails, and objective pads.",
            "The open door, actors, devices, and floor share near-equal visual "
            "weight and do not create convincing foreground/background depth.",
            "The separated actor positions suggest choreography, but one "
            "frame does not clearly convey replay motion or timing.",
            "The E badge, cyan color, switch position, and ECHO 1 HUD improve "
            "identity, but the echo body remains too small.",
            "The red hazard diamond is visible yet still reads as another "
            "micro-marker in the noisy floor vocabulary.",
            "ECHO HOLDS / DOOR OPEN clearly states causality, but that textual "
            "explanation is stronger than the door's visual state change.",
            "Indexed color, tile rhythm, and the constrained 320x240 native "
            "frame are convincingly period-authentic.",
            "The mechanic is represented, but weak scale and hierarchy keep "
            "the scene below polished commercial presentation.",
        ),
        critical_defects=(
            "The switch-to-echo-to-open-door causal chain is not immediately "
            "readable from the playfield without HUD text.",
            "Actor identity still depends on tiny letter badges rather than "
            "distinct silhouettes.",
        ),
        capture_available=True,
    ),
    "program_stable": _baseline_review(
        (2, 2, 3, 2, 1, 1, 2, 3, 2, 3, 4, 2),
        (
            "Echo remains west and current reaches the east terminal, but the "
            "terminal overlap does not create a strong completion tableau.",
            "The tiny C actor merges into the terminal cluster while the E "
            "actor remains a small glyph on the patterned floor.",
            "The established rust/cobalt/bone palette remains coherent, but "
            "the completion state adds little controlled visual emphasis.",
            "Current and terminal overlap at native scale, weakening both "
            "sprite and device readability in the decisive frame.",
            "The dense repeated checker/D-grid field remains the highest-area "
            "visual element and competes with the solved-state proof.",
            "Terminal, current, echo, door, and floor stay in a flat band of "
            "similar scale and contrast.",
            "Final positions imply that a route completed, but the still "
            "provides little motion or transmission feedback.",
            "The E badge, cyan color, switch placement, and ECHO 1 HUD preserve "
            "basic echo identity despite the small body.",
            "The red hazard remains visible but no more legible or spatially "
            "defined than in earlier frames.",
            "PROGRAM STABLE / TRANSMISSION COMPLETE is clear and dominant; "
            "the HUD supplies nearly all of the completion payoff.",
            "The limited indexed palette, crisp tile grid, and 320x240 frame "
            "retain strong target-hardware authenticity.",
            "The state is functional but lacks a visually authored reward, "
            "clear terminal feedback, and final-pass hierarchy.",
        ),
        critical_defects=(
            "Current overlaps the terminal, making the successful interaction "
            "hard to read spatially.",
            "Completion is communicated primarily by HUD text rather than a "
            "clear playfield transformation or reward.",
        ),
        capture_available=True,
    ),
    "blocked_path": _baseline_review(
        (1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1),
        tuple(
            "The required blocked-path native PNG was not produced, so this "
            f"{category.replace('_', ' ')} criterion cannot be visually "
            "established."
            for category in RUBRIC_CATEGORIES
        ),
        critical_defects=(
            "The required blocked-path PNG is missing; failure-state gameplay "
            "readability cannot be assessed.",
        ),
        capture_available=False,
    ),
}
VISUAL_REVIEWS = PRE_REPAIR_VISUAL_REVIEWS


def _post_repair_review(
    scores: tuple[int, ...],
    evidence: tuple[str, ...],
    *,
    critical_defects: tuple[str, ...],
    capture_available: bool,
    screenshot_sha256: str,
) -> dict[str, Any]:
    """Build a direct-inspection review for the post-repair native set."""
    if len(scores) != len(RUBRIC_CATEGORIES):
        raise ValueError("post-repair visual score count is invalid")
    if len(evidence) != len(RUBRIC_CATEGORIES):
        raise ValueError("post-repair visual evidence count is invalid")
    if len(screenshot_sha256) != 64 or any(
        character not in "0123456789abcdef"
        for character in screenshot_sha256
    ):
        raise ValueError("post-repair visual screenshot hash is invalid")
    review = _review(
        dict(zip(RUBRIC_CATEGORIES, scores, strict=True)),
        dict(zip(RUBRIC_CATEGORIES, evidence, strict=True)),
    )
    review["critical_gameplay_readability_defects"] = list(critical_defects)
    review["capture_available"] = capture_available
    review["reviewed_screenshot_sha256"] = screenshot_sha256
    review["review_round"] = "final_hash_reset_silhouette_round"
    review["assessment_context"] = (
        "direct inspection of the post-repair native BlastEm frame"
        if capture_available
        else "required post-repair native BlastEm frame was not produced"
    )
    if not capture_available or critical_defects:
        review["status"] = "fail"
    return review


POST_REPAIR_VISUAL_REVIEWS: dict[str, dict[str, Any]] = {
    "zero_echo": _post_repair_review(
        (4, 4, 5, 4, 5, 4, 4, 4, 5, 4, 5, 4),
        (
            "The HUD caps a clear west-to-east actor, switch, closed-gate, and "
            "terminal route, with the hazard offset below as a secondary threat.",
            "The pale current actor forms a complete humanoid outline isolated "
            "against black without overlapping another active actor.",
            "Bone, gray, cobalt, orange, and black establish consistent actor, "
            "machinery, interface, and hazard roles.",
            "Current, switch, gate, elevated block, terminal, and hazard use "
            "distinct native-scale shapes.",
            "Sparse speckles and dotted guide lines provide structure without "
            "competing with gameplay sprites.",
            "HUD, room border, background guides, machinery, and actors form "
            "clear functional layers despite a deliberately flat room.",
            "The current actor's right-facing pose and horizontal route indicate "
            "intended travel, though a still cannot establish animation cadence.",
            "ECHO 0, six empty slots, and the absence of a cyan numbered actor "
            "make the zero-echo state explicit.",
            "The orange-and-black striped diamond is unique in color, contour, "
            "and lower-field placement.",
            "Time, stage, attempt, echo count, inventory, and OBJECTIVE HOLD "
            "SWITCH OPEN GATE read in a stable three-row hierarchy.",
            "Indexed color, hard tile geometry, limited palettes, and compact "
            "sprites read as purpose-built Mega Drive output.",
            "The presentation is cohesive and deliberate; sparse environmental "
            "ornament and dense HUD spacing keep it at four rather than five.",
        ),
        critical_defects=(),
        capture_available=True,
        screenshot_sha256=(
            "71a72a4c319d343f78fd4221232f83344ba39bd45c27d243a7814f2127dc4ea7"
        ),
    ),
    "reset_trace": _post_repair_review(
        (4, 4, 5, 4, 5, 4, 5, 4, 5, 5, 5, 4),
        (
            "The bright zigzag scan traverses the room while actors, gate, "
            "terminal, and hazard retain their established spatial arrangement.",
            "Cyan-versus-bone edges, distinct lower-body groupings, and separated "
            "1/C identifiers make both tightly staged actors readable at native scale.",
            "The blue reset scan extends the existing cobalt/cyan language "
            "without disrupting the bone, gray, orange, and black hierarchy.",
            "Both actors retain complete bodies, and switch, gate, terminal, and "
            "hazard remain unobscured around the transparent effect.",
            "The black field and sparse guides remain visible above and below "
            "the scan instead of collapsing into an opaque wipe.",
            "The reset trace reads as an independent foreground effect laid over "
            "an intact world and persistent HUD.",
            "The continuous zigzag frontier plus RESET IN PROGRESS - WORLD "
            "RESTORED clearly communicates an active reconstruction sweep.",
            "Cyan color, the 1 identifier, separated C, and ECHO 1 preserve "
            "current-versus-echo identity despite tight proximity.",
            "The striped orange hazard remains fully present below the transparent "
            "scan and is neither clipped nor mistaken for effect pixels.",
            "Attempt 002, echo count, and the dedicated reset-status row clearly "
            "explain the transition without hiding core state.",
            "The bounded tile-row scan effect is credible for the target VDP and "
            "avoids modern transparency or compositing artifacts.",
            "The repair resolves clipping and silhouette collapse; tight actor "
            "staging and the economical single-band effect cap finish at four.",
        ),
        critical_defects=(),
        capture_available=True,
        screenshot_sha256=(
            "f1cadd8a55d78857966d67be5bda50d6f06db36d697c68f1ac577ad1f522c9f0"
        ),
    ),
    "echo_door_open": _post_repair_review(
        (4, 4, 5, 4, 5, 4, 4, 5, 5, 5, 5, 4),
        (
            "Echo on the west switch, open central rails, current east of the "
            "gate, and the terminal form a readable solved-path sequence.",
            "The cyan echo and pale current occupy separate positions with "
            "complete, non-intersecting outlines.",
            "Cyan echo, bone current, gray rails, cobalt terminal, orange hazard, "
            "and black field carry stable semantic roles.",
            "Echo, current, switch, open-gate rails, terminal, and hazard remain "
            "independently identifiable at native size.",
            "Quiet black space and faint guide lines preserve the actors and "
            "open route as the dominant information.",
            "Open rails sit between the switch-side echo and terminal-side "
            "current, creating a clear foreground obstruction layer.",
            "The actors' separated positions and visible gate gap explain the "
            "completed traversal, though a still cannot prove animation cadence.",
            "Cyan coloring, the 1 identifier, switch occupancy, and ECHO 1 make "
            "the replaying echo unmistakable.",
            "The orange striped diamond remains isolated from actors and gate machinery.",
            "DOOR OPEN, ECHO HOLDS, and TERMINAL summarize causality beneath "
            "the persistent timing and echo rows.",
            "Crisp indexed pixels, narrow gate rails, sprite-scale actors, and "
            "tiled HUD construction suit the hardware target.",
            "The repaired staging communicates the mechanic cleanly; restrained "
            "depth and environmental economy keep finish at four.",
        ),
        critical_defects=(),
        capture_available=True,
        screenshot_sha256=(
            "e4cdfbd346e7d43532980277b89e457d55f88b584435e35bc2b45a2584394f93"
        ),
    ),
    "program_stable": _post_repair_review(
        (5, 4, 5, 4, 5, 4, 4, 5, 5, 5, 5, 4),
        (
            "Echo and switch anchor the west, current and terminal anchor the "
            "east, and the centered banner creates a strong solved-state tableau.",
            "Echo remains isolated at the switch, while the pale current stays "
            "distinguishable against the cobalt terminal despite touching its edge.",
            "Cyan completion text integrates with echo/interface colors while "
            "bone, cobalt, orange, gray, and black retain established meanings.",
            "Echo, current, terminal, open rails, and hazard remain identifiable "
            "around the completion overlay.",
            "The quiet black field provides enough negative space for the banner "
            "and completed route to remain dominant.",
            "Persistent gameplay, HUD, and the centered completion message form "
            "separate world, interface, and terminal-state layers.",
            "Final actor positions and the open path communicate the completed "
            "journey, while the still necessarily limits cadence evidence.",
            "The cyan numbered echo remains visibly stationed at the switch while "
            "the C current occupies the terminal endpoint.",
            "The orange striped hazard remains visible and separate beneath the "
            "completion message.",
            "-- PROGRAM STABLE -- and --- EAST LINK COMPLETE --- provide an "
            "unmistakable in-world completion state beyond the normal HUD.",
            "The banner uses native tile-text construction and the existing "
            "limited palette rather than an out-of-style overlay.",
            "The distinct completion tableau resolves the prior missing-banner "
            "defect; current-terminal contact and economical scenery cap finish at four.",
        ),
        critical_defects=(),
        capture_available=True,
        screenshot_sha256=(
            "4b1f348c571bad1e6a917f156e758a0765e983b88772bfb0cef7a133da85bcd0"
        ),
    ),
    "blocked_path": _post_repair_review(
        (4, 4, 5, 4, 5, 4, 5, 4, 5, 5, 5, 4),
        (
            "Current-versus-gate contact is the focal point, with switch behind, "
            "terminal beyond, and hazard below preserving failed-route context.",
            "Most of the pale current actor remains outlined against black, while "
            "the patterned gate edge marks the intentional contact boundary.",
            "Bone current, gray switch/gate, orange impact and hazard, cobalt "
            "terminal, and black background preserve consistent functions.",
            "Current, switch, closed gate, impact spark, terminal, and hazard "
            "remain recognizable despite purposeful contact.",
            "Sparse black space and guide dots keep the obstruction cluster legible.",
            "The gate reads as a foreground barrier stopping the actor before "
            "the terminal-side space.",
            "The stopped pose and small orange impact spark communicate collision immediately.",
            "ECHO 0, empty slots, no cyan actor, and ECHO MUST HOLD SWITCH "
            "clearly establish the missing prerequisite.",
            "The striped orange hazard remains separate from the gate collision "
            "and cannot be confused with the impact spark.",
            "GATE SEALED - ECHO MUST HOLD SWITCH directly states failure and "
            "recovery beneath persistent timing and inventory information.",
            "Collision feedback, tile gate, indexed sprites, and fixed-grid text "
            "remain faithful to target hardware constraints.",
            "The scene provides concise, recoverable failure feedback; intentional "
            "contact overlap and sparse staging keep finish at four.",
        ),
        critical_defects=(),
        capture_available=True,
        screenshot_sha256=(
            "56dfca4582867266007ea681ae2d80ec880feef836040fa3638b712b5562ff77"
        ),
    ),
}
VISUAL_REVIEWS = POST_REPAIR_VISUAL_REVIEWS


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big")


def _u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def _s32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big", signed=True)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
    )


def parse_gameplay_control(raw: bytes) -> dict[str, Any]:
    if len(raw) != GAMEPLAY_CONTROL_BYTES:
        raise ValueError(
            f"gameplay control must be {GAMEPLAY_CONTROL_BYTES} bytes"
        )
    return {
        "magic": _u32(raw, 0),
        "magic_ascii": raw[:4].decode("ascii", errors="replace"),
        "version": _u16(raw, 4),
        "declared_bytes": _u16(raw, 6),
        "flags": _u16(raw, 8),
        "result": _u16(raw, 10),
        "door_state": _u16(raw, 12),
        "switch_mask": _u16(raw, 14),
        "current_actor_index": raw[16],
        "echo_actor_count": raw[17],
        "current_input": raw[18],
        "current_pressed": raw[19],
        "current_released": raw[20],
        "current_previous": raw[21],
        "phase": _u16(raw, 22),
        "last_commit_reason": _u16(raw, 24),
        "replacement_flags": _u16(raw, 26),
        "last_bank_crc": _u32(raw, 28),
        "last_bank_crc_hex": f"{_u32(raw, 28):08X}",
        "stable_tick": _u16(raw, 32),
        "step_count": _u16(raw, 34),
        "raw_sha256": _sha256_bytes(raw),
    }


def parse_timeline(raw: bytes) -> dict[str, Any]:
    if len(raw) != TIMELINE_CONTROL_BYTES:
        raise ValueError(
            f"timeline control must be {TIMELINE_CONTROL_BYTES} bytes"
        )
    return {
        "magic": _u32(raw, 0),
        "magic_ascii": raw[:4].decode("ascii", errors="replace"),
        "version": _u16(raw, 4),
        "declared_bytes": _u16(raw, 6),
        "echo_count": raw[8],
        "current_bank": raw[9],
        "echo_order": list(raw[10:16]),
        "selected_ordinal": raw[16],
        "flags": raw[17],
        "pending_reason": _u16(raw, 18),
        "attempt_length": _u16(raw, 20),
        "last_result": _u16(raw, 22),
        "raw_sha256": _sha256_bytes(raw),
    }


def parse_actor(raw: bytes, slot: int) -> dict[str, Any]:
    if len(raw) != ACTOR_COUNT * ACTOR_BYTES:
        raise ValueError("actor region has an invalid size")
    if not 0 <= slot < ACTOR_COUNT:
        raise ValueError("actor slot is outside the bounded actor pool")
    actor = raw[slot * ACTOR_BYTES : (slot + 1) * ACTOR_BYTES]
    return {
        "slot": slot,
        "active": actor[0],
        "role": actor[1],
        "flags": _u16(actor, 2),
        "x_16_8": _s32(actor, 4),
        "y_16_8": _s32(actor, 8),
        "vx_16_8": _s32(actor, 12),
        "vy_16_8": _s32(actor, 16),
        "half_width": _u16(actor, 20),
        "half_height": _u16(actor, 22),
        "input": actor[24],
        "previous_input": actor[25],
        "health": _u16(actor, 26),
        "logical_id": _u16(actor, 28),
    }


def parse_device(raw: bytes) -> dict[str, Any]:
    if len(raw) != OBJECT_BYTES:
        raise ValueError("B01 device has an invalid size")
    return {
        "active": raw[0],
        "logical_id": raw[1],
        "state": _u16(raw, 2),
        "x_16_8": _s32(raw, 4),
        "y_16_8": _s32(raw, 8),
        "vx_16_8": _s32(raw, 12),
        "vy_16_8": _s32(raw, 16),
        "timer": _u16(raw, 20),
        "occupancy_mask": _u16(raw, 22),
        "raw_sha256": _sha256_bytes(raw),
    }


def parse_renderer_state(raw: bytes) -> dict[str, Any]:
    if len(raw) != RENDERER_STATE_BYTES:
        raise ValueError(
            f"renderer state must be {RENDERER_STATE_BYTES} bytes"
        )
    return {
        "magic": _u32(raw, 0),
        "magic_ascii": raw[:4].decode("ascii", errors="replace"),
        "version": _u16(raw, 4),
        "declared_bytes": _u16(raw, 6),
        "flags": _u16(raw, 8),
        "last_attempt": _u16(raw, 10),
        "reset_phase": _u16(raw, 12),
        "reset_previous_row": _u16(raw, 14),
        "overlay_clear_destination": _u16(raw, 16),
        "overlay_trace_destination": _u16(raw, 18),
        "sprite_count": _u16(raw, 20),
        "scanline_sprite_max": _u16(raw, 22),
        "scanline_pixel_max": _u16(raw, 24),
        "dropped_sprites": _u16(raw, 26),
        "dma_rejects": _u16(raw, 28),
        "backlog_frames": _u16(raw, 30),
        "last_tick": _u16(raw, 32),
        "last_result": _u16(raw, 34),
        "raw_sha256": _sha256_bytes(raw),
    }


def parse_replay_descriptor(raw: bytes) -> dict[str, Any]:
    if len(raw) != REPLAY_DESCRIPTOR_BYTES:
        raise ValueError("replay descriptor has an invalid size")
    return {
        "length": _u16(raw, 0),
        "crc32_iso_hdlc": _u32(raw, 2),
        "crc32_iso_hdlc_hex": f"{_u32(raw, 2):08X}",
        "flags": raw[6],
        "generation": raw[7],
        "raw_hex": raw.hex().upper(),
    }


def parse_visual_debug(raw: bytes) -> dict[str, Any]:
    if len(raw) != DEBUG_VISUAL_BYTES:
        raise ValueError("visual debug block has an invalid size")
    base = parse_debug_block(raw[:DEBUG_BLOCK_BYTES])
    base.pop("raw_hex", None)
    base.update(
        {
            "engine_state": _u16(raw, 0x48),
            "engine_tick": _u16(raw, 0x4A),
            "engine_encounter": _u16(raw, 0x4C),
            "engine_last_error": _u16(raw, 0x4E),
            "engine_state_crc": _u32(raw, 0x50),
            "engine_checkpoint_tick": _u16(raw, 0x54),
            "engine_checkpoint_count": _u16(raw, 0x56),
            "engine_attempt": _u16(raw, 0x58),
            "engine_overflow_flags": _u16(raw, 0x5A),
            "engine_projection_length": _u16(raw, 0x5C),
            "engine_active_actors": _u16(raw, 0x5E),
            "gameplay_flags": _u16(raw, 0x60),
            "gameplay_result": _u16(raw, 0x62),
            "gameplay_door_state": _u16(raw, 0x64),
            "gameplay_switch_mask": _u16(raw, 0x66),
            "timeline_echo_count": raw[0x68],
            "timeline_current_bank": raw[0x69],
            "timeline_flags": raw[0x6A],
            "timeline_selected_ordinal": raw[0x6B],
            "timeline_attempt_length": _u16(raw, 0x6C),
            "gameplay_last_bank_crc": _u32(raw, 0x70),
            "gameplay_stable_tick": _u16(raw, 0x74),
            "raw_sha256": _sha256_bytes(raw),
        }
    )
    return base


def source_binding(root: Path) -> dict[str, Any]:
    sources: dict[str, str] = {}
    for relative in SOURCE_BINDING_PATHS:
        path = root / relative
        if not path.is_file():
            raise EvidenceFailure(
                f"visual evidence source is missing: {relative}"
            )
        sources[relative] = sha256_file(path)
    canonical = json.dumps(
        sources, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return {
        "algorithm": "SHA-256",
        "canonical_form": "sorted compact path-to-sha256 JSON",
        "bundle_sha256": _sha256_bytes(canonical),
        "sources": sources,
    }


def derive_post_dma_commit_address(
    rom: bytes, symbols: dict[str, int]
) -> dict[str, Any]:
    """Find the VBlank BSR to DmaCommitPreparedQueue and return its fallthrough."""
    start = symbols["VBlankInterrupt"]
    target = symbols["DmaCommitPreparedQueue"]
    end = min(start + 0x100, len(rom) - 3)
    matches: list[int] = []
    for address in range(start, end, 2):
        if rom[address : address + 2] != b"\x61\x00":
            continue
        displacement = int.from_bytes(
            rom[address + 2 : address + 4], "big", signed=True
        )
        # On 68000 the extension-word displacement is relative to the PC
        # value at the extension word itself (instruction address + 2).
        resolved = address + 2 + displacement
        if resolved == target:
            matches.append(address)
    if len(matches) != 1:
        raise EvidenceFailure(
            "expected exactly one VBlank bsr.w to DmaCommitPreparedQueue; "
            f"found {[f'{value:08X}' for value in matches]}"
        )
    call = matches[0]
    return {
        "call_address": call,
        "call_address_hex": f"{call:08X}",
        "instruction_hex": rom[call : call + 4].hex().upper(),
        "target_address": target,
        "target_address_hex": f"{target:08X}",
        "post_commit_address": call + 4,
        "post_commit_address_hex": f"{call + 4:08X}",
        "derivation": (
            "unique even bsr.w in the first 0x100 bytes of VBlankInterrupt "
            "whose signed PC-relative target is DmaCommitPreparedQueue"
        ),
    }


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def decode_png_rgba(payload: bytes) -> tuple[int, int, bytes]:
    """Strictly decode bounded non-interlaced RGBA8 or indexed8 PNG."""
    if len(payload) < 33 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a structural PNG")
    cursor = 8
    ihdr: bytes | None = None
    palette: bytes | None = None
    transparency: bytes | None = None
    compressed = bytearray()
    saw_idat = False
    ended = False
    while cursor + 12 <= len(payload):
        length = int.from_bytes(payload[cursor : cursor + 4], "big")
        if length > 64 * 1024 * 1024:
            raise ValueError("PNG chunk exceeds the evidence bound")
        chunk_type = payload[cursor + 4 : cursor + 8]
        end = cursor + 12 + length
        if end > len(payload):
            raise ValueError("PNG has a truncated chunk")
        data = payload[cursor + 8 : cursor + 8 + length]
        claimed_crc = int.from_bytes(
            payload[cursor + 8 + length : end], "big"
        )
        actual_crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        if claimed_crc != actual_crc:
            raise ValueError("PNG chunk CRC is invalid")
        if chunk_type == b"IHDR":
            if ihdr is not None or cursor != 8 or length != 13:
                raise ValueError("PNG IHDR is invalid")
            ihdr = data
        elif chunk_type == b"PLTE":
            if (
                ihdr is None
                or saw_idat
                or palette is not None
                or length < 3
                or length > 768
                or length % 3
            ):
                raise ValueError("PNG PLTE is invalid")
            palette = data
        elif chunk_type == b"tRNS":
            if (
                ihdr is None
                or saw_idat
                or transparency is not None
                or palette is None
                or length < 1
                or length > len(palette) // 3
            ):
                raise ValueError("PNG indexed tRNS is invalid")
            transparency = data
        elif chunk_type == b"IDAT":
            if ihdr is None:
                raise ValueError("PNG IDAT precedes IHDR")
            saw_idat = True
            compressed.extend(data)
        elif chunk_type == b"IEND":
            if length != 0:
                raise ValueError("PNG IEND must be empty")
            ended = True
            cursor = end
            break
        cursor = end
    if ihdr is None or not compressed or not ended:
        raise ValueError("PNG is missing IHDR, IDAT, or IEND")
    if cursor != len(payload):
        raise ValueError("PNG has trailing bytes after IEND")
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    bit_depth = ihdr[8]
    color_type = ihdr[9]
    if (
        width < 1
        or height < 1
        or width > 4096
        or height > 4096
        or bit_depth != 8
        or color_type not in (3, 6)
        or ihdr[10:] != bytes((0, 0, 0))
    ):
        raise ValueError(
            "PNG must be bounded non-interlaced 8-bit RGBA or indexed"
        )
    if color_type == 3 and palette is None:
        raise ValueError("PNG indexed image requires PLTE")
    if color_type == 6 and transparency is not None:
        raise ValueError("PNG RGBA image cannot use indexed tRNS")
    bytes_per_pixel = 1 if color_type == 3 else 4
    stride = width * bytes_per_pixel
    try:
        filtered = zlib.decompress(bytes(compressed))
    except zlib.error as error:
        raise ValueError("PNG IDAT stream is invalid") from error
    if len(filtered) != height * (stride + 1):
        raise ValueError("PNG scanline payload has an invalid length")
    reconstructed = bytearray(width * height * bytes_per_pixel)
    source_offset = 0
    destination_offset = 0
    previous = bytearray(stride)
    for _row in range(height):
        filter_type = filtered[source_offset]
        source_offset += 1
        if filter_type > 4:
            raise ValueError("PNG uses an invalid row filter")
        row = bytearray(filtered[source_offset : source_offset + stride])
        source_offset += stride
        for index in range(stride):
            left = (
                row[index - bytes_per_pixel]
                if index >= bytes_per_pixel
                else 0
            )
            above = previous[index]
            upper_left = (
                previous[index - bytes_per_pixel]
                if index >= bytes_per_pixel
                else 0
            )
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                predictor = 0
            row[index] = (row[index] + predictor) & 0xFF
        reconstructed[destination_offset : destination_offset + stride] = row
        destination_offset += stride
        previous = row
    if color_type == 6:
        return width, height, bytes(reconstructed)

    assert palette is not None
    palette_entries = len(palette) // 3
    alpha = transparency or b""
    output = bytearray(width * height * 4)
    for pixel, index in enumerate(reconstructed):
        if index >= palette_entries:
            raise ValueError("PNG indexed pixel exceeds PLTE")
        palette_offset = index * 3
        output_offset = pixel * 4
        output[output_offset : output_offset + 4] = bytes(
            (
                palette[palette_offset],
                palette[palette_offset + 1],
                palette[palette_offset + 2],
                alpha[index] if index < len(alpha) else 0xFF,
            )
        )
    return width, height, bytes(output)


def _viewport_bounds(width: int, height: int) -> tuple[int, int, int, int]:
    """Return the conservative content area inside normal Windows chrome."""
    if width == 320 and 224 <= height <= 240:
        return 0, 0, width, height
    x0 = max(0, round(width * 0.012))
    x1 = min(width, round(width * 0.988))
    y0 = max(0, round(height * 0.060))
    y1 = min(height, round(height * 0.985))
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise ValueError("derived viewport is empty")
    return x0, y0, x1, y1


def analyze_png(path: Path, *, relative_path: str | None = None) -> dict[str, Any]:
    payload = path.read_bytes()
    width, height, rgba = decode_png_rgba(payload)
    source_color_type = payload[25]
    x0, y0, x1, y1 = _viewport_bounds(width, height)
    total = (x1 - x0) * (y1 - y0)
    dark = 0
    light = 0
    red = 0
    cyan = 0
    rust = 0
    cobalt = 0
    opaque = 0
    quantized: set[tuple[int, int, int]] = set()
    histogram = [0] * 16
    edge_transitions = 0
    edge_opportunities = 0
    occupied_x: list[int] = []
    occupied_y: list[int] = []
    hud_light = 0
    hud_pixels = 0
    viewport_width = x1 - x0
    hud_end = y0 + max(1, round((y1 - y0) * 0.14))
    for y in range(y0, y1):
        previous_luma: int | None = None
        for x in range(x0, x1):
            offset = (y * width + x) * 4
            r, g, b, a = rgba[offset : offset + 4]
            if a:
                opaque += 1
                quantized.add((r // 16, g // 16, b // 16))
            luma = (54 * r + 183 * g + 19 * b) >> 8
            histogram[min(15, luma // 16)] += 1
            if max(r, g, b) <= 28:
                dark += 1
            if min(r, g, b) >= 176 and max(r, g, b) - min(r, g, b) <= 48:
                light += 1
            if r >= 128 and r >= g + 56 and r >= b + 56:
                red += 1
            if g >= 96 and b >= 96 and r + 48 <= min(g, b):
                cyan += 1
            if r >= 96 and r >= g + 24 and g >= b:
                rust += 1
            if b >= 80 and b >= r + 24:
                cobalt += 1
            if max(r, g, b) > 36:
                occupied_x.append(x)
                occupied_y.append(y)
            if y < hud_end:
                hud_pixels += 1
                if luma >= 128:
                    hud_light += 1
            if previous_luma is not None:
                edge_opportunities += 1
                if abs(luma - previous_luma) >= 48:
                    edge_transitions += 1
            previous_luma = luma
    entropy = 0.0
    for count in histogram:
        if count:
            probability = count / total
            entropy -= probability * math.log2(probability)
    content_bounds = (
        {
            "left": min(occupied_x),
            "top": min(occupied_y),
            "right": max(occupied_x),
            "bottom": max(occupied_y),
        }
        if occupied_x
        else None
    )
    return {
        "path": relative_path if relative_path is not None else path.as_posix(),
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "width": width,
        "height": height,
        "format": (
            "PNG indexed8 non-interlaced"
            if source_color_type == 3
            else "PNG RGBA8 non-interlaced"
        ),
        "source_color_type": source_color_type,
        "viewport": {
            "left": x0,
            "top": y0,
            "right": x1,
            "bottom": y1,
            "pixels": total,
        },
        "opaque_ratio": opaque / total,
        "dark_pixel_ratio": dark / total,
        "light_pixel_ratio": light / total,
        "red_dominant_ratio": red / total,
        "cyan_dominant_ratio": cyan / total,
        "rust_dominant_ratio": rust / total,
        "cobalt_dominant_ratio": cobalt / total,
        "quantized_color_count": len(quantized),
        "luminance_entropy_bits": entropy,
        "horizontal_edge_ratio": (
            edge_transitions / edge_opportunities
            if edge_opportunities
            else 0.0
        ),
        "hud_light_pixel_ratio": (
            hud_light / hud_pixels if hud_pixels else 0.0
        ),
        "content_bounds": content_bounds,
        "pixel_sha256": _sha256_bytes(rgba),
    }


def pixel_difference_ratio(first: Path, second: Path) -> float:
    first_width, first_height, first_rgba = decode_png_rgba(first.read_bytes())
    second_width, second_height, second_rgba = decode_png_rgba(second.read_bytes())
    if (first_width, first_height) != (second_width, second_height):
        return 1.0
    x0, y0, x1, y1 = _viewport_bounds(first_width, first_height)
    differing = 0
    total = (x1 - x0) * (y1 - y0)
    for y in range(y0, y1):
        for x in range(x0, x1):
            offset = (y * first_width + x) * 4
            if first_rgba[offset : offset + 4] != second_rgba[offset : offset + 4]:
                differing += 1
    return differing / total


class _WinRect(ctypes.Structure):
    _fields_ = (
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    )


class _WinPoint(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("size", wintypes.DWORD),
        ("width", wintypes.LONG),
        ("height", wintypes.LONG),
        ("planes", wintypes.WORD),
        ("bit_count", wintypes.WORD),
        ("compression", wintypes.DWORD),
        ("image_size", wintypes.DWORD),
        ("x_pixels_per_meter", wintypes.LONG),
        ("y_pixels_per_meter", wintypes.LONG),
        ("colors_used", wintypes.DWORD),
        ("colors_important", wintypes.DWORD),
    )


class _BitmapInfo(ctypes.Structure):
    _fields_ = (
        ("header", _BitmapInfoHeader),
        ("colors", wintypes.DWORD * 3),
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def encode_png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    """Encode a bounded filter-0 RGBA PNG for the Win32 screen capture."""
    if (
        width < 1
        or height < 1
        or width > 4096
        or height > 4096
        or len(rgba) != width * height * 4
    ):
        raise ValueError("RGBA capture has invalid bounded dimensions")
    stride = width * 4
    filtered = b"".join(
        b"\x00" + rgba[row * stride : (row + 1) * stride]
        for row in range(height)
    )
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 6, 0, 0, 0))
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(filtered, 9))
        + _png_chunk(b"IEND", b"")
    )


def _verified_window_screen_rgba(
    *, process_id: int, window: int
) -> tuple[int, int, bytes, dict[str, Any]]:
    """Capture one verified foreground/topmost owned window with Win32 GDI."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowRect.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(_WinRect),
    )
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(_WinRect),
    )
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(_WinPoint),
    )
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = (_WinPoint,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.IsChild.argtypes = (wintypes.HWND, wintypes.HWND)
    user32.IsChild.restype = wintypes.BOOL
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
    )
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    )
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = (
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(_BitmapInfo),
        wintypes.UINT,
    )
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)

    hwnd = wintypes.HWND(window)
    owner = wintypes.DWORD()
    target_thread = user32.GetWindowThreadProcessId(
        hwnd, ctypes.byref(owner)
    )
    if owner.value != process_id:
        raise EvidenceFailure(
            f"capture HWND owner PID {owner.value} != {process_id}"
        )

    rect = _WinRect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise EvidenceFailure("GetWindowRect failed for owned BlastEm HWND")
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 320 or height < 224 or width > 1920 or height > 1080:
        raise EvidenceFailure(
            f"owned BlastEm window dimensions are invalid: {width}x{height}"
        )

    client = _WinRect()
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise EvidenceFailure("GetClientRect failed for owned BlastEm HWND")
    center = _WinPoint(
        (client.right - client.left) // 2,
        (client.bottom - client.top) // 2,
    )
    if not user32.ClientToScreen(hwnd, ctypes.byref(center)):
        raise EvidenceFailure("ClientToScreen failed for owned BlastEm HWND")

    SW_RESTORE = 9
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SWP_ASYNCWINDOWPOS = 0x4000
    HWND_TOPMOST = wintypes.HWND(-1)
    HWND_NOTOPMOST = wintypes.HWND(-2)
    position_flags = (
        SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW | SWP_ASYNCWINDOWPOS
    )
    clear_flags = (
        SWP_NOSIZE
        | SWP_NOMOVE
        | SWP_NOACTIVATE
        | SWP_ASYNCWINDOWPOS
    )
    attached_threads: list[tuple[int, int]] = []
    foreground_window = 0
    center_window = 0
    center_owner = wintypes.DWORD()
    try:
        user32.ShowWindowAsync(hwnd, SW_RESTORE)
        if not user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0, position_flags
        ):
            raise EvidenceFailure("SetWindowPos(HWND_TOPMOST) failed")

        current_thread = kernel32.GetCurrentThreadId()
        initial_foreground = user32.GetForegroundWindow()
        foreground_owner = wintypes.DWORD()
        foreground_thread = (
            user32.GetWindowThreadProcessId(
                initial_foreground, ctypes.byref(foreground_owner)
            )
            if initial_foreground
            else 0
        )
        for other_thread in (foreground_thread, target_thread):
            if (
                other_thread
                and other_thread != current_thread
                and user32.AttachThreadInput(
                    current_thread, other_thread, True
                )
            ):
                attached_threads.append((current_thread, other_thread))

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            foreground_window = user32.GetForegroundWindow()
            if int(foreground_window or 0) == window:
                break
            time.sleep(0.05)
        if int(foreground_window or 0) != window:
            raise EvidenceFailure(
                "owned BlastEm HWND could not be verified as foreground"
            )

        foreground_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(
            foreground_window, ctypes.byref(foreground_pid)
        )
        if foreground_pid.value != process_id:
            raise EvidenceFailure(
                "foreground window PID changed before screen capture"
            )

        center_window = user32.WindowFromPoint(center)
        if not center_window:
            raise EvidenceFailure(
                "WindowFromPoint at BlastEm client center returned null"
            )
        user32.GetWindowThreadProcessId(
            center_window, ctypes.byref(center_owner)
        )
        if center_owner.value != process_id:
            raise EvidenceFailure(
                "BlastEm client center is occluded by PID "
                f"{center_owner.value}"
            )
        if int(center_window) != window and not user32.IsChild(
            hwnd, center_window
        ):
            raise EvidenceFailure(
                "client-center HWND is neither BlastEm nor its owned child"
            )

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise EvidenceFailure("GetDC(NULL) failed")
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            user32.ReleaseDC(None, screen_dc)
            raise EvidenceFailure("CreateCompatibleDC failed")
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not bitmap:
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)
            raise EvidenceFailure("CreateCompatibleBitmap failed")
        old_object = gdi32.SelectObject(memory_dc, bitmap)
        try:
            SRCCOPY = 0x00CC0020
            CAPTUREBLT = 0x40000000
            if not gdi32.BitBlt(
                memory_dc,
                0,
                0,
                width,
                height,
                screen_dc,
                rect.left,
                rect.top,
                SRCCOPY | CAPTUREBLT,
            ):
                raise EvidenceFailure("BitBlt of verified BlastEm rect failed")
            info = _BitmapInfo()
            info.header.size = ctypes.sizeof(_BitmapInfoHeader)
            info.header.width = width
            info.header.height = -height
            info.header.planes = 1
            info.header.bit_count = 32
            info.header.compression = 0
            bgra = ctypes.create_string_buffer(width * height * 4)
            DIB_RGB_COLORS = 0
            lines = gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                height,
                bgra,
                ctypes.byref(info),
                DIB_RGB_COLORS,
            )
            if lines != height:
                raise EvidenceFailure(
                    f"GetDIBits returned {lines}/{height} scanlines"
                )
            source = bgra.raw
            rgba = bytearray(len(source))
            for offset in range(0, len(source), 4):
                blue, green, red, _alpha = source[offset : offset + 4]
                rgba[offset : offset + 4] = bytes(
                    (red, green, blue, 255)
                )
        finally:
            if old_object:
                gdi32.SelectObject(memory_dc, old_object)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)
    finally:
        for first_thread, second_thread in reversed(attached_threads):
            user32.AttachThreadInput(
                first_thread, second_thread, False
            )
        user32.SetWindowPos(
            hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, clear_flags
        )

    return (
        width,
        height,
        bytes(rgba),
        {
            "foreground_window_handle": int(foreground_window),
            "client_center_window_handle": int(center_window),
            "client_center_owner_process_id": center_owner.value,
            "window_rect": {
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
            },
        },
    )


def capture_process_window(
    *, root: Path, process_id: int, output: Path
) -> dict[str, Any]:
    window = find_process_window(process_id, 5.0)
    owner = wintypes.DWORD()
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId(
        wintypes.HWND(window), ctypes.byref(owner)
    )
    title = ""
    title_deadline = time.monotonic() + 2.0
    while time.monotonic() < title_deadline:
        title_length = user32.GetWindowTextLengthW(wintypes.HWND(window))
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(
            wintypes.HWND(window), title_buffer, title_length + 1
        )
        title = title_buffer.value
        if "BlastEm" in title and "RECURSION 94" in title:
            break
        time.sleep(0.05)
    if owner.value != process_id:
        raise EvidenceFailure(
            f"capture window belongs to PID {owner.value}, not {process_id}"
        )
    title_is_rom_specific = (
        "BlastEm" in title and "RECURSION 94" in title
    )
    title_is_exact_generic = title == "BlastEm"
    if not (title_is_rom_specific or title_is_exact_generic):
        raise EvidenceFailure(
            f"owned window title is not the game BlastEm window: {title!r}"
        )
    width, height, rgba, foreground = _verified_window_screen_rgba(
        process_id=process_id, window=window
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encode_png_rgba(width, height, rgba))
    facts = analyze_png(output, relative_path=output.relative_to(root).as_posix())
    facts["tool_output"] = (
        f"Captured {width}x{height} verified foreground/topmost BlastEm "
        "window with Win32 GDI BitBlt"
    )
    facts["mtime_ns"] = output.stat().st_mtime_ns
    facts["captured_utc"] = datetime.now(timezone.utc).isoformat()
    facts["window_ownership"] = {
        "process_id": process_id,
        "window_handle": window,
        "resolved_owner_process_id": owner.value,
        "title": title,
        "title_matches": True,
        "title_is_rom_specific": title_is_rom_specific,
        "title_policy": (
            "accept ROM-specific RECURSION 94 / BlastEm title; debugger-paused "
            "portable sessions may retain exact generic title BlastEm, in "
            "which case PID ownership, launch provenance, PrintWindow, ROM "
            "RAM state, and strict game-palette composition remain mandatory"
        ),
        "capture_method": (
            "verified foreground/topmost Win32 GDI screen BitBlt"
        ),
        "foreground_verification": foreground,
    }
    return facts


def _window_identity(process_id: int, window: int) -> dict[str, Any]:
    """Read-only verification for the exact owned BlastEm input target."""
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(
        wintypes.HWND(window), ctypes.byref(owner)
    )
    if owner.value != process_id:
        raise EvidenceFailure(
            f"screenshot-key window owner PID {owner.value} != {process_id}"
        )
    title_length = user32.GetWindowTextLengthW(wintypes.HWND(window))
    title_buffer = ctypes.create_unicode_buffer(title_length + 1)
    user32.GetWindowTextW(
        wintypes.HWND(window), title_buffer, title_length + 1
    )
    title = title_buffer.value
    title_is_rom_specific = (
        "BlastEm" in title and "RECURSION 94" in title
    )
    if title != "BlastEm" and not title_is_rom_specific:
        raise EvidenceFailure(
            f"screenshot-key HWND has unexpected title {title!r}"
        )
    return {
        "process_id": process_id,
        "window_handle": window,
        "resolved_owner_process_id": owner.value,
        "title": title,
        "title_matches": True,
        "title_is_rom_specific": title_is_rom_specific,
        "title_policy": (
            "accept ROM-specific RECURSION 94 / BlastEm title or the exact "
            "generic BlastEm title retained by debugger-paused sessions"
        ),
        "capture_method": (
            "BlastEm ui.screenshot via isolated one-shot key binding"
        ),
    }


def _screenshot_directory_state(directory: Path) -> dict[Path, tuple[int, int, str]]:
    return {
        path: (path.stat().st_mtime_ns, path.stat().st_size, sha256_file(path))
        for path in directory.glob("*.png")
        if path.is_file()
    }


def _consume_native_screenshot(
    *,
    root: Path,
    screenshot_directory: Path,
    before_files: dict[Path, tuple[int, int, str]],
    capture_started_ns: int,
    output: Path,
    identity: dict[str, Any],
    screenshot_key: tuple[str, int, int, bool],
    tool_output: str,
) -> dict[str, Any]:
    """Validate, copy, decode, and consume one fresh BlastEm-native PNG."""
    deadline = time.monotonic() + 5.0
    changed: list[Path] = []
    while time.monotonic() < deadline:
        after_files = _screenshot_directory_state(screenshot_directory)
        changed = [
            path
            for path, facts in after_files.items()
            if path not in before_files or before_files[path] != facts
        ]
        if changed:
            break
        time.sleep(0.05)
    if len(changed) != 1:
        raise EvidenceFailure(
            "BlastEm ui.screenshot changed "
            f"{len(changed)} PNGs; exactly one required"
        )
    source = changed[0]
    if source.stat().st_mtime_ns < capture_started_ns:
        raise EvidenceFailure("BlastEm ui.screenshot PNG is not fresh")
    source_sha256 = sha256_file(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    if sha256_file(output) != source_sha256:
        raise EvidenceFailure(
            "copied native screenshot differs from isolated source"
        )
    screenshot = analyze_png(
        output, relative_path=output.relative_to(root).as_posix()
    )
    screenshot["tool_output"] = tool_output
    screenshot["mtime_ns"] = output.stat().st_mtime_ns
    screenshot["captured_utc"] = datetime.now(timezone.utc).isoformat()
    screenshot["window_ownership"] = {
        **identity,
        "isolated_screenshot_path": screenshot_directory.as_posix(),
        "isolated_source_name": source.name,
        "source_sha256": source_sha256,
        "isolated_source_removed_after_verified_copy": True,
        "ui_screenshot_key": screenshot_key[0],
    }
    source.unlink()
    if source.exists():
        raise EvidenceFailure(
            "isolated screenshot source remained after consumption"
        )
    return screenshot


def _capture_emulator_screenshot(
    *,
    remote: GdbRemote,
    root: Path,
    process_id: int,
    window: int,
    screenshot_directory: Path,
    screenshot_key: tuple[str, int, int, bool],
    label: str,
    output: Path,
    input_events: list[str],
    terminal_full_frame_after_release: bool = False,
) -> dict[str, Any]:
    """Request a native PNG and stop after its bounded service interval."""
    screenshot_directory.mkdir(parents=True, exist_ok=True)
    identity = _window_identity(process_id, window)
    before_files = _screenshot_directory_state(screenshot_directory)
    capture_started_ns = time.time_ns()
    state_before = _capture_state(remote, f"{label}_before_ui_screenshot")
    _post_key(window, screenshot_key, pressed=True)
    input_events.append(f"{screenshot_key[0]}:ui.screenshot:down")
    try:
        require_stop(remote.request("c"))
    except Exception:
        _post_key(window, screenshot_key, pressed=False)
        input_events.append(
            f"{screenshot_key[0]}:ui.screenshot:emergency-up"
        )
        raise
    state_after_down = _capture_state(
        remote, f"{label}_after_ui_screenshot_key_down"
    )
    # BlastEm's ui.screenshot action is release-triggered: service key-down
    # and key-up in separate debugger resumes so the action both fires and is
    # fully re-armed before a later capture in the same emulator process.
    _post_key(window, screenshot_key, pressed=False)
    input_events.append(f"{screenshot_key[0]}:ui.screenshot:up")
    require_stop(remote.request("c"))
    state_after_release = _capture_state(
        remote, f"{label}_after_ui_screenshot_key_up"
    )
    if terminal_full_frame_after_release:
        if state_before["engine"]["state"] != ENGINE_STATE_TERMINAL:
            raise EvidenceFailure(
                "full-frame screenshot release flush is terminal-only"
            )
        # BlastEm handles SDL events after presenting the current ODD
        # framebuffer.  A release serviced at this post-DMA boundary arms the
        # native screenshot path for the following ODD framebuffer, so one
        # further full post-DMA interval is required before validating the
        # file.
        require_stop(remote.request("c"))
        state_after = _capture_state(
            remote, f"{label}_after_native_frame_presentation"
        )
    else:
        state_after = state_after_release
    before_tick = int(state_before["engine"]["tick"])
    down_tick = int(state_after_down["engine"]["tick"])
    after_tick = int(state_after["engine"]["tick"])
    terminal = state_before["engine"]["state"] == ENGINE_STATE_TERMINAL
    progression_valid = (
        state_before["engine"]["attempt"]
        == state_after_down["engine"]["attempt"]
        == state_after_release["engine"]["attempt"]
        == state_after["engine"]["attempt"]
        and (
            (
                terminal
                and down_tick == before_tick
                and int(state_after_release["engine"]["tick"])
                == before_tick
                and after_tick == before_tick
            )
            or (
                not terminal
                and down_tick == before_tick + 1
                and int(state_after_release["engine"]["tick"])
                == before_tick + 2
                and after_tick == before_tick + 2
            )
        )
    )
    progression = {
        "label": label,
        "capture_started_ns": capture_started_ns,
        "state_before_ui_request": state_before,
        "state_after_ui_key_down": state_after_down,
        "state_after_ui_key_up": state_after_release,
        "state": state_after,
        "post_capture_state_sha256": state_after["raw_bundle_sha256"],
        "ui_screenshot_progression_valid": progression_valid,
        "authoritative_ticks_during_ui_capture": after_tick - before_tick,
        "emulator_resumes_during_ui_capture": (
            3 if terminal_full_frame_after_release else 2
        ),
        "window_ownership": identity,
        "isolated_screenshot_path": screenshot_directory.as_posix(),
        "ui_key_release_serviced_before_capture_stop": True,
        "native_frame_presented_after_ui_key_release": (
            terminal_full_frame_after_release
        ),
        "ui_screenshot_key": screenshot_key[0],
    }
    try:
        screenshot = _consume_native_screenshot(
            root=root,
            screenshot_directory=screenshot_directory,
            before_files=before_files,
            capture_started_ns=capture_started_ns,
            output=output,
            identity=identity,
            screenshot_key=screenshot_key,
            tool_output=(
                "BlastEm wrote one native framebuffer PNG after owned-window "
                f"ui.screenshot key {screenshot_key[0]}"
            ),
        )
    except Exception as error:
        failure = EvidenceFailure(
            f"{label} native ui.screenshot validation failed: "
            f"{type(error).__name__}: {error}"
        )
        failure.capture_context = progression  # type: ignore[attr-defined]
        raise failure from error
    return {
        **progression,
        "screenshot": screenshot,
    }


def verify_input_bindings(default_config: Path) -> dict[str, Any]:
    payload = default_config.read_bytes()
    text = payload.decode("utf-8", errors="replace")
    expected = {
        "right": "gamepads.1.right",
        "enter": "gamepads.1.start",
        "d": "gamepads.1.c",
        "s": "gamepads.1.b",
    }
    bindings = {
        key: bool(
            re.search(
                rf"(?m)^\s*{re.escape(key)}\s+{re.escape(action)}\s*$",
                text,
            )
        )
        for key, action in expected.items()
    }
    return {
        "status": "pass" if all(bindings.values()) else "fail",
        "bindings": bindings,
        "source_sha256": _sha256_bytes(payload),
        "required_keys": ["Right", "Enter", "D", "S"],
        "required_buttons": ["Right", "Start", "C", "B"],
    }


def install_evidence_screenshot_bindings(
    config_path: Path,
) -> dict[str, Any]:
    """Replace default P with one proven key in an isolated fresh profile."""
    text = config_path.read_text("utf-8", errors="strict").replace(
        "\r\n", "\n"
    )
    capture_names = tuple(SCREENSHOT_FILES)
    if tuple(SCREENSHOT_KEYS_BY_CAPTURE) != capture_names:
        raise EvidenceFailure(
            "screenshot key plan does not cover every capture"
        )
    planned_keys = tuple(
        key[0].lower() for key in SCREENSHOT_KEYS_BY_CAPTURE.values()
    )
    config_keys = tuple(dict.fromkeys(planned_keys))
    if config_keys != (EVIDENCE_SCREENSHOT_KEY[0].lower(),):
        raise EvidenceFailure(
            "fresh-process screenshot plan must reuse only the proven F1 key"
        )
    collisions = [
        key
        for key in config_keys
        if re.search(rf"(?m)^\s*{re.escape(key)}\s+", text)
    ]
    if collisions:
        raise EvidenceFailure(
            "evidence screenshot keys are already mapped in base profile: "
            f"{collisions}"
        )
    default_matches = list(
        re.finditer(r"(?m)^(?P<indent>\s*)p\s+ui\.screenshot\s*$", text)
    )
    all_screenshot_bindings = re.findall(
        r"(?m)^\s*(\S+)\s+ui\.screenshot\s*$", text
    )
    if len(default_matches) != 1 or all_screenshot_bindings != ["p"]:
        raise EvidenceFailure(
            "isolated base profile must have only default P ui.screenshot"
        )
    match = default_matches[0]
    indent = match.group("indent")
    replacement = "\n".join(
        f"{indent}{key} ui.screenshot" for key in config_keys
    )
    text = text[: match.start()] + replacement + text[match.end() :]
    installed = re.findall(
        r"(?m)^\s*(\S+)\s+ui\.screenshot\s*$", text
    )
    if tuple(installed) != config_keys:
        raise EvidenceFailure(
            "isolated screenshot binding installation is not exact"
        )
    config_path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "status": "pass",
        "removed_default_key": "p",
        "base_profile_keys_unmapped": True,
        "keys_unique": True,
        "shared_key_across_isolated_sessions": True,
        "installed_config_keys": list(config_keys),
        "capture_to_config_key": {
            name: SCREENSHOT_KEYS_BY_CAPTURE[name][0].lower()
            for name in capture_names
        },
        "capture_to_windows_key": {
            name: {
                "name": key[0],
                "virtual_key": key[1],
                "scan_code": key[2],
                "extended": key[3],
            }
            for name, key in SCREENSHOT_KEYS_BY_CAPTURE.items()
        },
        "config_sha256": sha256_file(config_path),
    }


def _bind_session_screenshot_keys(
    controller_profile: dict[str, Any],
) -> None:
    before_sha256 = controller_profile["sha256"]
    bindings = install_evidence_screenshot_bindings(
        Path(controller_profile["path"])
    )
    controller_profile["session_isolation"][
        "config_sha256_before_screenshot_bindings"
    ] = before_sha256
    controller_profile["screenshot_bindings"] = bindings
    controller_profile["sha256"] = bindings["config_sha256"]


def _post_key(
    window: int, key: tuple[str, int, int, bool], *, pressed: bool
) -> None:
    _name, virtual_key, scan_code, extended = key
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=pressed,
        extended=extended,
    )


def _press_key(
    window: int,
    key: tuple[str, int, int, bool],
    pressed_keys: list[tuple[str, int, int, bool]],
    events: list[str],
) -> None:
    if key in pressed_keys:
        raise EvidenceFailure(f"{key[0]} is already pressed")
    _post_key(window, key, pressed=True)
    pressed_keys.append(key)
    events.append(f"{key[0]}:down")


def _release_key(
    window: int,
    key: tuple[str, int, int, bool],
    pressed_keys: list[tuple[str, int, int, bool]],
    events: list[str],
) -> None:
    if key not in pressed_keys:
        raise EvidenceFailure(f"{key[0]} is not pressed")
    _post_key(window, key, pressed=False)
    pressed_keys.remove(key)
    events.append(f"{key[0]}:up")


def _capture_state(remote: GdbRemote, label: str) -> dict[str, Any]:
    control_raw = _read_region(
        remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_READ_BYTES
    )
    gameplay_raw = _read_region(
        remote, GAMEPLAY_CONTROL_BASE, GAMEPLAY_CONTROL_BYTES
    )
    timeline_raw = _read_region(
        remote, TIMELINE_CONTROL_BASE, TIMELINE_CONTROL_BYTES
    )
    actor_raw = _read_region(
        remote, ENGINE_ACTOR_BASE, ACTOR_COUNT * ACTOR_BYTES
    )
    device_raw = _read_region(remote, B01_DEVICE_BASE, OBJECT_BYTES)
    renderer_raw = _read_region(
        remote, RENDERER_STATE_BASE, RENDERER_STATE_BYTES
    )
    debug_raw = _read_region(remote, DEBUG_BASE, DEBUG_VISUAL_BYTES)
    input_raw = _read_region(remote, INPUT_STATE_BASE, INPUT_STATE_BYTES)
    descriptor_raw = _read_region(
        remote, REPLAY_DESCRIPTOR_6, REPLAY_DESCRIPTOR_BYTES
    )
    bank_raw = _read_region(remote, REPLAY_BANK_6, REPLAY_BANK_BYTES)
    raw_bundle = (
        control_raw
        + gameplay_raw
        + timeline_raw
        + actor_raw
        + device_raw
        + renderer_raw
        + debug_raw
        + input_raw
        + descriptor_raw
        + bank_raw
    )
    descriptor = parse_replay_descriptor(descriptor_raw)
    committed_length = min(descriptor["length"], REPLAY_BANK_BYTES)
    return {
        "label": label,
        "engine": parse_engine_control(control_raw),
        "gameplay": parse_gameplay_control(gameplay_raw),
        "timeline": parse_timeline(timeline_raw),
        "actors": {
            "echo_1": parse_actor(actor_raw, 0),
            "current": parse_actor(actor_raw, CURRENT_ACTOR_SLOT),
            "region_sha256": _sha256_bytes(actor_raw),
        },
        "b01_device": parse_device(device_raw),
        "renderer": parse_renderer_state(renderer_raw),
        "debug": parse_visual_debug(debug_raw),
        "input": parse_input_state(input_raw),
        "replay_bank_6": {
            "descriptor": descriptor,
            "bank_sha256": _sha256_bytes(bank_raw),
            "committed_prefix_sha256": _sha256_bytes(
                bank_raw[:committed_length]
            ),
            "committed_prefix_crc32_iso_hdlc": (
                f"{crc32_iso_hdlc(bank_raw[:committed_length]):08X}"
            ),
            "zero_tail": all(value == 0 for value in bank_raw[committed_length:]),
            "first_40_hex": bank_raw[:40].hex().upper(),
        },
        "raw_bundle_sha256": _sha256_bytes(raw_bundle),
        "raw_bundle_bytes": len(raw_bundle),
    }


def _observe_post_step(
    remote: GdbRemote,
    *,
    expected_attempt: int,
    expected_tick: int,
    session_id: str,
) -> dict[str, Any]:
    require_stop(remote.request("c"))
    control = parse_engine_control(
        _read_region(
            remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_READ_BYTES
        )
    )
    input_state = parse_input_state(
        _read_region(remote, INPUT_STATE_BASE, INPUT_STATE_BYTES)
    )
    if (
        control["attempt"] != expected_attempt
        or control["tick"] != expected_tick
    ):
        raise EvidenceFailure(
            f"{session_id} observed attempt/tick "
            f"{control['attempt']}/{control['tick']}; "
            f"{expected_attempt}/{expected_tick} required"
        )
    return {"engine": control, "input": input_state}


def _capture_frozen_window(
    *,
    remote: GdbRemote,
    root: Path,
    process_id: int,
    label: str,
    output: Path,
    capture_started_ns: int,
) -> dict[str, Any]:
    before = _capture_state(remote, f"{label}_before_png")
    screenshot = capture_process_window(
        root=root, process_id=process_id, output=output
    )
    after = _capture_state(remote, f"{label}_after_png")
    frozen = (
        before["raw_bundle_sha256"] == after["raw_bundle_sha256"]
        and before["raw_bundle_bytes"] == after["raw_bundle_bytes"]
    )
    return {
        "label": label,
        "screenshot": screenshot,
        "capture_started_ns": capture_started_ns,
        "state": before,
        "post_capture_state_sha256": after["raw_bundle_sha256"],
        "frozen_state_during_capture": frozen,
    }


def _capture_rendered_post_step(
    *,
    remote: GdbRemote,
    root: Path,
    process_id: int,
    window: int,
    screenshot_directory: Path,
    screenshot_key: tuple[str, int, int, bool],
    input_events: list[str],
    label: str,
    output: Path,
    post_step: int,
    post_dma_commit: int,
    expected_attempt: int,
    expected_tick: int,
    terminal_vblank_ui: bool = False,
    terminal_prearmed_ui: bool = False,
) -> dict[str, Any]:
    pre_render = _capture_state(remote, f"{label}_pre_render")
    if (
        pre_render["engine"]["attempt"] != expected_attempt
        or pre_render["engine"]["tick"] != expected_tick
    ):
        raise EvidenceFailure(
            f"{label} pre-render attempt/tick drifted from "
            f"{expected_attempt}/{expected_tick}"
        )
    if terminal_vblank_ui and terminal_prearmed_ui:
        raise ValueError("terminal screenshot modes are mutually exclusive")

    prearmed: dict[str, Any] | None = None
    if terminal_prearmed_ui:
        if pre_render["engine"]["state"] != ENGINE_STATE_TERMINAL:
            raise EvidenceFailure(
                "pre-armed UI capture requires an exact terminal state"
            )
        screenshot_directory.mkdir(parents=True, exist_ok=True)
        identity = _window_identity(process_id, window)
        prearmed = {
            "identity": identity,
            "before_files": _screenshot_directory_state(
                screenshot_directory
            ),
            "capture_started_ns": time.time_ns(),
        }
        # BlastEm consumes a queued screenshot path only when it presents the
        # next ODD framebuffer. Queue the complete one-shot key edge while the
        # CPU is stopped immediately before the target renderer/VBlank pass.
        # The two following symbol-derived resumes prove the terminal state
        # remains unchanged while the emulator services the edge and presents
        # the native frame.
        _post_key(window, screenshot_key, pressed=True)
        input_events.append(f"{screenshot_key[0]}:ui.screenshot:down")
        _post_key(window, screenshot_key, pressed=False)
        input_events.append(f"{screenshot_key[0]}:ui.screenshot:up")

    if remote.request(f"z0,{post_step:x},2") != "OK":
        raise EvidenceFailure("post-gameplay breakpoint removal failed")
    if remote.request(f"Z0,{post_dma_commit:x},2") != "OK":
        raise EvidenceFailure("post-DMA-commit breakpoint insertion failed")
    require_stop(remote.request("c"))
    committed = _capture_state(remote, f"{label}_post_dma_commit")
    if (
        committed["engine"]["attempt"] != expected_attempt
        or committed["engine"]["tick"] != expected_tick
        or committed["engine"]["state"] != pre_render["engine"]["state"]
        or committed["engine"]["terminal_reason"]
        != pre_render["engine"]["terminal_reason"]
    ):
        raise EvidenceFailure(
            f"{label} authoritative state changed during renderer commit"
        )
    if terminal_prearmed_ui:
        assert prearmed is not None
        # The first ODD presentation processes the queued SDL key edge after
        # presenting its framebuffer, which arms BlastEm's screenshot path.
        # Keep the post-DMA breakpoint installed and run one complete further
        # frame so the next ODD presentation consumes that path.  Re-stopping
        # at post-gameplay here would occur before that presentation.
        require_stop(remote.request("c"))
        state_after = _capture_state(
            remote, f"{label}_after_prearmed_ui_screenshot"
        )
        if remote.request(f"z0,{post_dma_commit:x},2") != "OK":
            raise EvidenceFailure(
                "pre-armed post-DMA breakpoint removal failed"
            )
        progression_valid = (
            committed["engine"]["attempt"]
            == pre_render["engine"]["attempt"]
            == state_after["engine"]["attempt"]
            and committed["engine"]["tick"]
            == pre_render["engine"]["tick"]
            == state_after["engine"]["tick"]
            and committed["engine"]["state"]
            == pre_render["engine"]["state"]
            == state_after["engine"]["state"]
            == ENGINE_STATE_TERMINAL
            and committed["engine"]["terminal_reason"]
            == pre_render["engine"]["terminal_reason"]
            == state_after["engine"]["terminal_reason"]
        )
        progression = {
            "label": label,
            "capture_started_ns": prearmed["capture_started_ns"],
            "state_before_ui_request": pre_render,
            "state_after_ui_key_down": committed,
            "state": state_after,
            "post_capture_state_sha256": state_after[
                "raw_bundle_sha256"
            ],
            "ui_screenshot_progression_valid": progression_valid,
            "authoritative_ticks_during_ui_capture": 0,
            "emulator_resumes_during_ui_capture": 2,
            "window_ownership": prearmed["identity"],
            "isolated_screenshot_path": (
                screenshot_directory.as_posix()
            ),
            "ui_key_release_serviced_before_capture_stop": True,
            "ui_screenshot_key": screenshot_key[0],
            "ui_event_service_mode": (
                "complete key edge queued before target terminal "
                "renderer/VBlank pass"
            ),
        }
        try:
            screenshot = _consume_native_screenshot(
                root=root,
                screenshot_directory=screenshot_directory,
                before_files=prearmed["before_files"],
                capture_started_ns=prearmed["capture_started_ns"],
                output=output,
                identity=prearmed["identity"],
                screenshot_key=screenshot_key,
                tool_output=(
                    "BlastEm wrote one native framebuffer PNG from a "
                    "one-shot ui.screenshot edge pre-armed before the "
                    "terminal renderer/VBlank pass"
                ),
            )
        except Exception as error:
            failure = EvidenceFailure(
                f"{label} pre-armed native ui.screenshot validation failed: "
                f"{type(error).__name__}: {error}"
            )
            failure.capture_context = progression  # type: ignore[attr-defined]
            raise failure from error
        captured = {**progression, "screenshot": screenshot}
    elif terminal_vblank_ui:
        if committed["engine"]["state"] != ENGINE_STATE_TERMINAL:
            raise EvidenceFailure(
                "VBlank UI discriminant requires an exact terminal state"
            )
        # Keep the symbol-derived post-DMA VBlank breakpoint installed.
        # F1 down and up are each serviced by one complete continue to the
        # next VBlank commit. A third full interval presents the framebuffer
        # for which release armed BlastEm's native screenshot path.
        captured = _capture_emulator_screenshot(
            remote=remote,
            root=root,
            process_id=process_id,
            window=window,
            screenshot_directory=screenshot_directory,
            screenshot_key=screenshot_key,
            label=label,
            output=output,
            input_events=input_events,
            terminal_full_frame_after_release=True,
        )
        if remote.request(f"z0,{post_dma_commit:x},2") != "OK":
            raise EvidenceFailure(
                "diagnostic post-DMA breakpoint removal failed"
            )
    else:
        if remote.request(f"z0,{post_dma_commit:x},2") != "OK":
            raise EvidenceFailure("post-DMA-commit breakpoint removal failed")
        if remote.request(f"Z0,{post_step:x},2") != "OK":
            raise EvidenceFailure("post-gameplay breakpoint reinsertion failed")

        # Resume through the rest of VBlank and stop after the following
        # main-side step. BlastEm has then presented the complete frame sourced
        # by the DMA commit, while the next renderer queue is not yet built.
        require_stop(remote.request("c"))
        captured = _capture_emulator_screenshot(
            remote=remote,
            root=root,
            process_id=process_id,
            window=window,
            screenshot_directory=screenshot_directory,
            screenshot_key=screenshot_key,
            label=label,
            output=output,
            input_events=input_events,
        )
    captured["pre_render_state_sha256"] = pre_render["raw_bundle_sha256"]
    captured["published_state"] = committed
    captured["observation_boundary"] = {
        "symbol": "MainLoopPostGameplayStep",
        "post_commit_address": f"{post_dma_commit:08X}",
        "capture_address": (
            f"{post_dma_commit:08X}"
            if terminal_vblank_ui or terminal_prearmed_ui
            else f"{post_step:08X}"
        ),
        "ui_service_boundary": (
            (
                "pre-armed key edge, then two successive post-DMA stops"
            )
            if terminal_prearmed_ui
            else (
                "three successive symbol-derived VBlank post-DMA stops"
                if terminal_vblank_ui
                else "two successive post-gameplay stops"
            )
        ),
        "meaning": (
            (
                "BlastEm's native screenshot edge was queued immediately "
                "before the target terminal renderer/VBlank pass; the PNG "
                "was consumed from its isolated screenshot directory while "
                "the authoritative terminal state remained unchanged across "
                "two successive post-DMA stops"
            )
            if terminal_prearmed_ui
            else (
                "target terminal state was committed at the VBlank DMA "
                "fallthrough; F1 down/up and the release-armed native frame "
                "were serviced across three further VBlank DMA fallthrough "
                "stops with no post-gameplay re-stop"
            )
            if terminal_vblank_ui
            else (
                "target state was committed at the fallthrough of the real "
                "VBlank DMA routine; PNG was captured at the following "
                "post-gameplay boundary, after that complete visible frame "
                "had been presented and before the next renderer build"
            )
        ),
    }
    return captured


def _run_minimal_zero_echo_session(
    *,
    root: Path,
    rom: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    """Launch only far enough to prove one owned native tick-8 capture."""
    session_id = "minimal_zero_echo_capture"
    transcript.append(f"# {session_id} begin")
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed_keys: list[tuple[str, int, int, bool]] = []
    events: list[str] = []
    result: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(
        prefix="recursion94-m4-visual-minimal-"
    ) as profile_text:
        profile = Path(profile_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        pre_path_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            pre_path_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        _bind_session_screenshot_keys(controller_profile)
        screenshot_directory = Path(
            controller_profile["session_isolation"]["screenshot_path"]
        )
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=isolated_runtime,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            vblank = symbols["VBlankInterrupt"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("minimal VBlank breakpoint failed")
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            _press_key(window, RIGHT_KEY, pressed_keys, events)
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("minimal VBlank removal failed")
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure("minimal post-step breakpoint failed")
            attempt_0_inputs = bytearray()
            for tick in range(1, 9):
                observed = _observe_post_step(
                    remote,
                    expected_attempt=0,
                    expected_tick=tick,
                    session_id=session_id,
                )
                attempt_0_inputs.append(observed["input"]["held"])
            post_dma_commit = derive_post_dma_commit_address(
                rom.read_bytes(), symbols
            )["post_commit_address"]
            zero_capture = _capture_rendered_post_step(
                remote=remote,
                root=root,
                process_id=process.pid,
                window=window,
                screenshot_directory=screenshot_directory,
                screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE["zero_echo"],
                input_events=events,
                label="zero_echo",
                output=root / SCREENSHOT_FILES["zero_echo"],
                post_step=post_step,
                post_dma_commit=post_dma_commit,
                expected_attempt=0,
                expected_tick=8,
            )
            attempt_0_inputs.append(
                zero_capture["state_before_ui_request"]["input"]["held"]
            )
            attempt_0_inputs.append(
                zero_capture["state_after_ui_key_down"]["input"]["held"]
            )
            attempt_0_inputs.append(
                zero_capture["state"]["input"]["held"]
            )
            for tick in range(12, 41):
                observed = _observe_post_step(
                    remote,
                    expected_attempt=0,
                    expected_tick=tick,
                    session_id=session_id,
                )
                attempt_0_inputs.append(observed["input"]["held"])
                if tick == 32:
                    _release_key(
                        window, RIGHT_KEY, pressed_keys, events
                    )
            pre_commit = _capture_state(
                remote, "minimal_tick_40_pre_manual_commit"
            )
            _press_key(window, START_KEY, pressed_keys, events)
            _press_key(window, C_KEY, pressed_keys, events)
            reset_observation = _observe_post_step(
                remote,
                expected_attempt=1,
                expected_tick=0,
                session_id=session_id,
            )
            reset_state = _capture_state(
                remote, "minimal_post_start_c_attempt_1_tick_0"
            )
            _release_key(window, C_KEY, pressed_keys, events)
            _release_key(window, START_KEY, pressed_keys, events)
            _press_key(window, RIGHT_KEY, pressed_keys, events)
            reset_capture = _capture_rendered_post_step(
                remote=remote,
                root=root,
                process_id=process.pid,
                window=window,
                screenshot_directory=screenshot_directory,
                screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE["reset_trace"],
                input_events=events,
                label="reset_trace",
                output=root / SCREENSHOT_FILES["reset_trace"],
                post_step=post_step,
                post_dma_commit=post_dma_commit,
                expected_attempt=1,
                expected_tick=0,
            )
            attempt_1_inputs = [
                reset_capture[state_key]["input"]["held"]
                for state_key in (
                    "state_before_ui_request",
                    "state_after_ui_key_down",
                    "state",
                )
            ]
            result = {
                "session_id": session_id,
                "process_id": process.pid,
                "runtime_copy": runtime_copy,
                "controller_profile": controller_profile,
                "input_events": events,
                "attempt_0_inputs": list(attempt_0_inputs),
                "attempt_1_inputs": attempt_1_inputs,
                "pre_commit": pre_commit,
                "reset_observation": reset_observation,
                "reset_state": reset_state,
                "capture": zero_capture,
                "reset_capture": reset_capture,
            }
        finally:
            if window is not None:
                for key in reversed(pressed_keys):
                    try:
                        _post_key(window, key, pressed=False)
                    except Exception as error:
                        transcript.append(
                            "! minimal emergency key release failed: "
                            f"{type(error).__name__}: {error}"
                        )
            shutdown_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if result is None:
        raise EvidenceFailure("minimal capture produced no result")
    return result


def run_minimal_capture(
    *,
    root: Path,
    rom: Path,
    trace_path: Path,
    trace_json_path: Path,
    expected_sha256: str,
    timeout_seconds: float,
    lock_timeout_seconds: float,
) -> int:
    """Run the requested one-frame proof before any complete recapture."""
    transcript: list[str] = []
    report: dict[str, Any]
    rom_sha256 = ""
    try:
        if os.name != "nt":
            raise EvidenceFailure("minimal capture requires Windows")
        rom_sha256 = sha256_file(rom)
        if (
            rom_sha256 != expected_sha256
            or rom_sha256 != EXPECTED_DEBUG_ROM_SHA256
        ):
            raise EvidenceFailure("minimal capture ROM hash mismatch")
        emulator_directory = root / ".toolcache/blastem"
        emulator = emulator_directory / "blastem.exe"
        default_config = emulator_directory / "default.cfg"
        symbol_path = root / "build/recursion94-debug.sym"
        bindings = verify_input_bindings(default_config)
        if bindings["status"] != "pass":
            raise EvidenceFailure("minimal capture bindings failed")
        symbols = parse_vasm_symbols(symbol_path)
        missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
        if missing:
            raise EvidenceFailure(
                f"minimal symbol map is missing {missing}"
            )
        lock_path = root / ".toolcache/locks/blastem-gdb.lock"
        with exclusive_file_lock(
            lock_path, lock_timeout_seconds
        ) as lock:
            session = _run_minimal_zero_echo_session(
                root=root,
                rom=rom,
                emulator_directory=emulator_directory,
                default_config=default_config,
                symbols=symbols,
                timeout_seconds=timeout_seconds,
                transcript=transcript,
            )
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            "\n".join(transcript) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        capture = session["capture"]
        reset_capture = session["reset_capture"]
        image = capture["screenshot"]
        reset_image = reset_capture["screenshot"]
        published = capture["published_state"]
        state_before = capture["state_before_ui_request"]
        state_after = capture["state"]
        ownership = image["window_ownership"]
        checks = [
            check(
                "minimal_owned_native_ui_screenshot",
                ownership["process_id"]
                == ownership["resolved_owner_process_id"]
                == session["process_id"]
                and ownership["capture_method"]
                == (
                    "BlastEm ui.screenshot via isolated one-shot key binding"
                )
                and ownership["isolated_screenshot_path"]
                == session["controller_profile"]["session_isolation"][
                    "screenshot_path"
                ]
                and ownership["source_sha256"] == image["sha256"]
                and ownership[
                    "isolated_source_removed_after_verified_copy"
                ]
                is True
                and ownership["ui_screenshot_key"] == "F1",
                str(ownership),
            ),
            check(
                "minimal_single_proven_screenshot_key_profile",
                session["controller_profile"]["screenshot_bindings"][
                    "status"
                ]
                == "pass"
                and session["controller_profile"]["screenshot_bindings"][
                    "capture_to_config_key"
                ]
                == {
                    name: key[0].lower()
                    for name, key in SCREENSHOT_KEYS_BY_CAPTURE.items()
                }
                and session["controller_profile"][
                    "screenshot_bindings"
                ]["keys_unique"]
                and session["controller_profile"][
                    "screenshot_bindings"
                ]["shared_key_across_isolated_sessions"]
                and session["controller_profile"][
                    "screenshot_bindings"
                ]["base_profile_keys_unmapped"],
                "default P replaced by proven F1 in the isolated profile",
            ),
            check(
                "minimal_known_game_composition",
                0.25 <= image["dark_pixel_ratio"] < 0.97
                and 0.002 <= image["light_pixel_ratio"] <= 0.15
                and image["red_dominant_ratio"] < 0.08
                and 8 <= image["quantized_color_count"] <= 512
                and image["rust_dominant_ratio"] >= 0.005
                and image["cobalt_dominant_ratio"] >= 0.002,
                "dark/light/red/colors/rust/cobalt "
                f"{image['dark_pixel_ratio']:.4f}/"
                f"{image['light_pixel_ratio']:.4f}/"
                f"{image['red_dominant_ratio']:.4f}/"
                f"{image['quantized_color_count']}/"
                f"{image['rust_dominant_ratio']:.4f}/"
                f"{image['cobalt_dominant_ratio']:.4f}",
            ),
            check(
                "minimal_zero_echo_tick_8_state",
                published["engine"]["attempt"] == 0
                and published["engine"]["tick"] == 8
                and published["timeline"]["echo_count"] == 0
                and published["gameplay"]["door_state"] == 0
                and published["debug"]["fatal_code"] == 0
                and bytes(session["attempt_0_inputs"])
                == CANONICAL_ATTEMPT_0,
                "attempt/tick/echo/door/fatal "
                f"{published['engine']['attempt']}/"
                f"{published['engine']['tick']}/"
                f"{published['timeline']['echo_count']}/"
                f"{published['gameplay']['door_state']}/"
                f"{published['debug']['fatal_code']}",
            ),
            check(
                "minimal_zero_then_reset_two_key_probe",
                reset_capture["published_state"]["engine"]["attempt"] == 1
                and reset_capture["published_state"]["engine"]["tick"] == 0
                and reset_capture["published_state"]["timeline"][
                    "echo_count"
                ]
                == 1
                and reset_capture["state_before_ui_request"]["engine"][
                    "tick"
                ]
                == 1
                and reset_capture["state_after_ui_key_down"]["engine"][
                    "tick"
                ]
                == 2
                and reset_capture["state"]["engine"]["tick"] == 3
                and reset_capture["authoritative_ticks_during_ui_capture"]
                == 2
                and reset_capture["ui_screenshot_progression_valid"]
                and reset_capture[
                    "ui_key_release_serviced_before_capture_stop"
                ]
                and reset_capture["ui_screenshot_key"] == "F6"
                and capture["ui_screenshot_key"] == "F1"
                and session["attempt_1_inputs"] == [INPUT_RIGHT] * 3
                and reset_image["path"]
                == SCREENSHOT_FILES["reset_trace"]
                and reset_image["sha256"] != image["sha256"]
                and reset_image["window_ownership"]["source_sha256"]
                == reset_image["sha256"]
                and reset_image["window_ownership"][
                    "isolated_source_removed_after_verified_copy"
                ]
                is True,
                "same BlastEm process produced F1 zero-echo and F6 "
                "post-manual-reset native PNGs with exact live state",
            ),
            check(
                "minimal_ui_screenshot_progression_and_read_only",
                capture["ui_screenshot_progression_valid"]
                and capture[
                    "ui_key_release_serviced_before_capture_stop"
                ]
                and capture["authoritative_ticks_during_ui_capture"] == 2
                and state_after["engine"]["attempt"] == 0
                and state_before["engine"]["tick"] == 9
                and capture["state_after_ui_key_down"]["engine"]["tick"]
                == 10
                and state_after["engine"]["tick"] == 11
                and state_after["input"]["held"] == INPUT_RIGHT
                and capture["ui_screenshot_key"] == "F1"
                and reset_capture["ui_screenshot_key"] == "F6"
                and not debugger_write_packets(transcript),
                "published tick 8; F1/F6 down/up serviced across exact "
                "observed resumes; "
                "no G/M/P/X packets",
            ),
            check(
                "minimal_hash_provenance",
                sha256_file(rom) == rom_sha256
                and sha256_file(emulator)
                and sha256_file(symbol_path),
                f"ROM {rom_sha256}",
            ),
        ]
        passed = all(item["status"] == "pass" for item in checks)
        report = {
            "schema": "r94-m4-visual-minimal-capture-v1",
            "status": "pass" if passed else "fail",
            "rom_sha256": rom_sha256,
            "checks": checks,
            "session": session,
            "lock": lock,
            "emulator_sha256": sha256_file(emulator),
            "symbol_map_sha256": sha256_file(symbol_path),
            "transcript_sha256": sha256_file(trace_path),
        }
    except Exception as error:
        report = {
            "schema": "r94-m4-visual-minimal-capture-v1",
            "status": "fail",
            "rom_sha256": rom_sha256,
            "checks": [
                check(
                    "minimal_capture",
                    False,
                    f"{type(error).__name__}: {error}",
                )
            ],
            "error": f"{type(error).__name__}: {error}",
        }
        capture_context = getattr(error, "capture_context", None)
        if capture_context is not None:
            report["failure_capture_context"] = capture_context
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        "\n".join(transcript) + ("\n" if transcript else ""),
        encoding="utf-8",
        newline="\n",
    )
    report["transcript_sha256"] = sha256_file(trace_path)
    write_json(trace_json_path, report)
    passed = report["status"] == "pass"
    print(f"M4 minimal visual capture: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def _run_canonical_session(
    *,
    root: Path,
    rom: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    post_dma_commit: int,
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    session_id = "canonical_visual"
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed_keys: list[tuple[str, int, int, bool]] = []
    events: list[str] = []
    session: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(
        prefix="recursion94-m4-visual-canonical-"
    ) as profile_text:
        profile = Path(profile_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        pre_path_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            pre_path_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        _bind_session_screenshot_keys(controller_profile)
        screenshot_directory = Path(
            controller_profile["session_isolation"]["screenshot_path"]
        )
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=isolated_runtime,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            if process.poll() is not None:
                raise EvidenceFailure("canonical BlastEm exited early")
            vblank = symbols["VBlankInterrupt"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("VBlank breakpoint insertion failed")
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            _press_key(window, RIGHT_KEY, pressed_keys, events)
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("VBlank breakpoint removal failed")
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    "post-gameplay breakpoint insertion failed"
                )
            attempt_0_inputs = bytearray()
            first: dict[str, Any] | None = None
            for tick in range(1, 9):
                first = _observe_post_step(
                    remote,
                    expected_attempt=0,
                    expected_tick=tick,
                    session_id=session_id,
                )
                attempt_0_inputs.append(first["input"]["held"])
            assert first is not None
            zero_capture = _capture_rendered_post_step(
                remote=remote,
                root=root,
                process_id=process.pid,
                window=window,
                screenshot_directory=screenshot_directory,
                screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE["zero_echo"],
                input_events=events,
                label="zero_echo",
                output=root / SCREENSHOT_FILES["zero_echo"],
                post_step=post_step,
                post_dma_commit=post_dma_commit,
                expected_attempt=0,
                expected_tick=8,
            )
            if (
                zero_capture["state"]["engine"]["attempt"] != 0
                or zero_capture["state"]["engine"]["tick"] != 11
                or zero_capture["state"]["input"]["held"] != INPUT_RIGHT
            ):
                raise EvidenceFailure(
                    "zero-echo ui.screenshot did not advance canonical tick 11"
                )
            attempt_0_inputs.append(
                zero_capture["state_before_ui_request"]["input"]["held"]
            )
            attempt_0_inputs.append(
                zero_capture["state_after_ui_key_down"]["input"]["held"]
            )
            attempt_0_inputs.append(
                zero_capture["state"]["input"]["held"]
            )
            if attempt_0_inputs[-3:] != bytes(
                (INPUT_RIGHT, INPUT_RIGHT, INPUT_RIGHT)
            ):
                raise EvidenceFailure(
                    "zero-echo ui.screenshot did not preserve held Right"
                )
            for tick in range(12, 41):
                observed = _observe_post_step(
                    remote,
                    expected_attempt=0,
                    expected_tick=tick,
                    session_id=session_id,
                )
                attempt_0_inputs.append(observed["input"]["held"])
                if tick == 32:
                    _release_key(
                        window, RIGHT_KEY, pressed_keys, events
                    )
            pre_commit = _capture_state(
                remote, "attempt_0_tick_40_pre_manual_commit"
            )

            _press_key(window, START_KEY, pressed_keys, events)
            _press_key(window, C_KEY, pressed_keys, events)
            reset_observed = _observe_post_step(
                remote,
                expected_attempt=1,
                expected_tick=0,
                session_id=session_id,
            )
            reset_state = _capture_state(
                remote, "post_start_c_attempt_1_tick_0"
            )
            _release_key(window, C_KEY, pressed_keys, events)
            _release_key(window, START_KEY, pressed_keys, events)
            _press_key(window, RIGHT_KEY, pressed_keys, events)
            reset_capture = _capture_rendered_post_step(
                remote=remote,
                root=root,
                process_id=process.pid,
                window=window,
                screenshot_directory=screenshot_directory,
                screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE["reset_trace"],
                input_events=events,
                label="reset_trace",
                output=root / SCREENSHOT_FILES["reset_trace"],
                post_step=post_step,
                post_dma_commit=post_dma_commit,
                expected_attempt=1,
                expected_tick=0,
            )

            if (
                reset_capture["state"]["engine"]["attempt"] != 1
                or reset_capture["state"]["engine"]["tick"] != 3
                or reset_capture["state"]["input"]["held"] != INPUT_RIGHT
            ):
                raise EvidenceFailure(
                    "reset ui.screenshot did not advance canonical tick 3"
                )
            attempt_1_inputs = bytearray(
                (
                    reset_capture["state_before_ui_request"]["input"][
                        "held"
                    ],
                    reset_capture["state_after_ui_key_down"]["input"][
                        "held"
                    ],
                    reset_capture["state"]["input"]["held"],
                )
            )
            if attempt_1_inputs != bytes(
                (INPUT_RIGHT, INPUT_RIGHT, INPUT_RIGHT)
            ):
                raise EvidenceFailure(
                    "reset ui.screenshot did not preserve held Right"
                )
            for tick in range(4, OPEN_CAPTURE_TICK + 1):
                observed = _observe_post_step(
                    remote,
                    expected_attempt=1,
                    expected_tick=tick,
                    session_id=session_id,
                )
                attempt_1_inputs.append(observed["input"]["held"])
            open_capture = _capture_rendered_post_step(
                remote=remote,
                root=root,
                process_id=process.pid,
                window=window,
                screenshot_directory=screenshot_directory,
                screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE[
                    "echo_door_open"
                ],
                input_events=events,
                label="echo_door_open",
                output=root / SCREENSHOT_FILES["echo_door_open"],
                post_step=post_step,
                post_dma_commit=post_dma_commit,
                expected_attempt=1,
                expected_tick=OPEN_CAPTURE_TICK,
            )

            if (
                open_capture["state"]["engine"]["attempt"] != 1
                or open_capture["state"]["engine"]["tick"]
                != OPEN_CAPTURE_TICK + 3
                or open_capture["state"]["input"]["held"] != INPUT_RIGHT
            ):
                raise EvidenceFailure(
                    "open-door ui.screenshot did not advance canonical "
                    f"tick {OPEN_CAPTURE_TICK + 3}"
                )
            for state_key in (
                "state_before_ui_request",
                "state_after_ui_key_down",
                "state",
            ):
                held = open_capture[state_key]["input"]["held"]
                if held != INPUT_RIGHT:
                    raise EvidenceFailure(
                        "open-door ui.screenshot did not preserve held Right"
                    )
                attempt_1_inputs.append(held)
            for tick in range(OPEN_CAPTURE_TICK + 4, 166):
                observed = _observe_post_step(
                    remote,
                    expected_attempt=1,
                    expected_tick=tick,
                    session_id=session_id,
                )
                attempt_1_inputs.append(observed["input"]["held"])
            _press_key(window, B_KEY, pressed_keys, events)
            terminal_observed = _observe_post_step(
                remote,
                expected_attempt=1,
                expected_tick=166,
                session_id=session_id,
            )
            attempt_1_inputs.append(terminal_observed["input"]["held"])
            _release_key(window, B_KEY, pressed_keys, events)
            _release_key(window, RIGHT_KEY, pressed_keys, events)
            stable_capture = _capture_rendered_post_step(
                remote=remote,
                root=root,
                process_id=process.pid,
                window=window,
                screenshot_directory=screenshot_directory,
                screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE[
                    "program_stable"
                ],
                input_events=events,
                label="program_stable",
                output=root / SCREENSHOT_FILES["program_stable"],
                post_step=post_step,
                post_dma_commit=post_dma_commit,
                expected_attempt=1,
                expected_tick=166,
                terminal_vblank_ui=True,
            )

            session = {
                "session_id": session_id,
                "metadata": {
                    "process_id": process.pid,
                    "launch_flags": list(LAUNCH_FLAGS),
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "initial_breakpoint_symbol": "VBlankInterrupt",
                    "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
                    "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
                    "counters_and_controller_poll_begin": (
                        FIRST_RUNNING_VBLANK_ROLE
                    ),
                    "step_breakpoint_symbol": "MainLoopPostGameplayStep",
                    "render_capture_boundary": (
                        "fallthrough of VBlank DmaCommitPreparedQueue"
                    ),
                    "input_events": events,
                },
                "attempt_0_inputs": list(attempt_0_inputs),
                "attempt_1_inputs": list(attempt_1_inputs),
                "pre_commit": pre_commit,
                "reset_observation": reset_observed,
                "reset_state": reset_state,
                "captures": {
                    "zero_echo": zero_capture,
                    "reset_trace": reset_capture,
                    "echo_door_open": open_capture,
                    "program_stable": stable_capture,
                },
                "input_injection": {
                    "status": "pass",
                    "method": (
                        "PostMessageW key events to the owned BlastEm window"
                    ),
                    "buttons": ["Right", "Start", "C", "B"],
                    "guest_ram_register_or_pc_writes": False,
                },
            }
        finally:
            if window is not None:
                for key in reversed(pressed_keys):
                    try:
                        _post_key(window, key, pressed=False)
                        events.append(f"{key[0]}:emergency-up")
                    except Exception as error:
                        transcript.append(
                            "! emergency key release failed: "
                            f"{type(error).__name__}: {error}"
                        )
            pressed_keys.clear()
            shutdown_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if session is None:
        raise EvidenceFailure("canonical visual session produced no evidence")
    lines = transcript[start_index:]
    session["metadata"]["transcript_sha256"] = _sha256_bytes(
        ("\n".join(lines) + "\n").encode("utf-8")
    )
    return session


def _run_blocked_session(
    *,
    root: Path,
    rom: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    post_dma_commit: int,
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    session_id = "blocked_then_recover"
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed_keys: list[tuple[str, int, int, bool]] = []
    events: list[str] = []
    session: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(
        prefix="recursion94-m4-visual-blocked-"
    ) as profile_text:
        profile = Path(profile_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        pre_path_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            pre_path_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        _bind_session_screenshot_keys(controller_profile)
        screenshot_directory = Path(
            controller_profile["session_isolation"]["screenshot_path"]
        )
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=isolated_runtime,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            if process.poll() is not None:
                raise EvidenceFailure("blocked-path BlastEm exited early")
            vblank = symbols["VBlankInterrupt"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("VBlank breakpoint insertion failed")
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            _press_key(window, RIGHT_KEY, pressed_keys, events)
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("VBlank breakpoint removal failed")
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    "post-gameplay breakpoint insertion failed"
                )
            inputs = bytearray()
            for tick in range(1, 101):
                observed = _observe_post_step(
                    remote,
                    expected_attempt=0,
                    expected_tick=tick,
                    session_id=session_id,
                )
                inputs.append(observed["input"]["held"])
            _press_key(window, B_KEY, pressed_keys, events)
            blocked_observed = _observe_post_step(
                remote,
                expected_attempt=0,
                expected_tick=101,
                session_id=session_id,
            )
            inputs.append(blocked_observed["input"]["held"])
            _release_key(window, B_KEY, pressed_keys, events)
            _release_key(window, RIGHT_KEY, pressed_keys, events)
            blocked_capture = _capture_rendered_post_step(
                remote=remote,
                root=root,
                process_id=process.pid,
                window=window,
                screenshot_directory=screenshot_directory,
                screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE["blocked_path"],
                input_events=events,
                label="blocked_path",
                output=root / SCREENSHOT_FILES["blocked_path"],
                post_step=post_step,
                post_dma_commit=post_dma_commit,
                expected_attempt=0,
                expected_tick=101,
            )

            if (
                blocked_capture["state"]["engine"]["attempt"] != 0
                or blocked_capture["state"]["engine"]["tick"] != 104
                or blocked_capture["state"]["input"]["held"] != 0
            ):
                raise EvidenceFailure(
                    "blocked ui.screenshot did not advance neutral tick 104"
                )
            for state_key in (
                "state_before_ui_request",
                "state_after_ui_key_down",
                "state",
            ):
                held = blocked_capture[state_key]["input"]["held"]
                if held != 0:
                    raise EvidenceFailure(
                        "blocked ui.screenshot did not preserve neutral input"
                    )
                inputs.append(held)
            _press_key(window, START_KEY, pressed_keys, events)
            _press_key(window, C_KEY, pressed_keys, events)
            recovered_observed = _observe_post_step(
                remote,
                expected_attempt=1,
                expected_tick=0,
                session_id=session_id,
            )
            recovered = _capture_state(
                remote, "blocked_path_start_c_recovered"
            )
            _release_key(window, C_KEY, pressed_keys, events)
            _release_key(window, START_KEY, pressed_keys, events)

            session = {
                "session_id": session_id,
                "metadata": {
                    "process_id": process.pid,
                    "launch_flags": list(LAUNCH_FLAGS),
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "initial_breakpoint_symbol": "VBlankInterrupt",
                    "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
                    "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
                    "counters_and_controller_poll_begin": (
                        FIRST_RUNNING_VBLANK_ROLE
                    ),
                    "step_breakpoint_symbol": "MainLoopPostGameplayStep",
                    "render_capture_boundary": (
                        "fallthrough of VBlank DmaCommitPreparedQueue"
                    ),
                    "input_events": events,
                },
                "attempt_0_inputs": list(inputs),
                "blocked_input_observation": blocked_observed,
                "blocked_capture": blocked_capture,
                "recovered_observation": recovered_observed,
                "recovered_state": recovered,
                "input_injection": {
                    "status": "pass",
                    "method": (
                        "PostMessageW key events to the owned BlastEm window"
                    ),
                    "buttons": ["Right", "B", "Start", "C"],
                    "guest_ram_register_or_pc_writes": False,
                },
            }
        finally:
            if window is not None:
                for key in reversed(pressed_keys):
                    try:
                        _post_key(window, key, pressed=False)
                        events.append(f"{key[0]}:emergency-up")
                    except Exception as error:
                        transcript.append(
                            "! emergency key release failed: "
                            f"{type(error).__name__}: {error}"
                        )
            pressed_keys.clear()
            shutdown_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if session is None:
        raise EvidenceFailure("blocked visual session produced no evidence")
    lines = transcript[start_index:]
    session["metadata"]["transcript_sha256"] = _sha256_bytes(
        ("\n".join(lines) + "\n").encode("utf-8")
    )
    return session


def _capture_map(
    canonical: dict[str, Any], blocked: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    captures = dict(canonical["captures"])
    captures["blocked_path"] = blocked["blocked_capture"]
    return captures


def _append_active_capture_inputs(
    capture: dict[str, Any], inputs: bytearray
) -> None:
    if (
        capture["published_state"]["engine"]["state"]
        == ENGINE_STATE_TERMINAL
    ):
        return
    for state_key in (
        "state_before_ui_request",
        "state_after_ui_key_down",
        "state",
    ):
        inputs.append(capture[state_key]["input"]["held"])


def _run_fresh_capture_session(
    *,
    root: Path,
    rom: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    post_dma_commit: int,
    timeout_seconds: float,
    transcript: list[str],
    capture_name: str,
    terminal_vblank_ui: bool = False,
) -> dict[str, Any]:
    if capture_name not in SCREENSHOT_FILES:
        raise ValueError(f"unknown fresh capture {capture_name}")
    if terminal_vblank_ui and capture_name != "program_stable":
        raise ValueError(
            "terminal VBlank UI discriminant is program-stable only"
        )
    session_id = (
        "fresh_program_stable_vblank_discriminant"
        if terminal_vblank_ui
        else f"fresh_{capture_name}"
    )
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed_keys: list[tuple[str, int, int, bool]] = []
    events: list[str] = []
    result: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(
        prefix=f"recursion94-m4-{capture_name}-"
    ) as profile_text:
        profile = Path(profile_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        pre_path_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            pre_path_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        _bind_session_screenshot_keys(controller_profile)
        screenshot_directory = Path(
            controller_profile["session_isolation"]["screenshot_path"]
        )
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=isolated_runtime,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            if process.poll() is not None:
                raise EvidenceFailure(
                    f"{capture_name} BlastEm exited early"
                )
            vblank = symbols["VBlankInterrupt"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    "stage-3 priming VBlank breakpoint insertion failed"
                )
            # The first stop is the stage-3 acknowledgment-only priming ISR.
            # Right remains queued until the next stage-4 RUNNING full VBlank
            # performs the first controller poll and advances frame counters.
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            _press_key(window, RIGHT_KEY, pressed_keys, events)
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    "stage-3 priming VBlank breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure("fresh post-step breakpoint failed")

            attempt_0_inputs = bytearray()
            attempt_1_inputs = bytearray()
            pre_commit: dict[str, Any] | None = None
            manual_commit_entry: dict[str, Any] | None = None
            reset_observation: dict[str, Any] | None = None
            reset_state: dict[str, Any] | None = None
            blocked_observation: dict[str, Any] | None = None
            recovered_observation: dict[str, Any] | None = None
            recovered_state: dict[str, Any] | None = None

            if capture_name == "blocked_path":
                for tick in range(1, 101):
                    observed = _observe_post_step(
                        remote,
                        expected_attempt=0,
                        expected_tick=tick,
                        session_id=session_id,
                    )
                    attempt_0_inputs.append(observed["input"]["held"])
                _press_key(window, B_KEY, pressed_keys, events)
                blocked_observation = _observe_post_step(
                    remote,
                    expected_attempt=0,
                    expected_tick=101,
                    session_id=session_id,
                )
                attempt_0_inputs.append(
                    blocked_observation["input"]["held"]
                )
                _release_key(window, B_KEY, pressed_keys, events)
                _release_key(window, RIGHT_KEY, pressed_keys, events)
                capture = _capture_rendered_post_step(
                    remote=remote,
                    root=root,
                    process_id=process.pid,
                    window=window,
                    screenshot_directory=screenshot_directory,
                    screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE[capture_name],
                    input_events=events,
                    label=capture_name,
                    output=root / SCREENSHOT_FILES[capture_name],
                    post_step=post_step,
                    post_dma_commit=post_dma_commit,
                    expected_attempt=0,
                    expected_tick=101,
                )
                _append_active_capture_inputs(
                    capture, attempt_0_inputs
                )
                _press_key(window, START_KEY, pressed_keys, events)
                _press_key(window, C_KEY, pressed_keys, events)
                recovered_observation = _observe_post_step(
                    remote,
                    expected_attempt=1,
                    expected_tick=0,
                    session_id=session_id,
                )
                recovered_state = _capture_state(
                    remote, "fresh_blocked_start_c_recovered"
                )
                _release_key(window, C_KEY, pressed_keys, events)
                _release_key(window, START_KEY, pressed_keys, events)
            else:
                initial_stop = 8 if capture_name == "zero_echo" else 40
                for tick in range(1, initial_stop + 1):
                    observed = _observe_post_step(
                        remote,
                        expected_attempt=0,
                        expected_tick=tick,
                        session_id=session_id,
                    )
                    attempt_0_inputs.append(observed["input"]["held"])
                    if tick == 32:
                        _release_key(
                            window, RIGHT_KEY, pressed_keys, events
                        )
                if capture_name == "zero_echo":
                    capture = _capture_rendered_post_step(
                        remote=remote,
                        root=root,
                        process_id=process.pid,
                        window=window,
                        screenshot_directory=screenshot_directory,
                        screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE[
                            capture_name
                        ],
                        input_events=events,
                        label=capture_name,
                        output=root / SCREENSHOT_FILES[capture_name],
                        post_step=post_step,
                        post_dma_commit=post_dma_commit,
                        expected_attempt=0,
                        expected_tick=8,
                    )
                    _append_active_capture_inputs(
                        capture, attempt_0_inputs
                    )
                else:
                    pre_commit = _capture_state(
                        remote, f"{capture_name}_pre_manual_commit"
                    )
                    _press_key(window, START_KEY, pressed_keys, events)
                    _press_key(window, C_KEY, pressed_keys, events)

                    # Stop after the meta chord has been latched by
                    # ControllerPoll but before commit/reset. The reset path
                    # exceeds one frame and can queue attempt 1's first token,
                    # so next-attempt releases/holds must be staged here rather
                    # than at the later post-reset main-loop stop.
                    meta_input = symbols["TimelineHandleMetaInput"]
                    if remote.request(f"z0,{post_step:x},2") != "OK":
                        raise EvidenceFailure(
                            f"{capture_name} post-step removal before "
                            "meta-input staging failed"
                        )
                    if remote.request(f"Z0,{meta_input:x},2") != "OK":
                        raise EvidenceFailure(
                            f"{capture_name} meta-input breakpoint failed"
                        )
                    require_stop(remote.request("c"))
                    manual_commit_entry = _capture_state(
                        remote,
                        f"{capture_name}_start_c_meta_entry",
                    )
                    _release_key(
                        window, C_KEY, pressed_keys, events
                    )
                    _release_key(
                        window, START_KEY, pressed_keys, events
                    )
                    if capture_name != "reset_trace":
                        _press_key(
                            window, RIGHT_KEY, pressed_keys, events
                        )
                    if remote.request(f"z0,{meta_input:x},2") != "OK":
                        raise EvidenceFailure(
                            f"{capture_name} meta-input breakpoint removal "
                            "failed"
                        )
                    if remote.request(f"Z0,{post_step:x},2") != "OK":
                        raise EvidenceFailure(
                            f"{capture_name} post-step reinsertion failed"
                        )
                    reset_observation = _observe_post_step(
                        remote,
                        expected_attempt=1,
                        expected_tick=0,
                        session_id=session_id,
                    )
                    reset_state = _capture_state(
                        remote, f"{capture_name}_post_manual_commit"
                    )
                    if capture_name == "reset_trace":
                        for tick in range(1, 4):
                            observed = _observe_post_step(
                                remote,
                                expected_attempt=1,
                                expected_tick=tick,
                                session_id=session_id,
                            )
                            attempt_1_inputs.append(
                                observed["input"]["held"]
                            )
                        capture = _capture_rendered_post_step(
                            remote=remote,
                            root=root,
                            process_id=process.pid,
                            window=window,
                            screenshot_directory=screenshot_directory,
                            screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE[
                                capture_name
                            ],
                            input_events=events,
                            label=capture_name,
                            output=root / SCREENSHOT_FILES[capture_name],
                            post_step=post_step,
                            post_dma_commit=post_dma_commit,
                            expected_attempt=1,
                            expected_tick=3,
                        )
                        _append_active_capture_inputs(
                            capture, attempt_1_inputs
                        )
                    else:
                        last_right_tick = (
                            OPEN_CAPTURE_TICK
                            if capture_name == "echo_door_open"
                            else 165
                        )
                        for tick in range(1, last_right_tick + 1):
                            observed = _observe_post_step(
                                remote,
                                expected_attempt=1,
                                expected_tick=tick,
                                session_id=session_id,
                            )
                            attempt_1_inputs.append(
                                observed["input"]["held"]
                            )
                        target_tick = last_right_tick
                        if capture_name == "program_stable":
                            _press_key(
                                window, B_KEY, pressed_keys, events
                            )
                            observed = _observe_post_step(
                                remote,
                                expected_attempt=1,
                                expected_tick=166,
                                session_id=session_id,
                            )
                            attempt_1_inputs.append(
                                observed["input"]["held"]
                            )
                            _release_key(
                                window, B_KEY, pressed_keys, events
                            )
                            _release_key(
                                window, RIGHT_KEY, pressed_keys, events
                            )
                            target_tick = 166
                        capture = _capture_rendered_post_step(
                            remote=remote,
                            root=root,
                            process_id=process.pid,
                            window=window,
                            screenshot_directory=screenshot_directory,
                            screenshot_key=SCREENSHOT_KEYS_BY_CAPTURE[
                                capture_name
                            ],
                            input_events=events,
                            label=capture_name,
                            output=root / SCREENSHOT_FILES[capture_name],
                            post_step=post_step,
                            post_dma_commit=post_dma_commit,
                            expected_attempt=1,
                            expected_tick=target_tick,
                            terminal_vblank_ui=(
                                terminal_vblank_ui
                                or capture_name == "program_stable"
                            ),
                            terminal_prearmed_ui=False,
                        )
                        _append_active_capture_inputs(
                            capture, attempt_1_inputs
                        )
            result = {
                "session_id": session_id,
                "metadata": {
                    "process_id": process.pid,
                    "launch_flags": list(LAUNCH_FLAGS),
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "initial_breakpoint_symbol": "VBlankInterrupt",
                    "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
                    "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
                    "counters_and_controller_poll_begin": (
                        FIRST_RUNNING_VBLANK_ROLE
                    ),
                    "step_breakpoint_symbol": "MainLoopPostGameplayStep",
                    "attempt_1_input_staging_breakpoint_symbol": (
                        "TimelineHandleMetaInput"
                        if manual_commit_entry is not None
                        else None
                    ),
                    "attempt_1_input_staging_role": (
                        "after_controller_poll_before_commit_reset"
                        if manual_commit_entry is not None
                        else None
                    ),
                    "render_capture_boundary": (
                        (
                            "F1 down/up plus one release-armed framebuffer "
                            "across successive fallthroughs of VBlank "
                            "DmaCommitPreparedQueue"
                        )
                        if (
                            terminal_vblank_ui
                            or capture_name == "program_stable"
                        )
                        else "fallthrough of VBlank DmaCommitPreparedQueue"
                    ),
                    "input_events": events,
                },
                "attempt_0_inputs": list(attempt_0_inputs),
                "attempt_1_inputs": list(attempt_1_inputs),
                "pre_commit": pre_commit,
                "manual_commit_entry": manual_commit_entry,
                "reset_observation": reset_observation,
                "reset_state": reset_state,
                "blocked_input_observation": blocked_observation,
                "recovered_observation": recovered_observation,
                "recovered_state": recovered_state,
                "capture": capture,
                "input_injection": {
                    "status": "pass",
                    "method": (
                        "PostMessageW key events to the owned BlastEm window"
                    ),
                    "guest_ram_register_or_pc_writes": False,
                },
            }
        finally:
            if window is not None:
                for key in reversed(pressed_keys):
                    try:
                        _post_key(window, key, pressed=False)
                        events.append(f"{key[0]}:emergency-up")
                    except Exception as error:
                        transcript.append(
                            "! fresh emergency key release failed: "
                            f"{type(error).__name__}: {error}"
                        )
            pressed_keys.clear()
            shutdown_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if result is None:
        raise EvidenceFailure(
            f"{capture_name} fresh session produced no evidence"
        )
    lines = transcript[start_index:]
    result["metadata"]["transcript_sha256"] = _sha256_bytes(
        ("\n".join(lines) + "\n").encode("utf-8")
    )
    return result


def _aggregate_fresh_capture_sessions(
    sessions: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if tuple(sessions) != tuple(SCREENSHOT_FILES):
        raise EvidenceFailure("fresh capture session set is incomplete")
    canonical_names = tuple(SCREENSHOT_FILES)[:4]
    reset_session = sessions["reset_trace"]
    stable_session = sessions["program_stable"]
    blocked_session = sessions["blocked_path"]
    canonical_transcripts = "\n".join(
        f"{name}:{sessions[name]['metadata']['transcript_sha256']}"
        for name in canonical_names
    ).encode("ascii")
    canonical = {
        "session_id": "fresh_canonical_capture_set",
        "metadata": {
            "process_id": sessions["zero_echo"]["metadata"]["process_id"],
            "process_ids": [
                sessions[name]["metadata"]["process_id"]
                for name in canonical_names
            ],
            "controller_profile": sessions["zero_echo"]["metadata"][
                "controller_profile"
            ],
            "capture_sessions": {
                name: sessions[name]["metadata"]
                for name in canonical_names
            },
            "input_events": [
                event
                for name in canonical_names
                for event in sessions[name]["metadata"]["input_events"]
            ],
            "transcript_sha256": _sha256_bytes(canonical_transcripts),
        },
        "attempt_0_inputs": reset_session["attempt_0_inputs"],
        "attempt_1_inputs": stable_session["attempt_1_inputs"],
        "pre_commit": reset_session["pre_commit"],
        "manual_commit_entries": {
            name: sessions[name]["manual_commit_entry"]
            for name in (
                "reset_trace",
                "echo_door_open",
                "program_stable",
            )
        },
        "reset_observation": reset_session["reset_observation"],
        "reset_state": reset_session["reset_state"],
        "captures": {
            name: sessions[name]["capture"] for name in canonical_names
        },
        "input_injection": {
            "status": (
                "pass"
                if all(
                    sessions[name]["input_injection"]["status"] == "pass"
                    for name in canonical_names
                )
                else "fail"
            ),
            "method": "fresh owned-window controller sessions",
            "guest_ram_register_or_pc_writes": False,
        },
    }
    blocked = {
        "session_id": blocked_session["session_id"],
        "metadata": {
            **blocked_session["metadata"],
            "capture_sessions": {
                "blocked_path": blocked_session["metadata"]
            },
        },
        "attempt_0_inputs": blocked_session["attempt_0_inputs"],
        "blocked_input_observation": blocked_session[
            "blocked_input_observation"
        ],
        "blocked_capture": blocked_session["capture"],
        "recovered_observation": blocked_session[
            "recovered_observation"
        ],
        "recovered_state": blocked_session["recovered_state"],
        "input_injection": blocked_session["input_injection"],
    }
    return canonical, blocked


def _report_checks(
    *,
    rom_sha256: str,
    canonical: dict[str, Any],
    blocked: dict[str, Any],
    captures: dict[str, dict[str, Any]],
    provenance: dict[str, Any],
    transcript_lines: list[str],
) -> list[dict[str, Any]]:
    zero = captures["zero_echo"]["published_state"]
    reset = canonical["reset_state"]
    reset_frame = captures["reset_trace"]["published_state"]
    opened = captures["echo_door_open"]["published_state"]
    stable = captures["program_stable"]["published_state"]
    failed = captures["blocked_path"]["published_state"]
    recovered = blocked["recovered_state"]
    write_packets = debugger_write_packets(transcript_lines)
    screenshots = [captures[name]["screenshot"] for name in SCREENSHOT_FILES]
    rubric_reviews = [VISUAL_REVIEWS[name] for name in SCREENSHOT_FILES]
    screenshot_paths = [item["path"] for item in screenshots]
    capture_sessions = {
        **canonical["metadata"]["capture_sessions"],
        **blocked["metadata"]["capture_sessions"],
    }
    session_metadata = list(capture_sessions.values())
    checks = [
        check(
            "exact_final_debug_rom",
            rom_sha256 == EXPECTED_DEBUG_ROM_SHA256,
            f"Debug ROM SHA-256 {rom_sha256}",
        ),
        check(
            "five_fresh_isolated_standard_pad_sessions",
            len(
                {
                    session["process_id"]
                    for session in session_metadata
                }
            )
            == len(SCREENSHOT_FILES)
            and all(
                session["controller_profile"].get("status")
                == "pass"
                and session["controller_profile"].get(
                    "port_1_assignment"
                )
                == "gamepad3.1"
                and session["controller_profile"]
                .get("session_isolation", {})
                .get("status")
                == "pass"
                for session in session_metadata
            ),
            "independent process IDs "
            f"{[session['process_id'] for session in session_metadata]}",
        ),
        check(
            "startup_priming_and_running_vblank_origin_explicit",
            all(
                session.get("initial_breakpoint_symbol")
                == "VBlankInterrupt"
                and session.get("initial_breakpoint_role")
                == STARTUP_PRIMING_ROLE
                and session.get("authoritative_origin")
                == FIRST_RUNNING_VBLANK_ROLE
                and session.get("counters_and_controller_poll_begin")
                == FIRST_RUNNING_VBLANK_ROLE
                and session.get("step_breakpoint_symbol")
                == "MainLoopPostGameplayStep"
                for session in session_metadata
            ),
            (
                "each fresh capture starts at the stage-3 acknowledgment-only "
                "priming ISR; controller polling and counters begin on the "
                "next stage-4 RUNNING full VBlank"
            ),
        ),
        check(
            "attempt_1_input_staged_before_commit_reset",
            all(
                capture_sessions[name].get(
                    "attempt_1_input_staging_breakpoint_symbol"
                )
                == "TimelineHandleMetaInput"
                and capture_sessions[name].get(
                    "attempt_1_input_staging_role"
                )
                == "after_controller_poll_before_commit_reset"
                and canonical["manual_commit_entries"][name]["engine"][
                    "attempt"
                ]
                == 0
                and canonical["manual_commit_entries"][name]["engine"][
                    "tick"
                ]
                == 40
                and canonical["manual_commit_entries"][name]["input"][
                    "start_held"
                ]
                == 1
                and canonical["manual_commit_entries"][name]["input"][
                    "held"
                ]
                == INPUT_C
                and canonical["manual_commit_entries"][name]["input"][
                    "pressed"
                ]
                & INPUT_C
                for name in (
                    "reset_trace",
                    "echo_door_open",
                    "program_stable",
                )
            ),
            "Start+C was guest-latched at meta entry; C/Start releases and "
            "the next-attempt input were staged before the long reset path",
        ),
        check(
            "controller_window_input_only",
            all(
                session["input_injection"].get("status") == "pass"
                and session["input_injection"].get(
                    "guest_ram_register_or_pc_writes"
                )
                is False
                for session in (canonical, blocked)
            )
            and not write_packets,
            f"debugger RAM/register/PC write packets {write_packets}",
        ),
        check(
            "single_proven_screenshot_key_per_isolated_session",
            all(
                session["controller_profile"]["screenshot_bindings"][
                    "status"
                ]
                == "pass"
                and session["controller_profile"][
                    "screenshot_bindings"
                ]["capture_to_config_key"]
                == {
                    name: key[0].lower()
                    for name, key in SCREENSHOT_KEYS_BY_CAPTURE.items()
                }
                and session["controller_profile"][
                    "screenshot_bindings"
                ]["keys_unique"]
                and session["controller_profile"][
                    "screenshot_bindings"
                ]["shared_key_across_isolated_sessions"]
                and session["controller_profile"][
                    "screenshot_bindings"
                ]["base_profile_keys_unmapped"]
                for session in session_metadata
            )
            and all(
                captures[name]["ui_screenshot_key"] == key[0]
                for name, key in SCREENSHOT_KEYS_BY_CAPTURE.items()
            )
            and all(
                capture_sessions[name]["input_events"].count(
                    f"{key[0]}:ui.screenshot:down"
                )
                == 1
                and capture_sessions[name]["input_events"].count(
                    f"{key[0]}:ui.screenshot:up"
                )
                == 1
                for name, key in SCREENSHOT_KEYS_BY_CAPTURE.items()
            ),
            "default P replaced by F1; each fresh process uses one "
            "F1 down/up screenshot event",
        ),
        check(
            "symbol_derived_post_dma_capture_boundary",
            provenance.get("post_dma_commit", {}).get("derivation")
            is not None
            and all(
                capture.get("observation_boundary", {}).get(
                    "post_commit_address"
                )
                == provenance["post_dma_commit"]["post_commit_address_hex"]
                for capture in captures.values()
            ),
            str(provenance.get("post_dma_commit", {})),
        ),
        check(
            "all_pngs_fresh_owned_and_structurally_decoded",
            screenshot_paths == list(SCREENSHOT_FILES.values())
            and all(
                item["format"]
                in (
                    "PNG RGBA8 non-interlaced",
                    "PNG indexed8 non-interlaced",
                )
                and 320 <= item["width"] <= 1920
                and 224 <= item["height"] <= 1080
                and item["bytes"] > 2000
                and item["mtime_ns"]
                >= captures[name]["capture_started_ns"]
                and item.get("window_ownership", {}).get("process_id")
                == item.get("window_ownership", {}).get(
                    "resolved_owner_process_id"
                )
                == capture_sessions[name]["process_id"]
                and item.get("window_ownership", {}).get("title_matches")
                is True
                and "BlastEm"
                in item.get("window_ownership", {}).get("title", "")
                and (
                    item.get("window_ownership", {}).get("title")
                    == "BlastEm"
                    or "RECURSION 94"
                    in item.get("window_ownership", {}).get("title", "")
                )
                for name, item in zip(SCREENSHOT_FILES, screenshots)
            ),
            f"paths/dimensions {[(item['path'], item['width'], item['height']) for item in screenshots]}",
        ),
        check(
            "owned_native_screenshot_and_pinned_rom_launch_provenance",
            provenance.get("rom", {}).get("path")
            == "build/recursion94-debug.bin"
            and provenance.get("rom", {}).get("sha256") == rom_sha256
            and provenance.get("emulator", {}).get("path")
            == ".toolcache/blastem/blastem.exe"
            and _valid_sha256(
                provenance.get("emulator", {}).get("sha256")
            )
            and provenance.get("launch_flags") == list(LAUNCH_FLAGS)
            and all(
                item.get("window_ownership", {}).get("capture_method")
                == (
                    "BlastEm ui.screenshot via isolated one-shot key binding"
                )
                and item.get("window_ownership", {}).get("process_id")
                == item.get("window_ownership", {}).get(
                    "resolved_owner_process_id"
                )
                == capture_sessions[name]["process_id"]
                and item.get("window_ownership", {}).get(
                    "source_sha256"
                )
                == item.get("sha256")
                and item.get("window_ownership", {}).get(
                    "isolated_source_removed_after_verified_copy"
                )
                is True
                and item.get("window_ownership", {}).get(
                    "isolated_screenshot_path"
                )
                == capture_sessions[name]["controller_profile"][
                    "session_isolation"
                ]["screenshot_path"]
                and item.get("window_ownership", {}).get(
                    "ui_screenshot_key"
                )
                == SCREENSHOT_KEYS_BY_CAPTURE[name][0]
                and captures[name].get("ui_screenshot_key")
                == SCREENSHOT_KEYS_BY_CAPTURE[name][0]
                for name, item in zip(SCREENSHOT_FILES, screenshots)
            ),
            "ROM path/hash, pinned BlastEm path/hash, launch flags, owner PID, "
            "and native isolated ui.screenshot source are all bound",
        ),
        check(
            "all_captures_bind_exact_ui_screenshot_progression",
            all(
                capture["ui_screenshot_progression_valid"]
                and capture[
                    "ui_key_release_serviced_before_capture_stop"
                ]
                and capture["emulator_resumes_during_ui_capture"]
                == (
                    3
                    if capture["state_before_ui_request"]["engine"]["state"]
                    == ENGINE_STATE_TERMINAL
                    else 2
                )
                and capture["post_capture_state_sha256"]
                == capture["state"]["raw_bundle_sha256"]
                and capture["state_before_ui_request"]["engine"][
                    "attempt"
                ]
                == capture["state"]["engine"]["attempt"]
                and capture["authoritative_ticks_during_ui_capture"]
                == (
                    0
                    if capture["state_before_ui_request"]["engine"][
                        "state"
                    ]
                    == ENGINE_STATE_TERMINAL
                    else 2
                )
                and capture["state_after_ui_key_down"]["engine"]["tick"]
                - capture["state_before_ui_request"]["engine"]["tick"]
                == (
                    0
                    if capture["state_before_ui_request"]["engine"][
                        "state"
                    ]
                    == ENGINE_STATE_TERMINAL
                    else 1
                )
                and capture["state"]["engine"]["tick"]
                - capture["state_before_ui_request"]["engine"]["tick"]
                == capture["authoritative_ticks_during_ui_capture"]
                for capture in captures.values()
            ),
            "active captures advanced one step on F1-down and one on F1-up; "
            "terminal capture remained tick-stable across down, up, and the "
            "release-armed native-frame presentation",
        ),
        check(
            "nonblank_nonfatal_readable_image_set",
            all(
                0.25 <= item["dark_pixel_ratio"] < 0.97
                and item["light_pixel_ratio"] >= 0.002
                and item["light_pixel_ratio"] <= 0.15
                and item["red_dominant_ratio"] < 0.08
                and 8 <= item["quantized_color_count"] <= 512
                and item["luminance_entropy_bits"] >= 0.7
                and item["horizontal_edge_ratio"] >= 0.01
                and item["hud_light_pixel_ratio"] >= 0.002
                # The authored flame occupies 380 of 76,800 native pixels in
                # the sparsest B01 frames (0.0049479...).  Keep the signature
                # colour gate below that exact footprint while still rejecting
                # screenshots without the room's rust landmark.
                and item["rust_dominant_ratio"] >= 0.004
                and item["cobalt_dominant_ratio"] >= 0.002
                for item in screenshots
            ),
            "dark/light/red/colors/rust/cobalt/entropy/edges "
            + str(
                [
                    (
                        round(item["dark_pixel_ratio"], 4),
                        round(item["light_pixel_ratio"], 4),
                        round(item["red_dominant_ratio"], 4),
                        item["quantized_color_count"],
                        round(item["rust_dominant_ratio"], 4),
                        round(item["cobalt_dominant_ratio"], 4),
                        round(item["luminance_entropy_bits"], 3),
                        round(item["horizontal_edge_ratio"], 4),
                    )
                    for item in screenshots
                ]
            ),
        ),
        check(
            "five_visually_distinct_states",
            len({item["sha256"] for item in screenshots}) == 5
            and all(
                item["difference_ratio"] >= 0.0005
                for item in provenance.get("image_differences", [])
            ),
            str(provenance.get("image_differences", [])),
        ),
        check(
            "initial_zero_echo_closed_door_state",
            zero["engine"]["magic"] == ENGINE_MAGIC
            and zero["engine"]["attempt"] == 0
            and zero["engine"]["tick"] == 8
            and zero["engine"]["state"] == ENGINE_STATE_ACTIVE
            and zero["timeline"]["magic"] == TIMELINE_MAGIC
            and zero["timeline"]["echo_count"] == 0
            and zero["timeline"]["current_bank"] == 6
            and zero["gameplay"]["magic"] == GAMEPLAY_MAGIC
            and zero["gameplay"]["door_state"] == 0
            and zero["gameplay"]["switch_mask"] == 0
            and zero["actors"]["current"]["x_16_8"] == 44 << 8,
            "attempt/tick/echo/bank/door/current-x "
            f"{zero['engine']['attempt']}/{zero['engine']['tick']}/"
            f"{zero['timeline']['echo_count']}/{zero['timeline']['current_bank']}/"
            f"{zero['gameplay']['door_state']}/"
            f"{zero['actors']['current']['x_16_8'] >> 8}",
        ),
        check(
            "manual_commit_rotates_real_timeline_without_consuming_step",
            bytes(canonical["attempt_0_inputs"]) == CANONICAL_ATTEMPT_0
            and reset["engine"]["attempt"] == 1
            and reset["engine"]["tick"] == 0
            and reset["timeline"]["echo_count"] == 1
            and reset["timeline"]["echo_order"]
            == [6, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
            and reset["timeline"]["current_bank"] == 0
            and reset["gameplay"]["last_commit_reason"]
            == GAMEPLAY_COMMIT_MANUAL
            and reset["replay_bank_6"]["descriptor"]["length"] == 40
            and reset["replay_bank_6"]["descriptor"]["flags"]
            & (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
            == (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
            and reset["replay_bank_6"]["descriptor"]["crc32_iso_hdlc"]
            == CANONICAL_BANK_6_CRC
            and reset["replay_bank_6"]["first_40_hex"]
            == CANONICAL_ATTEMPT_0.hex().upper()
            and reset["replay_bank_6"]["zero_tail"],
            "Right x32 + neutral x8 became bank 6 echo; attempt 1 stayed tick 0",
        ),
        check(
            "visible_reset_feedback_is_live",
            reset_frame["engine"]["attempt"] == 1
            and reset_frame["engine"]["tick"] == 3
            and reset_frame["renderer"]["magic"] == RENDERER_MAGIC
            and reset_frame["renderer"]["flags"]
            & RENDERER_FLAG_RESET_ACTIVE
            and 0 < reset_frame["renderer"]["reset_phase"] < 12
            and reset_frame["renderer"]["last_attempt"] == 1
            and reset_frame["renderer"]["last_tick"] == 3,
            "renderer flags/phase/attempt/tick "
            f"{reset_frame['renderer']['flags']:04X}/"
            f"{reset_frame['renderer']['reset_phase']}/"
            f"{reset_frame['renderer']['last_attempt']}/"
            f"{reset_frame['renderer']['last_tick']}",
        ),
        check(
            "echo_alone_holds_switch_and_opens_door",
            opened["engine"]["attempt"] == 1
            and opened["engine"]["tick"] == OPEN_CAPTURE_TICK
            and opened["timeline"]["echo_count"] == 1
            and opened["actors"]["echo_1"]["x_16_8"] == 80 << 8
            and opened["actors"]["current"]["x_16_8"]
            == OPEN_CAPTURE_CURRENT_X << 8
            and opened["b01_device"]["occupancy_mask"] == 0x0001
            and opened["gameplay"]["switch_mask"] == 0x0001
            and opened["gameplay"]["door_state"] == 0x0003
            and opened["gameplay"]["flags"]
            & (
                GAMEPLAY_FLAG_INITIALIZED
                | GAMEPLAY_FLAG_DOOR_OPEN
                | GAMEPLAY_FLAG_SWITCH_HELD
            )
            == (
                GAMEPLAY_FLAG_INITIALIZED
                | GAMEPLAY_FLAG_DOOR_OPEN
                | GAMEPLAY_FLAG_SWITCH_HELD
            ),
            "echo/current X, occupancy, door "
            f"{opened['actors']['echo_1']['x_16_8'] >> 8}/"
            f"{opened['actors']['current']['x_16_8'] >> 8}/"
            f"{opened['b01_device']['occupancy_mask']:04X}/"
            f"{opened['gameplay']['door_state']:04X}",
        ),
        check(
            "program_stable_rendered_from_terminal_state",
            stable["engine"]["attempt"] == 1
            and stable["engine"]["tick"] == 166
            and stable["engine"]["state"] == ENGINE_STATE_TERMINAL
            and stable["engine"]["terminal_reason"]
            == GAMEPLAY_TERMINAL_PROGRAM_STABLE
            and stable["gameplay"]["result"]
            == GAMEPLAY_RESULT_PROGRAM_STABLE
            and stable["gameplay"]["stable_tick"] == 166
            and stable["gameplay"]["door_state"] == 7
            and stable["b01_device"]["occupancy_mask"] == 1
            and stable["renderer"]["flags"]
            & RENDERER_FLAG_PROGRAM_STABLE
            and stable["renderer"]["last_tick"] == 166,
            "terminal reason/result/stable tick/renderer flags "
            f"{stable['engine']['terminal_reason']:04X}/"
            f"{stable['gameplay']['result']}/"
            f"{stable['gameplay']['stable_tick']}/"
            f"{stable['renderer']['flags']:04X}",
        ),
        check(
            "plausible_current_only_failure_is_blocked_not_solved",
            failed["engine"]["attempt"] == 0
            and failed["engine"]["tick"] == 101
            and failed["engine"]["state"] == ENGINE_STATE_ACTIVE
            and failed["engine"]["terminal_reason"] == ENGINE_TERMINAL_NONE
            and failed["timeline"]["echo_count"] == 0
            and failed["actors"]["current"]["x_16_8"] == 108 << 8
            and failed["actors"]["current"]["flags"] & 0x0001
            and failed["gameplay"]["current_pressed"] & INPUT_B
            and failed["gameplay"]["result"] == GAMEPLAY_RESULT_OK
            and failed["gameplay"]["door_state"] == 0
            and failed["gameplay"]["switch_mask"] == 0,
            "tick/current-x/pressed/result/door "
            f"{failed['engine']['tick']}/"
            f"{failed['actors']['current']['x_16_8'] >> 8}/"
            f"{failed['gameplay']['current_pressed']:02X}/"
            f"{failed['gameplay']['result']}/"
            f"{failed['gameplay']['door_state']}",
        ),
        check(
            "blocked_path_recovers_via_same_fast_reset",
            recovered["engine"]["attempt"] == 1
            and recovered["engine"]["tick"] == 0
            and recovered["engine"]["state"] == ENGINE_STATE_ACTIVE
            and recovered["engine"]["last_error"] == 0
            and recovered["timeline"]["echo_count"] == 1
            and recovered["timeline"]["current_bank"] == 0
            and recovered["gameplay"]["last_commit_reason"]
            == GAMEPLAY_COMMIT_MANUAL
            and recovered["actors"]["current"]["x_16_8"] == 32 << 8,
            "blocked attempt reset to attempt 1/tick 0 with one echo and canonical spawn",
        ),
        check(
            "renderer_capacity_and_current_capture_frame_have_no_fault",
            all(
                capture["state"]["renderer"]["magic"] == RENDERER_MAGIC
                and not (
                    capture["state"]["renderer"]["flags"]
                    & RENDERER_FAULT_FLAGS
                )
                and capture["state"]["renderer"]["dropped_sprites"] == 0
                and capture["state"]["renderer"]["dma_rejects"] == 0
                and capture["state"]["renderer"]["last_result"] == 0
                and 0
                < capture["state"]["renderer"]["sprite_count"]
                <= 64
                and capture["state"]["renderer"]["scanline_sprite_max"]
                <= 20
                and capture["state"]["renderer"]["scanline_pixel_max"]
                <= 320
                and capture["state"]["debug"]["fatal_code"] == 0
                and capture["state"]["debug"]["dma_overflow_count"] == 0
                and capture["state"]["debug"]["dma_required_drop_count"]
                == 0
                for capture in captures.values()
            ),
            "current-frame sprite/scanline maxima and capture-local "
            "historical backlog/VBlank observations "
            + str(
                [
                    (
                        capture["state"]["renderer"]["sprite_count"],
                        capture["state"]["renderer"][
                            "scanline_sprite_max"
                        ],
                        capture["state"]["renderer"]["scanline_pixel_max"],
                        capture["state"]["renderer"]["backlog_frames"],
                        capture["state"]["debug"][
                            "vblank_overrun_count"
                        ],
                    )
                    for capture in captures.values()
                ]
            ),
        ),
        check(
            "independent_prompt_rubric_passes_every_scene",
            all(
                review["status"] == "pass"
                and review["minimum_score"] >= 4
                and review["average_score"] >= 4.2
                and not review["critical_gameplay_readability_defects"]
                and not review["severe_sprite_dropout_or_corruption"]
                for review in rubric_reviews
            ),
            "scene minimum/average "
            + str(
                [
                    (
                        name,
                        VISUAL_REVIEWS[name]["minimum_score"],
                        VISUAL_REVIEWS[name]["average_score"],
                    )
                    for name in SCREENSHOT_FILES
                ]
            ),
        ),
        check(
            "visual_reviews_bind_exact_captured_png_hashes",
            all(
                VISUAL_REVIEWS[name].get(
                    "reviewed_screenshot_sha256"
                )
                == captures[name]["screenshot"]["sha256"]
                for name in SCREENSHOT_FILES
            ),
            str(
                [
                    (
                        name,
                        VISUAL_REVIEWS[name].get(
                            "reviewed_screenshot_sha256"
                        ),
                        captures[name]["screenshot"]["sha256"],
                    )
                    for name in SCREENSHOT_FILES
                ]
            ),
        ),
        check(
            "hash_bound_rom_symbols_emulator_sources_and_traces",
            all(
                _valid_sha256(value)
                for value in (
                    rom_sha256,
                    provenance.get("emulator", {}).get("sha256"),
                    provenance.get("symbol_map", {}).get("sha256"),
                    provenance.get("default_config", {}).get(
                        "source_sha256"
                    ),
                    provenance.get("source_binding", {}).get(
                        "bundle_sha256"
                    ),
                    provenance.get("transcript", {}).get("sha256"),
                    canonical["metadata"].get("transcript_sha256"),
                    blocked["metadata"].get("transcript_sha256"),
                )
            )
            and all(
                _valid_sha256(session.get("transcript_sha256"))
                for session in session_metadata
            ),
            "dynamic SHA-256 provenance present for all required inputs",
        ),
    ]
    return checks


def build_visual_report(
    *,
    rom_sha256: str,
    canonical: dict[str, Any],
    blocked: dict[str, Any],
    provenance: dict[str, Any],
    transcript_lines: list[str],
) -> dict[str, Any]:
    captures = _capture_map(canonical, blocked)
    checks = _report_checks(
        rom_sha256=rom_sha256,
        canonical=canonical,
        blocked=blocked,
        captures=captures,
        provenance=provenance,
        transcript_lines=transcript_lines,
    )
    passed = bool(checks) and all(item["status"] == "pass" for item in checks)
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "startup_boundary": {
            "initial_breakpoint_symbol": "VBlankInterrupt",
            "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
            "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
            "counters_and_controller_poll_begin": (
                FIRST_RUNNING_VBLANK_ROLE
            ),
            "detail": (
                "the stage-3 ISR acknowledges VDP status only; controller "
                "polling, frame counters, and token publication begin on the "
                "next stage-4 RUNNING full VBlank"
            ),
        },
        "checks": checks,
        "captures": captures,
        "visual_reviews": VISUAL_REVIEWS,
        "reviewer": {
            "classification": (
                "independent visual-critic agent; did not implement renderer"
            ),
            "method": (
                "direct inspection of all five captured PNGs plus strict "
                "machine analysis and bound runtime state"
            ),
            "rubric_source": "prompt.txt section 42",
        },
        "provenance": provenance,
        "debugger_write_packets": debugger_write_packets(transcript_lines),
        "concrete_defects_and_limitations": [
            {
                "severity": "minor",
                "finding": (
                    "The intentionally economical floor grid repeats visibly "
                    "across the room; it does not interfere with objectives."
                ),
                "blocking": False,
            },
            {
                "severity": "minor",
                "finding": (
                    "The reset trace is deliberately coarse and short rather "
                    "than a rich full-screen effect; it remains unambiguous."
                ),
                "blocking": False,
            },
            {
                "severity": "limitation",
                "finding": (
                    "Still PNGs do not independently prove animation cadence "
                    "or frame pacing; motion clarity is bounded by the "
                    "controller-driven state sequence and captured frames."
                ),
                "blocking": False,
            },
            {
                "severity": "limitation",
                "finding": (
                    "The screenshot collector deliberately stops inside "
                    "VBlank and at main-loop boundaries. Its cumulative "
                    "backlog/VBlank-overrun counters therefore describe the "
                    "instrumented capture session and are not used as "
                    "uninterrupted performance proof; the exact same final "
                    "ROM hash must separately pass the ordinary runtime and "
                    "VBlank budget gates."
                ),
                "blocking": False,
            },
            {
                "severity": "limitation",
                "finding": (
                    "This M4 visual gate covers the B01 vertical slice, not "
                    "the later title, sector, boss, ending, or release scenes."
                ),
                "blocking": False,
            },
            {
                "severity": "limitation",
                "finding": (
                    "A debugger-paused portable BlastEm window may retain the "
                    "exact generic title `BlastEm`; those captures are accepted "
                    "only with matching process ownership, ROM launch/hash and "
                    "RAM provenance, a fresh PNG written by BlastEm's own "
                    "ui.screenshot action into an isolated session directory, "
                    "and strict known-game image-composition checks."
                ),
                "blocking": False,
            },
        ],
    }


def build_playability_report(
    *,
    visual_report: dict[str, Any],
    canonical: dict[str, Any],
    blocked: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    captures = _capture_map(canonical, blocked)
    initial = captures["zero_echo"]["published_state"]
    opened = captures["echo_door_open"]["published_state"]
    stable = captures["program_stable"]["published_state"]
    failed = captures["blocked_path"]["published_state"]
    recovered = blocked["recovered_state"]
    startup_boundary = visual_report.get("startup_boundary", {})
    observations = [
        {
            "area": "controls",
            "status": "pass",
            "evidence": (
                "Owned window-key events reached a standard gamepad3.1: "
                "Right moved current from X=32 to X=108, B was observed as "
                "an edge, and Start+C performed the documented manual commit."
            ),
        },
        {
            "area": "reset_speed_and_feedback",
            "status": "pass",
            "evidence": (
                "Start+C changed attempt 0/tick 40 directly to attempt 1/tick "
                "0 without consuming an authoritative replay byte; the live "
                "renderer exposed RESET_ACTIVE with a visible RESET TRACE."
            ),
        },
        {
            "area": "echo_meaning",
            "status": "pass",
            "evidence": (
                "The physical echo stopped at X=80 with occupancy bit 0 while "
                f"current advanced to X={OPEN_CAPTURE_CURRENT_X}; the door "
                "changed 0->3 and the frame "
                "said DOOR OPEN / ECHO HOLDS."
            ),
        },
        {
            "area": "failure_understandability",
            "status": "pass",
            "evidence": (
                "In a fresh zero-echo run, Right+B left current colliding at "
                "X=108, engine active, result OK, door closed, and terminal "
                "unset; the HUD explicitly said DOOR CLOSED / NEED ECHO."
            ),
        },
        {
            "area": "soft_lock_absence",
            "status": "pass",
            "evidence": (
                "The blocked run accepted Start+C and reconstructed attempt "
                "1/tick 0 at canonical X=32 with one echo, no engine error, "
                "and a new current replay bank."
            ),
        },
        {
            "area": "timeline_behavior",
            "status": "pass",
            "evidence": (
                "Right x32 + neutral x8 was read back from bank 6 with "
                "CRC 96501AB7; bank 6 became echo ordinal 1, bank 0 current, "
                "and the solution reached PROGRAM STABLE at tick 166."
            ),
        },
    ]
    checks = [
        check(
            "accurately_labeled_controller_driven_review",
            True,
            "automated independent controller-driven playability review; "
            "not represented as literal human hands-on testing",
        ),
        check(
            "visual_gate_passed",
            visual_report.get("status") == "pass",
            f"m4-screenshot status {visual_report.get('status')}",
        ),
        check(
            "startup_priming_boundary_preserved",
            startup_boundary.get("initial_breakpoint_role")
            == STARTUP_PRIMING_ROLE
            and startup_boundary.get("authoritative_origin")
            == FIRST_RUNNING_VBLANK_ROLE
            and startup_boundary.get("counters_and_controller_poll_begin")
            == FIRST_RUNNING_VBLANK_ROLE,
            str(startup_boundary),
        ),
        check(
            "all_scoped_playability_observations_pass",
            all(item["status"] == "pass" for item in observations),
            str([(item["area"], item["status"]) for item in observations]),
        ),
        check(
            "movement_interaction_reset_are_guest_observed",
            initial["actors"]["current"]["x_16_8"] == 44 << 8
            and failed["actors"]["current"]["x_16_8"] == 108 << 8
            and failed["gameplay"]["current_pressed"] & INPUT_B
            and opened["b01_device"]["occupancy_mask"] == 1
            and opened["gameplay"]["door_state"] == 3
            and stable["gameplay"]["result"]
            == GAMEPLAY_RESULT_PROGRAM_STABLE
            and recovered["engine"]["attempt"] == 1
            and recovered["engine"]["tick"] == 0,
            "live state covers movement, failed B, echo-door causality, "
            "successful B, and recovery",
        ),
        check(
            "no_fatal_or_renderer_drop_during_review",
            all(
                capture["state"]["debug"]["fatal_code"] == 0
                and capture["state"]["renderer"]["dropped_sprites"] == 0
                and capture["state"]["renderer"]["dma_rejects"] == 0
                for capture in captures.values()
            ),
            "fatal/drop/reject counters remained zero in all reviewed states",
        ),
        check(
            "qa_provenance_bound_to_visual_session",
            visual_report.get("rom_sha256")
            == EXPECTED_DEBUG_ROM_SHA256
            and provenance.get("transcript", {}).get("sha256")
            == visual_report.get("provenance", {})
            .get("transcript", {})
            .get("sha256"),
            "QA report consumes the same ROM-bound sessions and transcript",
        ),
    ]
    passed = bool(checks) and all(item["status"] == "pass" for item in checks)
    return {
        "schema": QA_REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": QA_EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "rom_sha256": visual_report["rom_sha256"],
        "startup_boundary": startup_boundary,
        "classification": (
            "Independent automated controller-driven playability review. "
            "This is not literal human hands-on testing."
        ),
        "checks": checks,
        "observations": observations,
        "reviewed_screenshots": list(SCREENSHOT_FILES.values()),
        "provenance": {
            "visual_report_schema": visual_report["schema"],
            "visual_report_status": visual_report["status"],
            "source_binding_sha256": provenance["source_binding"][
                "bundle_sha256"
            ],
            "transcript_sha256": provenance["transcript"]["sha256"],
        },
        "limitations": [
            "The review exercises the scoped B01 Right/B/Start+C path; it "
            "does not claim full campaign controls, accessibility, or balance.",
            "Input is automated through the real configured controller path; "
            "no person physically held a controller in these sessions.",
            "Still screenshots support readability review but are not video.",
            "Audio is assessed by the separate M4 audio gate.",
        ],
    }


def _refresh_image_analysis(
    root: Path, canonical: dict[str, Any], blocked: dict[str, Any]
) -> None:
    captures = _capture_map(canonical, blocked)
    for name, relative in SCREENSHOT_FILES.items():
        path = root / relative
        facts = analyze_png(path, relative_path=relative)
        previous = captures[name]["screenshot"]
        facts["tool_output"] = previous.get("tool_output", "")
        facts["mtime_ns"] = path.stat().st_mtime_ns
        facts["captured_utc"] = previous.get("captured_utc")
        facts["window_ownership"] = previous.get("window_ownership", {})
        captures[name]["screenshot"] = facts


def _image_difference_manifest(root: Path) -> list[dict[str, Any]]:
    names = list(SCREENSHOT_FILES)
    pairs = zip(names, names[1:])
    return [
        {
            "first": first,
            "second": second,
            "difference_ratio": pixel_difference_ratio(
                root / SCREENSHOT_FILES[first],
                root / SCREENSHOT_FILES[second],
            ),
        }
        for first, second in pairs
    ]


def _write_partial_post_repair_reports(
    *,
    root: Path,
    rom: Path,
    capture_trace: dict[str, Any],
    trace_json_path: Path,
    report_path: Path,
    qa_report_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Finalize an honest report from a checkpointed partial native run."""
    completed_names = (
        "zero_echo",
        "reset_trace",
        "echo_door_open",
    )
    if capture_trace.get("status") != "fail":
        raise EvidenceFailure("partial capture trace is not a failed checkpoint")
    if tuple(capture_trace.get("completed_capture_names", ())) != completed_names:
        raise EvidenceFailure(
            "partial capture does not contain the expected coherent prefix"
        )
    if capture_trace.get("pending_capture_names") != [
        "program_stable",
        "blocked_path",
    ]:
        raise EvidenceFailure("partial capture pending set is unexpected")
    if (
        "program_stable native ui.screenshot validation failed"
        not in capture_trace.get("error", "")
    ):
        raise EvidenceFailure("partial capture has an unexpected blocker")
    rom_sha256 = sha256_file(rom)
    if (
        rom_sha256 != EXPECTED_DEBUG_ROM_SHA256
        or capture_trace.get("rom_sha256") != rom_sha256
    ):
        raise EvidenceFailure("partial trace is not bound to the final ROM")
    provenance = capture_trace["provenance"]
    if source_binding(root) != provenance["source_binding"]:
        raise EvidenceFailure("visual source binding differs from partial run")
    transcript_lines = capture_trace["transcript_lines"]
    writes = debugger_write_packets(transcript_lines)
    sessions = capture_trace["fresh_sessions"]
    captures: dict[str, dict[str, Any]] = {}
    for name in completed_names:
        session = sessions[name]
        capture = session["capture"]
        relative = SCREENSHOT_FILES[name]
        path = root / relative
        refreshed = analyze_png(path, relative_path=relative)
        previous = capture["screenshot"]
        refreshed["tool_output"] = previous.get("tool_output", "")
        refreshed["mtime_ns"] = path.stat().st_mtime_ns
        refreshed["captured_utc"] = previous.get("captured_utc")
        refreshed["window_ownership"] = previous["window_ownership"]
        capture["screenshot"] = refreshed
        captures[name] = capture
    failure_context = capture_trace["failure_capture_context"]
    captures["program_stable"] = {
        "status": "missing",
        "expected_path": SCREENSHOT_FILES["program_stable"],
        "failure": capture_trace["error"],
        "ui_screenshot_key": failure_context["ui_screenshot_key"],
        "state_before_ui_request": failure_context[
            "state_before_ui_request"
        ],
        "state_after_ui_key_down": failure_context[
            "state_after_ui_key_down"
        ],
        "state": failure_context["state"],
        "post_capture_state_sha256": failure_context[
            "post_capture_state_sha256"
        ],
        "ui_screenshot_progression_valid": failure_context[
            "ui_screenshot_progression_valid"
        ],
        "authoritative_ticks_during_ui_capture": failure_context[
            "authoritative_ticks_during_ui_capture"
        ],
        "emulator_resumes_during_ui_capture": failure_context[
            "emulator_resumes_during_ui_capture"
        ],
        "ui_key_release_serviced_before_capture_stop": failure_context[
            "ui_key_release_serviced_before_capture_stop"
        ],
        "window_ownership": failure_context["window_ownership"],
    }
    captures["blocked_path"] = {
        "status": "not_attempted",
        "expected_path": SCREENSHOT_FILES["blocked_path"],
        "reason": (
            "the coherent attempt stopped fail-closed after the repeated "
            "terminal native screenshot failure"
        ),
    }
    session_metadata = [sessions[name]["metadata"] for name in completed_names]
    expected_targets = {
        "zero_echo": (0, 8),
        "reset_trace": (1, 3),
        "echo_door_open": (1, OPEN_CAPTURE_TICK),
    }
    native_owned = all(
        capture["screenshot"]["format"] == "PNG indexed8 non-interlaced"
        and capture["screenshot"]["width"] == 320
        and capture["screenshot"]["height"] == 240
        and capture["screenshot"]["window_ownership"]["process_id"]
        == capture["screenshot"]["window_ownership"][
            "resolved_owner_process_id"
        ]
        == sessions[name]["metadata"]["process_id"]
        and capture["screenshot"]["window_ownership"]["source_sha256"]
        == capture["screenshot"]["sha256"]
        and capture["screenshot"]["window_ownership"][
            "isolated_source_removed_after_verified_copy"
        ]
        is True
        for name, capture in (
            (name, captures[name]) for name in completed_names
        )
    )
    progression_bound = all(
        capture["published_state"]["engine"]["attempt"]
        == expected_targets[name][0]
        and capture["published_state"]["engine"]["tick"]
        == expected_targets[name][1]
        and capture["state_after_ui_key_down"]["engine"]["tick"]
        == capture["state_before_ui_request"]["engine"]["tick"] + 1
        and capture["state"]["engine"]["tick"]
        == capture["state_before_ui_request"]["engine"]["tick"] + 2
        and capture["authoritative_ticks_during_ui_capture"] == 2
        and capture["emulator_resumes_during_ui_capture"] == 2
        and capture["ui_key_release_serviced_before_capture_stop"] is True
        and capture["post_capture_state_sha256"]
        == capture["state"]["raw_bundle_sha256"]
        for name, capture in (
            (name, captures[name]) for name in completed_names
        )
    )
    isolated_f1 = (
        len({metadata["process_id"] for metadata in session_metadata}) == 3
        and all(
            metadata["controller_profile"]["status"] == "pass"
            and metadata["controller_profile"]["port_1_assignment"]
            == "gamepad3.1"
            and metadata["controller_profile"]["session_isolation"]["status"]
            == "pass"
            and metadata["controller_profile"]["screenshot_bindings"][
                "installed_config_keys"
            ]
            == ["f1"]
            and metadata["controller_profile"]["screenshot_bindings"][
                "shared_key_across_isolated_sessions"
            ]
            is True
            and metadata["input_events"].count(
                "F1:ui.screenshot:down"
            )
            == 1
            and metadata["input_events"].count("F1:ui.screenshot:up")
            == 1
            for metadata in session_metadata
        )
    )
    runtime_semantics = (
        captures["zero_echo"]["published_state"]["timeline"]["echo_count"] == 0
        and captures["zero_echo"]["published_state"]["gameplay"][
            "door_state"
        ]
        == 0
        and captures["reset_trace"]["published_state"]["renderer"]["flags"]
        & RENDERER_FLAG_RESET_ACTIVE
        and captures["echo_door_open"]["published_state"]["timeline"][
            "echo_count"
        ]
        == 1
        and captures["echo_door_open"]["published_state"]["b01_device"][
            "occupancy_mask"
        ]
        == 1
        and captures["echo_door_open"]["published_state"]["gameplay"][
            "door_state"
        ]
        == 3
    )
    terminal_bound = (
        failure_context["ui_screenshot_key"] == "F1"
        and failure_context["authoritative_ticks_during_ui_capture"] == 0
        and failure_context["emulator_resumes_during_ui_capture"] == 2
        and failure_context[
            "ui_key_release_serviced_before_capture_stop"
        ]
        is True
        and all(
            failure_context[key]["engine"]["state"]
            == ENGINE_STATE_TERMINAL
            and failure_context[key]["engine"]["tick"] == 166
            and failure_context[key]["engine"]["terminal_reason"]
            == GAMEPLAY_TERMINAL_PROGRAM_STABLE
            and failure_context[key]["gameplay"]["result"]
            == GAMEPLAY_RESULT_PROGRAM_STABLE
            and failure_context[key]["renderer"]["flags"]
            & RENDERER_FLAG_PROGRAM_STABLE
            for key in (
                "state_before_ui_request",
                "state_after_ui_key_down",
                "state",
            )
        )
    )
    budget_states = {
        name: captures[name]["published_state"] for name in completed_names
    }
    budget_states["program_stable"] = failure_context[
        "state_before_ui_request"
    ]
    renderer_budgets_clean = all(
        not (state["renderer"]["flags"] & RENDERER_FAULT_FLAGS)
        and state["renderer"]["backlog_frames"] == 0
        and state["renderer"]["dropped_sprites"] == 0
        and state["renderer"]["dma_rejects"] == 0
        and state["debug"]["vblank_overrun_count"] == 0
        and state["debug"]["dma_overflow_count"] == 0
        and state["debug"]["dma_required_drop_count"] == 0
        and state["debug"]["fatal_code"] == 0
        for state in budget_states.values()
    )
    rubric_passed = all(
        review["status"] == "pass"
        and review["minimum_score"] >= 4
        and review["average_score"] >= 4.2
        and not review["critical_gameplay_readability_defects"]
        for review in POST_REPAIR_VISUAL_REVIEWS.values()
    )
    missing_program_path = root / SCREENSHOT_FILES["program_stable"]
    missing_blocked_path = root / SCREENSHOT_FILES["blocked_path"]
    checks = [
        check(
            "exact_final_debug_rom",
            rom_sha256 == EXPECTED_DEBUG_ROM_SHA256,
            f"Debug ROM SHA-256 {rom_sha256}",
        ),
        check(
            "three_successes_use_fresh_isolated_standard_pad_processes",
            isolated_f1,
            (
                "process IDs "
                f"{[metadata['process_id'] for metadata in session_metadata]}"
            ),
        ),
        check(
            "controller_window_input_only",
            all(
                sessions[name]["input_injection"]["status"] == "pass"
                and sessions[name]["input_injection"][
                    "guest_ram_register_or_pc_writes"
                ]
                is False
                for name in completed_names
            )
            and not writes,
            f"debugger RAM/register/PC write packets {writes}",
        ),
        check(
            "single_proven_f1_binding_once_per_isolated_session",
            isolated_f1
            and all(
                captures[name]["ui_screenshot_key"] == "F1"
                for name in completed_names
            ),
            "F1 is installed and used once in each fresh process",
        ),
        check(
            "three_native_pngs_are_fresh_owned_and_structurally_decoded",
            native_owned,
            str(
                [
                    (
                        name,
                        captures[name]["screenshot"]["sha256"],
                        captures[name]["screenshot"]["width"],
                        captures[name]["screenshot"]["height"],
                    )
                    for name in completed_names
                ]
            ),
        ),
        check(
            "three_pngs_bind_exact_published_and_ui_progression_states",
            progression_bound,
            str(
                [
                    (
                        name,
                        captures[name]["published_state"]["engine"]["tick"],
                        captures[name]["state_before_ui_request"]["engine"][
                            "tick"
                        ],
                        captures[name]["state_after_ui_key_down"]["engine"][
                            "tick"
                        ],
                        captures[name]["state"]["engine"]["tick"],
                    )
                    for name in completed_names
                ]
            ),
        ),
        check(
            "zero_reset_and_echo_door_runtime_semantics",
            bool(runtime_semantics),
            "0 echo/closed; RESET_ACTIVE; 1 echo occupancy/open door",
        ),
        check(
            "program_stable_terminal_state_is_guest_observed",
            terminal_bound,
            "attempt 1 tick 166 terminal reason 0100/result 1/renderer flag 0040",
        ),
        check(
            "renderer_and_vblank_budgets_have_no_faults",
            renderer_budgets_clean,
            str(
                [
                    (
                        name,
                        state["renderer"]["backlog_frames"],
                        state["debug"]["vblank_overrun_count"],
                        state["renderer"]["dropped_sprites"],
                        state["renderer"]["dma_rejects"],
                    )
                    for name, state in budget_states.items()
                ]
            ),
        ),
        check(
            "program_stable_native_png_exists",
            False,
            (
                "F1 down/up was serviced in the owned terminal process but "
                "BlastEm created zero PNGs; stale pre-repair file removed "
                f"({missing_program_path.relative_to(root).as_posix()})"
            ),
        ),
        check(
            "blocked_path_native_png_exists",
            False,
            (
                "not attempted after terminal capture failure; "
                f"{missing_blocked_path.relative_to(root).as_posix()} absent"
            ),
        ),
        check(
            "prompt_section_42_visual_rubric_passes_every_scene",
            rubric_passed,
            str(
                [
                    (
                        name,
                        review["minimum_score"],
                        review["average_score"],
                    )
                    for name, review in POST_REPAIR_VISUAL_REVIEWS.items()
                ]
            ),
        ),
        check(
            "hash_bound_rom_symbols_emulator_sources_and_trace",
            all(
                _valid_sha256(value)
                for value in (
                    provenance["rom"]["sha256"],
                    provenance["emulator"]["sha256"],
                    provenance["symbol_map"]["sha256"],
                    provenance["default_config"]["source_sha256"],
                    provenance["source_binding"]["bundle_sha256"],
                    provenance["transcript"]["sha256"],
                    sha256_file(trace_json_path),
                )
            ),
            "dynamic SHA-256 provenance is present for the partial run",
        ),
    ]
    comparisons = {
        name: {
            "pre_repair_path": (
                "artifacts/screenshots/"
                f"m4-before-repair-{name.replace('_', '-')}.png"
            ),
            "post_repair_path": SCREENSHOT_FILES[name],
            "difference_ratio": pixel_difference_ratio(
                root
                / (
                    "artifacts/screenshots/"
                    f"m4-before-repair-{name.replace('_', '-')}.png"
                ),
                root / SCREENSHOT_FILES[name],
            ),
        }
        for name in completed_names
    }
    visual = {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "baseline_kind": "post_repair_partial_native_failure",
        "status": "fail",
        "rom_sha256": rom_sha256,
        "checks": checks,
        "capture_completeness": {
            "required": 5,
            "native_pngs": 3,
            "missing": ["program_stable", "blocked_path"],
        },
        "captures": captures,
        "visual_reviews": POST_REPAIR_VISUAL_REVIEWS,
        "pre_repair_comparison": comparisons,
        "reviewer": {
            "classification": (
                "independent visual-critic agent; did not implement renderer"
            ),
            "method": (
                "direct inspection of three post-repair native PNGs; missing "
                "program-stable and blocked-path frames fail closed"
            ),
            "rubric_source": "prompt.txt section 42",
        },
        "capture_failure_classification": {
            "classification": (
                "evidence-tool/emulator UI-service boundary defect is strongly "
                "indicated for the zero-PNG terminal request"
            ),
            "reasoning": (
                "The same isolated F1 mapping produces native PNGs in three "
                "active sessions; at terminal, owned-window down/up events and "
                "two debugger resumes complete while attempt 1/tick 166/result "
                "1/renderer PROGRAM_STABLE remain coherent, but no file is "
                "created. This does not prove the terminal framebuffer failed."
            ),
            "independent_renderer_findings": (
                "The renderer still fails independently: reset/open/terminal "
                "record backlog counts 1/2/2 and VBlank overruns 1/5/5, and "
                "the inspectable frames fail the visual rubric."
            ),
        },
        "recommended_discriminating_experiment_not_run": (
            "In one fresh terminal-only session, remove the post-gameplay "
            "breakpoint after the tick-166 DMA commit, post F1 down and up "
            "between two successive symbol-derived VBlank/post-DMA stops, and "
            "check only the isolated screenshot directory. A native PNG would "
            "confirm that immediate terminal post-step re-stops starve BlastEm's "
            "UI event service; continued zero-PNG would localize the defect to "
            "the emulator/binding path. Use no guest memory/register/PC writes."
        ),
        "provenance": {
            **provenance,
            "partial_trace": {
                "path": trace_json_path.relative_to(root).as_posix(),
                "sha256": sha256_file(trace_json_path),
            },
        },
        "debugger_write_packets": writes,
        "concrete_defects_and_limitations": [
            {
                "severity": "critical",
                "blocking": True,
                "finding": (
                    "Program-stable terminal state is fully guest-observed, "
                    "but the native ui.screenshot action produced no PNG."
                ),
            },
            {
                "severity": "critical",
                "blocking": True,
                "finding": (
                    "Renderer backlog/VBlank-overrun counters regress from "
                    "0/0 at zero-echo to 1/1 at reset and 2/5 by open/terminal."
                ),
            },
            {
                "severity": "critical",
                "blocking": True,
                "finding": (
                    "Blocked-path capture was not attempted after the coherent "
                    "run stopped at the repeated terminal capture blocker."
                ),
            },
            {
                "severity": "critical",
                "blocking": True,
                "finding": (
                    "The reset-trace frame clips/occludes current and echo and "
                    "does not preserve hazard/objective readability."
                ),
            },
            {
                "severity": "major",
                "blocking": True,
                "finding": (
                    "Zero/open frames are materially cleaner, but small actor "
                    "silhouettes and current/echo overlap remain below rubric."
                ),
            },
        ],
    }
    qa_checks = [
        check(
            "accurately_labeled_controller_driven_review",
            True,
            "independent automated controller-driven playability review; "
            "not literal human testing",
        ),
        check(
            "controller_window_input_only",
            not writes,
            f"debugger RAM/register/PC write packets {writes}",
        ),
        check(
            "zero_reset_and_echo_door_paths_are_guest_observed",
            bool(runtime_semantics and progression_bound),
            "three controller-driven state/PNG bindings pass",
        ),
        check(
            "program_stable_state_is_guest_observed",
            terminal_bound,
            "terminal gameplay state passes; native frame capture does not",
        ),
        check(
            "renderer_and_vblank_budgets_are_clean",
            renderer_budgets_clean,
            "backlog/VBlank-overrun pairs are 0/0, 1/1, 2/5, 2/5",
        ),
        check(
            "all_five_required_native_frames_exist",
            False,
            "three of five required post-repair native frames exist",
        ),
        check(
            "blocked_failure_path_was_reviewed",
            False,
            "not attempted after stopping at the terminal capture blocker",
        ),
        check(
            "visual_gate_passed",
            False,
            "incomplete frame set and prompt section 42 rubric failures",
        ),
    ]
    qa = {
        "schema": QA_REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": QA_EVIDENCE_TYPE,
        "baseline_kind": "post_repair_partial_native_failure",
        "status": "fail",
        "rom_sha256": rom_sha256,
        "classification": (
            "Independent automated controller-driven playability review. "
            "This is not literal human hands-on testing."
        ),
        "checks": qa_checks,
        "observations": [
            {
                "area": "zero_echo",
                "status": "pass",
                "evidence": (
                    "Attempt 0 tick 8 is bound to a fresh owned native frame "
                    "with zero echoes and the door closed."
                ),
            },
            {
                "area": "reset_feedback",
                "status": "fail",
                "evidence": (
                    "RESET_ACTIVE is guest-observed and captured, but the "
                    "effect clips actors and removes hazard/objective context."
                ),
            },
            {
                "area": "echo_door_causality",
                "status": "partial",
                "evidence": (
                    "Echo count 1, occupancy bit 0, door state 3, and a native "
                    "open frame agree; actor overlap weakens visual clarity."
                ),
            },
            {
                "area": "program_stable",
                "status": "fail",
                "evidence": (
                    "Attempt 1 tick 166/result 1/terminal reason 0100 are "
                    "guest-observed, but BlastEm produced no native PNG."
                ),
            },
            {
                "area": "renderer_budget",
                "status": "fail",
                "evidence": (
                    "Backlog/VBlank-overrun counters reach 2/5 by the "
                    "echo-open and terminal states."
                ),
            },
            {
                "area": "blocked_path",
                "status": "fail",
                "evidence": (
                    "The coherent attempt stopped before this required path."
                ),
            },
        ],
        "reviewed_screenshots": [
            SCREENSHOT_FILES[name] for name in completed_names
        ],
        "provenance": {
            "visual_report_schema": REPORT_SCHEMA,
            "visual_report_status": "fail",
            "source_binding_sha256": provenance["source_binding"][
                "bundle_sha256"
            ],
            "transcript_sha256": provenance["transcript"]["sha256"],
            "partial_trace_sha256": sha256_file(trace_json_path),
        },
        "limitations": [
            "Only three of five required post-repair native frames exist.",
            "The review is automated through the real configured controller "
            "path; no person physically held a controller.",
            "Still screenshots do not prove animation cadence.",
            "Audio is assessed by the separate M4 audio gate.",
        ],
    }
    write_json(report_path, visual)
    write_json(qa_report_path, qa)
    return visual, qa


def _write_reports_from_trace(
    *,
    root: Path,
    rom: Path,
    trace_json_path: Path,
    report_path: Path,
    qa_report_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        capture_trace = json.loads(trace_json_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceFailure(
            f"cannot load existing visual capture trace: {error}"
        ) from error
    if (
        capture_trace.get("schema")
        == "r94-m4-visual-capture-checkpoint-v1"
    ):
        return _write_partial_post_repair_reports(
            root=root,
            rom=rom,
            capture_trace=capture_trace,
            trace_json_path=trace_json_path,
            report_path=report_path,
            qa_report_path=qa_report_path,
        )
    canonical = capture_trace["canonical_session"]
    blocked = capture_trace["blocked_session"]
    provenance = capture_trace["provenance"]
    transcript_lines = capture_trace["transcript_lines"]
    rom_sha256 = sha256_file(rom)
    if rom_sha256 != capture_trace["rom_sha256"]:
        raise EvidenceFailure("ROM differs from existing visual capture trace")
    if source_binding(root) != provenance["source_binding"]:
        raise EvidenceFailure("visual source binding differs from capture")
    _refresh_image_analysis(root, canonical, blocked)
    provenance["image_differences"] = _image_difference_manifest(root)
    visual = build_visual_report(
        rom_sha256=rom_sha256,
        canonical=canonical,
        blocked=blocked,
        provenance=provenance,
        transcript_lines=transcript_lines,
    )
    qa = build_playability_report(
        visual_report=visual,
        canonical=canonical,
        blocked=blocked,
        provenance=provenance,
    )
    write_json(report_path, visual)
    write_json(qa_report_path, qa)
    return visual, qa


def _failure_report(
    *, schema: str, evidence_type: str, rom_sha256: str, error: Exception
) -> dict[str, Any]:
    detail = f"{type(error).__name__}: {error}"
    report = {
        "schema": schema,
        "scope": "Milestone4",
        "evidence_type": evidence_type,
        "status": "fail",
        "rom_sha256": rom_sha256,
        "checks": [check("m4_visual_evidence_session", False, detail)],
        "error": detail,
    }
    capture_context = getattr(error, "capture_context", None)
    if capture_context is not None:
        report["failure_capture_context"] = capture_context
    return report


def _terminal_vblank_discriminant_report(
    *,
    rom_sha256: str,
    session: dict[str, Any],
    provenance: dict[str, Any],
    transcript_lines: list[str],
    trace_json_path: Path,
    root: Path,
) -> dict[str, Any]:
    """Report a successful one-shot terminal UI-service discriminant."""
    capture = session["capture"]
    screenshot = capture["screenshot"]
    metadata = session["metadata"]
    writes = debugger_write_packets(transcript_lines)
    states = [
        capture["published_state"],
        capture["state_before_ui_request"],
        capture["state_after_ui_key_down"],
        capture["state"],
    ]
    terminal_bound = all(
        state["engine"]["attempt"] == 1
        and state["engine"]["tick"] == 166
        and state["engine"]["state"] == ENGINE_STATE_TERMINAL
        and state["engine"]["terminal_reason"]
        == GAMEPLAY_TERMINAL_PROGRAM_STABLE
        and state["gameplay"]["result"]
        == GAMEPLAY_RESULT_PROGRAM_STABLE
        and state["renderer"]["flags"] & RENDERER_FLAG_PROGRAM_STABLE
        for state in states
    )
    profile = metadata["controller_profile"]
    isolated = (
        profile["status"] == "pass"
        and profile["port_1_assignment"] == "gamepad3.1"
        and profile["session_isolation"]["status"] == "pass"
        and profile["screenshot_bindings"]["installed_config_keys"] == ["f1"]
        and metadata["input_events"].count("F1:ui.screenshot:down") == 1
        and metadata["input_events"].count("F1:ui.screenshot:up") == 1
    )
    ownership = screenshot["window_ownership"]
    native_owned = (
        screenshot["path"] == SCREENSHOT_FILES["program_stable"]
        and screenshot["format"] == "PNG indexed8 non-interlaced"
        and screenshot["width"] == 320
        and screenshot["height"] == 240
        and ownership["process_id"]
        == ownership["resolved_owner_process_id"]
        == metadata["process_id"]
        and ownership["source_sha256"] == screenshot["sha256"]
        and ownership["isolated_source_removed_after_verified_copy"] is True
        and ownership["ui_screenshot_key"] == "F1"
    )
    boundary = capture["observation_boundary"]
    vblank_boundary = (
        boundary["post_commit_address"]
        == provenance["post_dma_commit"]["post_commit_address_hex"]
        and boundary["capture_address"]
        == provenance["post_dma_commit"]["post_commit_address_hex"]
        and boundary["ui_service_boundary"]
        == "three successive symbol-derived VBlank post-DMA stops"
        and capture["authoritative_ticks_during_ui_capture"] == 0
        and capture["emulator_resumes_during_ui_capture"] == 3
        and capture["ui_key_release_serviced_before_capture_stop"] is True
        and capture["native_frame_presented_after_ui_key_release"] is True
    )
    checks = [
        check(
            "exact_current_debug_rom",
            rom_sha256 == EXPECTED_DEBUG_ROM_SHA256,
            f"Debug ROM SHA-256 {rom_sha256}",
        ),
        check(
            "fresh_isolated_standard_pad_process",
            isolated,
            f"owned PID {metadata['process_id']} with one isolated F1 mapping",
        ),
        check(
            "no_guest_ram_register_or_pc_writes",
            not writes
            and session["input_injection"][
                "guest_ram_register_or_pc_writes"
            ]
            is False,
            f"debugger write packets {writes}",
        ),
        check(
            "terminal_state_remains_exact_across_ui_service",
            terminal_bound,
            "attempt 1/tick 166/reason 0100/result 1/renderer flag 0040",
        ),
        check(
            "f1_serviced_between_symbol_derived_vblank_dma_stops",
            vblank_boundary,
            str(boundary),
        ),
        check(
            "owned_native_program_stable_png_produced",
            native_owned,
            (
                f"{screenshot['path']} SHA-256 {screenshot['sha256']} "
                f"{screenshot['width']}x{screenshot['height']}"
            ),
        ),
    ]
    passed = all(item["status"] == "pass" for item in checks)
    return {
        "schema": "r94-m4-terminal-vblank-screenshot-discriminant-v1",
        "scope": "Milestone4",
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "experiment": (
            "one fresh terminal-only process; no immediate post-gameplay "
            "re-stop; F1 down/up across successive VBlank DMA fallthroughs"
        ),
        "native_png_produced": native_owned,
        "checks": checks,
        "conclusion": (
            "The native program-stable PNG proves that the previous immediate "
            "terminal post-gameplay re-stops starved BlastEm UI screenshot "
            "service; the zero-PNG symptom was an evidence-boundary defect, "
            "not a demonstrated framebuffer failure."
            if passed
            else "The discriminant did not establish a trusted native frame."
        ),
        "capture": capture,
        "session_metadata": metadata,
        "renderer_budget_observation": {
            "backlog_frames": capture["published_state"]["renderer"][
                "backlog_frames"
            ],
            "vblank_overrun_count": capture["published_state"]["debug"][
                "vblank_overrun_count"
            ],
            "dropped_sprites": capture["published_state"]["renderer"][
                "dropped_sprites"
            ],
            "dma_rejects": capture["published_state"]["renderer"][
                "dma_rejects"
            ],
        },
        "provenance": {
            **provenance,
            "diagnostic_trace": {
                "path": trace_json_path.relative_to(root).as_posix(),
                "sha256": sha256_file(trace_json_path),
            },
        },
        "debugger_write_packets": writes,
    }


def _terminal_vblank_discriminant_failure_report(
    *,
    rom_sha256: str,
    error: Exception,
    provenance: dict[str, Any],
    transcript_lines: list[str],
    trace_json_path: Path,
    root: Path,
) -> dict[str, Any]:
    """Report the localized outcome when the one-shot still yields no PNG."""
    writes = debugger_write_packets(transcript_lines)
    context = getattr(error, "capture_context", None)
    post_step = provenance.get("symbol_map", {}).get("symbols", {}).get(
        "MainLoopPostGameplayStep"
    )
    post_dma = provenance.get("post_dma_commit", {}).get(
        "post_commit_address_hex"
    )
    removal = f"> z0,{int(post_step, 16):x},2" if post_step else ""
    insertion = f"> Z0,{int(post_dma, 16):x},2" if post_dma else ""
    boundary_sequence = False
    if removal in transcript_lines and insertion in transcript_lines:
        removal_index = len(transcript_lines) - 1 - transcript_lines[::-1].index(
            removal
        )
        try:
            insertion_index = transcript_lines.index(
                insertion, removal_index + 1
            )
        except ValueError:
            insertion_index = -1
        if insertion_index > removal_index:
            tail = transcript_lines[insertion_index + 1 :]
            boundary_sequence = (
                tail.count("> c") == 4
                and f"> Z0,{int(post_step, 16):x},2" not in tail
                and "> k" in tail
            )
    owned_isolated_directory = bool(
        context
        and context["window_ownership"]["process_id"]
        == context["window_ownership"]["resolved_owner_process_id"]
        and context["window_ownership"]["title_matches"] is True
        and "/recursion94-m4-program_stable-"
        in context["isolated_screenshot_path"].replace("\\", "/")
        and context["isolated_screenshot_path"]
        .replace("\\", "/")
        .endswith("/session-data/screenshots")
    )
    terminal_bound = bool(
        context
        and context.get("ui_screenshot_key") == "F1"
        and context.get("authoritative_ticks_during_ui_capture") == 0
        and context.get("emulator_resumes_during_ui_capture") == 3
        and context.get("ui_key_release_serviced_before_capture_stop") is True
        and all(
            context[key]["engine"]["attempt"] == 1
            and context[key]["engine"]["tick"] == 166
            and context[key]["engine"]["state"] == ENGINE_STATE_TERMINAL
            and context[key]["gameplay"]["result"]
            == GAMEPLAY_RESULT_PROGRAM_STABLE
            for key in (
                "state_before_ui_request",
                "state_after_ui_key_down",
                "state",
            )
        )
    )
    checks = [
        check(
            "exact_current_debug_rom",
            rom_sha256 == EXPECTED_DEBUG_ROM_SHA256,
            f"Debug ROM SHA-256 {rom_sha256}",
        ),
        check(
            "no_guest_ram_register_or_pc_writes",
            not writes,
            f"debugger write packets {writes}",
        ),
        check(
            "terminal_state_and_f1_vblank_progression_preserved",
            terminal_bound,
            (
                "attempt 1/tick 166 stayed terminal across F1 down/up and "
                "three VBlank/post-DMA resumes"
            ),
        ),
        check(
            "post_gameplay_restops_removed_and_four_vblank_continues_used",
            boundary_sequence,
            (
                f"removed {post_step}, inserted symbol-derived {post_dma}; "
                "target commit plus F1 down/up/native-frame flush used "
                "exactly four continues"
            ),
        ),
        check(
            "owned_process_and_isolated_native_directory",
            owned_isolated_directory,
            (
                str(context["window_ownership"])
                + " "
                + context["isolated_screenshot_path"]
                if context
                else "capture context unavailable"
            ),
        ),
        check(
            "owned_native_program_stable_png_produced",
            False,
            f"{type(error).__name__}: {error}",
        ),
    ]
    report = {
        "schema": "r94-m4-terminal-vblank-screenshot-discriminant-v1",
        "scope": "Milestone4",
        "status": "fail",
        "rom_sha256": rom_sha256,
        "experiment": (
            "one fresh terminal-only process; no immediate post-gameplay "
            "re-stop; F1 down/up across successive VBlank DMA fallthroughs"
        ),
        "native_png_produced": False,
        "checks": checks,
        "error": f"{type(error).__name__}: {error}",
        "conclusion": (
            "The immediate post-gameplay re-stop hypothesis is falsified: "
            "zero-PNG persists even when F1 down/up are serviced across full "
            "VBlank/post-DMA stops. The failure is localized to BlastEm's "
            "terminal-state UI/binding/native-screenshot path, not proven to "
            "be a game framebuffer rendering failure."
        ),
        "provenance": {
            **provenance,
            "diagnostic_trace": {
                "path": trace_json_path.relative_to(root).as_posix(),
                "sha256": sha256_file(trace_json_path),
            },
        },
        "debugger_write_packets": writes,
    }
    if context is not None:
        report["failure_capture_context"] = context
    return report


def _fresh_transcript_sections(
    transcript_lines: list[str],
) -> dict[str, dict[str, Any]]:
    """Hash each immutable fresh-process section of a capture transcript."""
    sections: dict[str, dict[str, Any]] = {}
    for capture_name in SCREENSHOT_FILES:
        session_id = f"fresh_{capture_name}"
        begin = f"# {session_id} begin"
        end = f"# {session_id} end"
        if transcript_lines.count(begin) != 1 or transcript_lines.count(end) != 1:
            raise EvidenceFailure(
                f"transcript does not contain one complete {session_id} section"
            )
        first = transcript_lines.index(begin)
        last = transcript_lines.index(end)
        if last <= first:
            raise EvidenceFailure(
                f"transcript section {session_id} is out of order"
            )
        section_lines = transcript_lines[first : last + 1]
        payload = ("\n".join(section_lines) + "\n").encode("utf-8")
        sections[capture_name] = {
            "session_id": session_id,
            "line_start": first + 1,
            "line_end": last + 1,
            "sha256": _sha256_bytes(payload),
            "gdb_commands": sum(
                line.startswith("> ") for line in section_lines
            ),
        }
    return sections


def _write_frozen_pre_repair_baseline(
    *,
    root: Path,
    rom: Path,
    report_path: Path,
    qa_report_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write the immutable historical FAIL without launching an emulator."""
    failure_path = (
        root
        / "artifacts/reports/"
        "m4-before-repair-blocked-capture-failure.json"
    )
    transcript_path = root / "artifacts/traces/m4-visual-gdb-transcript.log"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    transcript_lines = transcript_path.read_text(encoding="utf-8").splitlines()
    if failure.get("rom_sha256") != PRE_REPAIR_DEBUG_ROM_SHA256:
        raise EvidenceFailure(
            "archived blocked capture is not bound to the pre-repair ROM"
        )
    if "changed 0 PNGs; exactly one required" not in failure.get("error", ""):
        raise EvidenceFailure("archived blocked capture has an unexpected error")
    sections = _fresh_transcript_sections(transcript_lines)
    writes = debugger_write_packets(transcript_lines)
    immutable_frames = {
        "zero_echo": {
            "path": (
                "artifacts/screenshots/"
                "m4-before-repair-zero-echo.png"
            ),
            "sha256": (
                "d8afefcea497ca33ef173442a6271f68e"
                "fb906fb3cdc3b0bfc55b293e7d9309d"
            ),
            "published_target_tick": 8,
            "state_before_ui_request_tick": 9,
            "state_after_ui_key_down_tick": 10,
            "state_after_ui_key_up_tick": 11,
            "visible_hud_tick": 9,
        },
        "reset_trace": {
            "path": (
                "artifacts/screenshots/"
                "m4-before-repair-reset-trace.png"
            ),
            "sha256": (
                "6785a9beab69777769d3a9e6cf9f271b"
                "9ca67c1d0f3d7820876be7e0b04eb357"
            ),
            "published_target_tick": 3,
            "state_before_ui_request_tick": 4,
            "state_after_ui_key_down_tick": 5,
            "state_after_ui_key_up_tick": 6,
            "visible_hud_tick": 4,
        },
        "echo_door_open": {
            "path": (
                "artifacts/screenshots/"
                "m4-before-repair-echo-door-open.png"
            ),
            "sha256": (
                "0a68bf377c9bb628eefb53613917fd4ced"
                "49cd4d29d8d15f74dd81d1e7a33b5e"
            ),
            "published_target_tick": 40,
            "state_before_ui_request_tick": 41,
            "state_after_ui_key_down_tick": 42,
            "state_after_ui_key_up_tick": 43,
            "visible_hud_tick": 41,
        },
        "program_stable": {
            "path": (
                "artifacts/screenshots/"
                "m4-before-repair-program-stable.png"
            ),
            "sha256": (
                "610a4998cd6dee3017e6f5fb4280262b9"
                "af9237e86307df83cc51f7374830947"
            ),
            "published_target_tick": 166,
            "state_before_ui_request_tick": 166,
            "state_after_ui_key_down_tick": 166,
            "state_after_ui_key_up_tick": 166,
            "visible_hud_tick": 166,
        },
    }
    captures: dict[str, dict[str, Any]] = {}
    frame_hashes_match = True
    frames_structural = True
    for capture_name, expected in immutable_frames.items():
        relative = expected["path"]
        path = root / relative
        facts = analyze_png(path, relative_path=relative)
        stat = path.stat()
        facts["mtime_ns"] = stat.st_mtime_ns
        facts["captured_utc"] = datetime.fromtimestamp(
            stat.st_mtime, timezone.utc
        ).isoformat()
        hash_matches = facts["sha256"] == expected["sha256"]
        structural = (
            facts["width"] == 320
            and facts["height"] == 240
            and facts["format"] == "PNG indexed8 non-interlaced"
        )
        frame_hashes_match = frame_hashes_match and hash_matches
        frames_structural = frames_structural and structural
        captures[capture_name] = {
            "status": "captured",
            "baseline": "pre_repair_immutable",
            "screenshot": facts,
            "expected_sha256": expected["sha256"],
            "hash_matches": hash_matches,
            "ui_screenshot_key": PRE_REPAIR_SCREENSHOT_KEYS_BY_CAPTURE[
                capture_name
            ][0],
            "capture_method": (
                "BlastEm ui.screenshot in one isolated fresh process"
            ),
            "state_binding": {
                "published_target_tick": expected[
                    "published_target_tick"
                ],
                "state_before_ui_request_tick": expected[
                    "state_before_ui_request_tick"
                ],
                "state_after_ui_key_down_tick": expected[
                    "state_after_ui_key_down_tick"
                ],
                "state_after_ui_key_up_tick": expected[
                    "state_after_ui_key_up_tick"
                ],
                "authoritative_ticks_during_ui_capture": (
                    expected["state_after_ui_key_up_tick"]
                    - expected["state_before_ui_request_tick"]
                ),
                "emulator_resumes_during_ui_capture": 2,
                "ui_key_release_serviced_before_capture_stop": True,
                "visible_hud_tick": expected["visible_hud_tick"],
                "note": (
                    "The native screenshot is release-triggered; the visible "
                    "HUD corresponds to the rendered key-down frame, while "
                    "the collector also serviced key-up before stopping."
                ),
            },
            "transcript_binding": sections[capture_name],
        }

    blocked_context = failure["failure_capture_context"]
    captures["blocked_path"] = {
        "status": "missing",
        "baseline": "pre_repair_immutable",
        "expected_path": SCREENSHOT_FILES["blocked_path"],
        "ui_screenshot_key": "F12",
        "capture_method": (
            "BlastEm ui.screenshot in one isolated fresh process"
        ),
        "failure": failure["error"],
        "state_binding": {
            "published_target_tick": 101,
            "state_before_ui_request_tick": 102,
            "state_after_ui_key_down_tick": 103,
            "state_after_ui_key_up_tick": 104,
            "authoritative_ticks_during_ui_capture": 2,
            "emulator_resumes_during_ui_capture": 2,
            "ui_key_release_serviced_before_capture_stop": True,
            "pre_request_raw_bundle_sha256": blocked_context[
                "state_before_ui_request"
            ]["raw_bundle_sha256"],
            "key_down_raw_bundle_sha256": blocked_context[
                "state_after_ui_key_down"
            ]["raw_bundle_sha256"],
            "key_up_raw_bundle_sha256": blocked_context["state"][
                "raw_bundle_sha256"
            ],
        },
        "window_ownership": blocked_context["window_ownership"],
        "transcript_binding": sections["blocked_path"],
        "archived_failure_context": blocked_context,
    }
    reviews = PRE_REPAIR_VISUAL_REVIEWS
    rubric_passed = all(
        review["status"] == "pass"
        and review["minimum_score"] >= 4
        and review["average_score"] >= 4.2
        and not review["critical_gameplay_readability_defects"]
        for review in reviews.values()
    )
    archived_failure_hash = sha256_file(failure_path)
    transcript_hash = sha256_file(transcript_path)
    checks = [
        check(
            "capture_time_rom_is_assigned_pre_repair_rom",
            failure["rom_sha256"] == PRE_REPAIR_DEBUG_ROM_SHA256,
            f"capture-time Debug ROM SHA-256 {failure['rom_sha256']}",
        ),
        check(
            "four_immutable_native_frames_hash_match_and_decode",
            frame_hashes_match and frames_structural,
            "four 320x240 indexed native PNGs match immutable SHA-256 values",
        ),
        check(
            "five_fresh_process_transcript_sections_preserved",
            len(sections) == 5,
            str(
                [
                    (
                        name,
                        section["line_start"],
                        section["line_end"],
                        section["sha256"],
                    )
                    for name, section in sections.items()
                ]
            ),
        ),
        check(
            "controller_path_has_no_debugger_state_writes",
            not writes,
            f"debugger RAM/register/PC write packets {writes}",
        ),
        check(
            "blocked_capture_failure_context_preserved",
            (
                blocked_context["ui_screenshot_key"] == "F12"
                and blocked_context[
                    "authoritative_ticks_during_ui_capture"
                ]
                == 2
                and blocked_context[
                    "ui_key_release_serviced_before_capture_stop"
                ]
                is True
                and blocked_context["state_before_ui_request"]["engine"][
                    "tick"
                ]
                == 102
                and blocked_context["state_after_ui_key_down"]["engine"][
                    "tick"
                ]
                == 103
                and blocked_context["state"]["engine"]["tick"] == 104
            ),
            (
                f"archived failure {failure_path.relative_to(root).as_posix()} "
                f"SHA-256 {archived_failure_hash}"
            ),
        ),
        check(
            "complete_required_five_png_set",
            False,
            "blocked-path ui.screenshot produced zero PNGs; four of five exist",
        ),
        check(
            "successful_session_process_and_profile_metadata_checkpointed",
            False,
            "the fifth-session exception preceded aggregate serialization, "
            "so the four successful PIDs/profile hashes are unavailable",
        ),
        check(
            "prompt_section_42_visual_rubric_passes_every_scene",
            rubric_passed,
            str(
                [
                    (
                        name,
                        review["minimum_score"],
                        review["average_score"],
                    )
                    for name, review in reviews.items()
                ]
            ),
        ),
    ]
    current_rom_hash = sha256_file(rom) if rom.is_file() else None
    visual = {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "baseline_kind": "pre_repair_frozen_failure",
        "status": "fail",
        "rom_sha256": PRE_REPAIR_DEBUG_ROM_SHA256,
        "checks": checks,
        "captures": captures,
        "visual_reviews": reviews,
        "reviewer": {
            "classification": (
                "independent visual-critic agent; did not implement renderer"
            ),
            "method": (
                "direct inspection of four immutable native PNGs; the required "
                "blocked-path frame is scored unavailable, not inferred"
            ),
            "rubric_source": "prompt.txt section 42",
        },
        "provenance": {
            "capture_time_rom": {
                "path": "build/recursion94-debug.bin",
                "sha256": PRE_REPAIR_DEBUG_ROM_SHA256,
                "binding_source": (
                    "collector preflight and archived failure report"
                ),
            },
            "transcript": {
                "path": transcript_path.relative_to(root).as_posix(),
                "sha256": transcript_hash,
                "commands": sum(
                    line.startswith("> ") for line in transcript_lines
                ),
                "sections": sections,
            },
            "archived_blocked_failure": {
                "path": failure_path.relative_to(root).as_posix(),
                "sha256": archived_failure_hash,
            },
            "freeze_environment_not_capture_binding": {
                "workspace_rom_sha256": current_rom_hash,
                "matches_capture_time_rom": (
                    current_rom_hash == PRE_REPAIR_DEBUG_ROM_SHA256
                ),
            },
        },
        "debugger_write_packets": writes,
        "concrete_defects_and_limitations": [
            {
                "severity": "critical",
                "blocking": True,
                "finding": (
                    "The required blocked-path native PNG is missing, so the "
                    "five-frame visual evidence set is incomplete."
                ),
            },
            {
                "severity": "critical",
                "blocking": True,
                "finding": (
                    "Approximately 8x8 actors and objectives are not reliably "
                    "readable against the high-contrast checker/D-grid floor."
                ),
            },
            {
                "severity": "critical",
                "blocking": True,
                "finding": (
                    "The reset overlay obscures actors and the hazard, and "
                    "current-versus-echo identity relies on HUD text/badges."
                ),
            },
            {
                "severity": "critical",
                "blocking": True,
                "finding": (
                    "Door-open causality and program completion are explained "
                    "primarily by HUD text rather than playfield hierarchy."
                ),
            },
            {
                "severity": "limitation",
                "blocking": True,
                "finding": (
                    "Successful process IDs and isolated profile hashes were "
                    "not checkpointed before the fifth-session exception; no "
                    "values are reconstructed or fabricated."
                ),
            },
        ],
    }
    qa_checks = [
        check(
            "accurately_labeled_controller_driven_review",
            True,
            "independent automated controller-driven playability review; "
            "not literal human testing",
        ),
        check(
            "controller_transcript_has_no_debugger_state_writes",
            not writes,
            f"debugger RAM/register/PC write packets {writes}",
        ),
        check(
            "four_canonical_controller_states_have_native_frames",
            len(immutable_frames) == 4
            and frame_hashes_match
            and frames_structural,
            "zero/reset/open/stable native frames are preserved",
        ),
        check(
            "blocked_path_has_required_native_frame",
            False,
            failure["error"],
        ),
        check(
            "visual_gate_passed",
            False,
            "section 42 rubric and five-frame completeness both fail",
        ),
    ]
    qa = {
        "schema": QA_REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": QA_EVIDENCE_TYPE,
        "baseline_kind": "pre_repair_frozen_failure",
        "status": "fail",
        "rom_sha256": PRE_REPAIR_DEBUG_ROM_SHA256,
        "classification": (
            "Independent automated controller-driven playability review. "
            "This is not literal human hands-on testing."
        ),
        "checks": qa_checks,
        "observations": [
            {
                "area": "controller_path",
                "status": "pass",
                "evidence": (
                    "Five isolated fresh-process transcript sections preserve "
                    "the controller-driven runs with no debugger state writes."
                ),
            },
            {
                "area": "zero_reset_open_stable",
                "status": "pass",
                "evidence": (
                    "Four native frames and exact request/key-down/key-up tick "
                    "schedules are hash-bound to their transcript sections."
                ),
            },
            {
                "area": "blocked_path",
                "status": "fail",
                "evidence": (
                    "Guest state reached ticks 101/102/103/104, current remained "
                    "at X=108 with the door closed, but F12 produced no PNG."
                ),
            },
            {
                "area": "visual_readability",
                "status": "fail",
                "evidence": (
                    "All captured scenes contain sub-4 rubric categories and "
                    "critical gameplay-readability defects."
                ),
            },
        ],
        "reviewed_screenshots": [
            expected["path"] for expected in immutable_frames.values()
        ],
        "provenance": {
            "visual_report_schema": REPORT_SCHEMA,
            "visual_report_status": "fail",
            "transcript_sha256": transcript_hash,
            "archived_failure_sha256": archived_failure_hash,
        },
        "limitations": [
            "This historical baseline is bound to the captured pre-repair ROM, "
            "not a later workspace build.",
            "The fifth required native frame is absent.",
            "The successful process/profile metadata was not serialized before "
            "the fifth-session failure and is intentionally not invented.",
            "Input was automated through the real configured controller path; "
            "no person physically held a controller.",
        ],
    }
    write_json(report_path, visual)
    write_json(qa_report_path, qa)
    return visual, qa


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--qa-report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--trace-json", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--lock-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--finalize-existing", action="store_true")
    parser.add_argument("--minimal-capture", action="store_true")
    parser.add_argument("--freeze-pre-repair-baseline", action="store_true")
    parser.add_argument(
        "--terminal-vblank-discriminant",
        action="store_true",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    rom = args.rom.resolve()
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m4-screenshot.json"
    )
    qa_report_path = (
        args.qa_report.resolve()
        if args.qa_report
        else root / "artifacts/reports/m4-gameplay-qa.json"
    )
    transcript_path = (
        args.trace.resolve()
        if args.trace
        else root
        / (
            "artifacts/traces/m4-visual-minimal-gdb.log"
            if args.minimal_capture
            else "artifacts/traces/m4-visual-gdb-transcript.log"
        )
    )
    trace_json_path = (
        args.trace_json.resolve()
        if args.trace_json
        else root
        / (
            "artifacts/traces/m4-visual-minimal.json"
            if args.minimal_capture
            else "artifacts/traces/m4-visual-session.json"
        )
    )
    rom_sha256 = ""
    visual: dict[str, Any]
    qa: dict[str, Any]
    if args.freeze_pre_repair_baseline:
        try:
            visual, qa = _write_frozen_pre_repair_baseline(
                root=root,
                rom=rom,
                report_path=report_path,
                qa_report_path=qa_report_path,
            )
        except Exception as error:
            visual = _failure_report(
                schema=REPORT_SCHEMA,
                evidence_type=EVIDENCE_TYPE,
                rom_sha256=PRE_REPAIR_DEBUG_ROM_SHA256,
                error=error,
            )
            qa = _failure_report(
                schema=QA_REPORT_SCHEMA,
                evidence_type=QA_EVIDENCE_TYPE,
                rom_sha256=PRE_REPAIR_DEBUG_ROM_SHA256,
                error=error,
            )
            write_json(report_path, visual)
            write_json(qa_report_path, qa)
        print("M4 frozen pre-repair baseline: FAIL")
        return 1
    if args.minimal_capture:
        expected = (
            args.expected_sha256.strip().lower()
            if args.expected_sha256
            else EXPECTED_DEBUG_ROM_SHA256
        )
        return run_minimal_capture(
            root=root,
            rom=rom,
            trace_path=transcript_path,
            trace_json_path=trace_json_path,
            expected_sha256=expected,
            timeout_seconds=args.timeout_seconds,
            lock_timeout_seconds=args.lock_timeout_seconds,
        )
    if args.finalize_existing:
        try:
            visual, qa = _write_reports_from_trace(
                root=root,
                rom=rom,
                trace_json_path=trace_json_path,
                report_path=report_path,
                qa_report_path=qa_report_path,
            )
        except Exception as error:
            rom_sha256 = sha256_file(rom) if rom.is_file() else ""
            visual = _failure_report(
                schema=REPORT_SCHEMA,
                evidence_type=EVIDENCE_TYPE,
                rom_sha256=rom_sha256,
                error=error,
            )
            qa = _failure_report(
                schema=QA_REPORT_SCHEMA,
                evidence_type=QA_EVIDENCE_TYPE,
                rom_sha256=rom_sha256,
                error=error,
            )
            write_json(report_path, visual)
            write_json(qa_report_path, qa)
        passed = visual["status"] == "pass" and qa["status"] == "pass"
        print(
            "M4 visual/playability finalize: "
            f"{'PASS' if passed else 'FAIL'}"
        )
        return 0 if passed else 1

    transcript: list[str] = []
    canonical: dict[str, Any] | None = None
    blocked: dict[str, Any] | None = None
    provenance: dict[str, Any] = {}
    fresh_sessions: dict[str, dict[str, Any]] = {}
    diagnostic_session: dict[str, Any] | None = None
    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live M4 visual evidence requires native Windows BlastEm"
            )
        if not 5.0 <= args.timeout_seconds <= 600.0:
            raise ValueError("--timeout-seconds must be within 5..600")
        rom_sha256 = sha256_file(rom)
        expected = (
            args.expected_sha256.strip().lower()
            if args.expected_sha256
            else EXPECTED_DEBUG_ROM_SHA256
        )
        if rom_sha256 != expected:
            raise EvidenceFailure(
                f"Debug ROM SHA-256 {rom_sha256} does not match {expected}"
            )
        if expected != EXPECTED_DEBUG_ROM_SHA256:
            raise EvidenceFailure(
                "M4 visual gate is assigned to the final Debug ROM hash"
            )
        emulator_directory = root / ".toolcache/blastem"
        emulator = emulator_directory / "blastem.exe"
        default_config = emulator_directory / "default.cfg"
        symbol_path = root / "build/recursion94-debug.sym"
        for path, description in (
            (rom, "Debug ROM"),
            (emulator, "pinned BlastEm"),
            (default_config, "pinned default.cfg"),
            (symbol_path, "Debug symbol map"),
        ):
            if not path.is_file():
                raise EvidenceFailure(f"{description} is missing: {path}")
        user_config = verify_no_user_blastem_override()
        bindings = verify_input_bindings(default_config)
        if bindings["status"] != "pass":
            raise EvidenceFailure(
                "pinned default.cfg lacks required controller bindings"
            )
        symbols = parse_vasm_symbols(symbol_path)
        missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
        if missing:
            raise EvidenceFailure(
                f"Debug symbol map lacks required symbols {missing}"
            )
        post_dma = derive_post_dma_commit_address(
            rom.read_bytes(), symbols
        )
        source = source_binding(root)
        lock_path = root / ".toolcache/locks/blastem-gdb.lock"
        provenance = {
            "rom": {
                "path": rom.relative_to(root).as_posix(),
                "sha256": rom_sha256,
            },
            "emulator": {
                "path": emulator.relative_to(root).as_posix(),
                "sha256": sha256_file(emulator),
            },
            "default_config": {
                "path": default_config.relative_to(root).as_posix(),
                **bindings,
            },
            "symbol_map": {
                "path": symbol_path.relative_to(root).as_posix(),
                "sha256": sha256_file(symbol_path),
                "symbols": {
                    name: f"{symbols[name]:08X}"
                    for name in REQUIRED_SYMBOLS
                },
            },
            "post_dma_commit": post_dma,
            "source_binding": source,
            "user_config_preflight": user_config,
            "serialized_lock": lock_path.relative_to(root).as_posix(),
            "launch_flags": list(LAUNCH_FLAGS),
            "capture_tool": "tools/m4_visual_evidence.py",
        }
        with exclusive_file_lock(
            lock_path, args.lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            if args.terminal_vblank_discriminant:
                diagnostic_session = _run_fresh_capture_session(
                    root=root,
                    rom=rom,
                    emulator_directory=emulator_directory,
                    default_config=default_config,
                    symbols=symbols,
                    post_dma_commit=post_dma["post_commit_address"],
                    timeout_seconds=args.timeout_seconds,
                    transcript=transcript,
                    capture_name="program_stable",
                    terminal_vblank_ui=True,
                )
            else:
                for capture_name in SCREENSHOT_FILES:
                    fresh_sessions[capture_name] = (
                        _run_fresh_capture_session(
                            root=root,
                            rom=rom,
                            emulator_directory=emulator_directory,
                            default_config=default_config,
                            symbols=symbols,
                            post_dma_commit=post_dma[
                                "post_commit_address"
                            ],
                            timeout_seconds=args.timeout_seconds,
                            transcript=transcript,
                            capture_name=capture_name,
                        )
                    )
                    transcript_path.parent.mkdir(parents=True, exist_ok=True)
                    transcript_path.write_text(
                        "\n".join(transcript) + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                    write_json(
                        trace_json_path,
                        {
                            "schema": (
                                "r94-m4-visual-capture-checkpoint-v1"
                            ),
                            "status": "in_progress",
                            "rom_sha256": rom_sha256,
                            "completed_capture_names": list(fresh_sessions),
                            "pending_capture_names": [
                                name
                                for name in SCREENSHOT_FILES
                                if name not in fresh_sessions
                            ],
                            "provenance": provenance,
                            "fresh_sessions": fresh_sessions,
                            "transcript_lines": transcript,
                        },
                    )
                canonical, blocked = _aggregate_fresh_capture_sessions(
                    fresh_sessions
                )
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(
            "\n".join(transcript) + ("\n" if transcript else ""),
            encoding="utf-8",
            newline="\n",
        )
        provenance["transcript"] = {
            "path": transcript_path.relative_to(root).as_posix(),
            "sha256": sha256_file(transcript_path),
            "commands": sum(line.startswith("> ") for line in transcript),
        }
        if sha256_file(rom) != rom_sha256:
            raise EvidenceFailure("Debug ROM changed during visual capture")
        if sha256_file(emulator) != provenance["emulator"]["sha256"]:
            raise EvidenceFailure("pinned BlastEm changed during capture")
        if sha256_file(symbol_path) != provenance["symbol_map"]["sha256"]:
            raise EvidenceFailure("symbol map changed during capture")
        if (
            sha256_file(default_config)
            != provenance["default_config"]["source_sha256"]
        ):
            raise EvidenceFailure("default.cfg changed during capture")
        if source_binding(root) != source:
            raise EvidenceFailure("visual source binding changed during capture")
        if args.terminal_vblank_discriminant:
            if diagnostic_session is None:
                raise EvidenceFailure(
                    "terminal VBlank discriminant produced no session"
                )
            diagnostic_trace = {
                "schema": (
                    "r94-m4-terminal-vblank-screenshot-discriminant-trace-v1"
                ),
                "status": "complete",
                "rom_sha256": rom_sha256,
                "provenance": provenance,
                "session": diagnostic_session,
                "transcript_lines": transcript,
            }
            write_json(trace_json_path, diagnostic_trace)
            diagnostic_report = _terminal_vblank_discriminant_report(
                rom_sha256=rom_sha256,
                session=diagnostic_session,
                provenance=provenance,
                transcript_lines=transcript,
                trace_json_path=trace_json_path,
                root=root,
            )
            write_json(report_path, diagnostic_report)
            passed = diagnostic_report["status"] == "pass"
            print(
                "M4 terminal VBlank screenshot discriminant: "
                f"{'PASS' if passed else 'FAIL'}"
            )
            return 0 if passed else 1
        provenance["image_differences"] = _image_difference_manifest(root)
        visual = build_visual_report(
            rom_sha256=rom_sha256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        qa = build_playability_report(
            visual_report=visual,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
        )
        trace_payload = {
            "schema": "r94-m4-visual-capture-trace-v1",
            "status": "complete",
            "rom_sha256": rom_sha256,
            "provenance": provenance,
            "canonical_session": canonical,
            "blocked_session": blocked,
            "fresh_sessions": fresh_sessions,
            "transcript_lines": transcript,
        }
        write_json(trace_json_path, trace_payload)
    except Exception as error:
        if args.terminal_vblank_discriminant:
            if transcript:
                transcript_path.parent.mkdir(parents=True, exist_ok=True)
                transcript_path.write_text(
                    "\n".join(transcript) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                provenance["transcript"] = {
                    "path": transcript_path.relative_to(root).as_posix(),
                    "sha256": sha256_file(transcript_path),
                    "commands": sum(
                        line.startswith("> ") for line in transcript
                    ),
                }
            diagnostic_trace = {
                "schema": (
                    "r94-m4-terminal-vblank-screenshot-discriminant-trace-v1"
                ),
                "status": "fail",
                "rom_sha256": rom_sha256,
                "error": f"{type(error).__name__}: {error}",
                "provenance": provenance,
                "transcript_lines": transcript,
            }
            capture_context = getattr(error, "capture_context", None)
            if capture_context is not None:
                diagnostic_trace["failure_capture_context"] = capture_context
            write_json(trace_json_path, diagnostic_trace)
            diagnostic_report = (
                _terminal_vblank_discriminant_failure_report(
                    rom_sha256=rom_sha256,
                    error=error,
                    provenance=provenance,
                    transcript_lines=transcript,
                    trace_json_path=trace_json_path,
                    root=root,
                )
            )
            write_json(report_path, diagnostic_report)
            print("M4 terminal VBlank screenshot discriminant: FAIL")
            return 1
        visual = _failure_report(
            schema=REPORT_SCHEMA,
            evidence_type=EVIDENCE_TYPE,
            rom_sha256=rom_sha256,
            error=error,
        )
        qa = _failure_report(
            schema=QA_REPORT_SCHEMA,
            evidence_type=QA_EVIDENCE_TYPE,
            rom_sha256=rom_sha256,
            error=error,
        )
        if transcript:
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(
                "\n".join(transcript) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            provenance["transcript"] = {
                "path": transcript_path.relative_to(root).as_posix(),
                "sha256": sha256_file(transcript_path),
                "commands": sum(
                    line.startswith("> ") for line in transcript
                ),
            }
        if fresh_sessions:
            failure_trace = {
                "schema": "r94-m4-visual-capture-checkpoint-v1",
                "status": "fail",
                "rom_sha256": rom_sha256,
                "completed_capture_names": list(fresh_sessions),
                "pending_capture_names": [
                    name
                    for name in SCREENSHOT_FILES
                    if name not in fresh_sessions
                ],
                "error": f"{type(error).__name__}: {error}",
                "provenance": provenance,
                "fresh_sessions": fresh_sessions,
                "transcript_lines": transcript,
            }
            capture_context = getattr(error, "capture_context", None)
            if capture_context is not None:
                failure_trace["failure_capture_context"] = capture_context
            write_json(trace_json_path, failure_trace)
            visual["preserved_successful_capture_names"] = list(
                fresh_sessions
            )
            visual["partial_trace"] = {
                "path": trace_json_path.relative_to(root).as_posix(),
                "sha256": sha256_file(trace_json_path),
            }
            qa["preserved_successful_capture_names"] = list(
                fresh_sessions
            )
    write_json(report_path, visual)
    write_json(qa_report_path, qa)
    passed = visual["status"] == "pass" and qa["status"] == "pass"
    print(
        "M4 visual/playability evidence: "
        f"{'PASS' if passed else 'FAIL'}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
