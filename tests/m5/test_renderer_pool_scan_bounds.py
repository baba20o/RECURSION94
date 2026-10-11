"""Source and state-model contracts for campaign renderer pool bounds.

The allocator proof lives in ``test_pool_scan_bounds``: lowest-free allocation
makes the historical maximum live count an exclusive physical-slot bound, and
freeing a slot never invalidates it.  These tests reuse that proof and bind the
seven read-only renderer scans that consume the ledger.  Legacy/B01 renderer
paths remain capacity-bound because their authored fixed slots are outside this
campaign-only optimization.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unittest

from tests.m5.test_pool_scan_bounds import (
    PoolHistory,
    _all_routines,
    _reachable_routines,
    bounded_active_slots,
    clamped_scan_bound,
    full_active_slots,
)
from tools.m3_reference.contracts import parse_layout_constants


ROOT = Path(__file__).resolve().parents[2]
C = parse_layout_constants(ROOT / "src/game/engine_layout.asm")


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _lines(source: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line.split(";", 1)[0].strip())
        for line in source.splitlines()
        if line.split(";", 1)[0].strip()
    ]


def _routine(relative: str, label: str) -> str:
    source = _source(relative)
    start = source.index(f"{label}:")
    following = re.search(
        r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$",
        source[start + len(label) + 1 :],
    )
    end = (
        start + len(label) + 1 + following.start()
        if following is not None
        else len(source)
    )
    return source[start:end]


@dataclass(frozen=True)
class RendererScan:
    routine: str
    loop: str
    pool: str
    pool_type: str
    capacity: str
    cursor: str
    counter: str
    bound_ready: str
    zero_exit: str
    clears_admitted: bool = False


SAT_SCANS = (
    RendererScan(
        "RendererBuildSat", ".campaign_device", "DEVICE_POOL",
        "POOL_TYPE_DEVICE", "DEVICE_CAPACITY", "a2", "d7",
        ".campaign_device_bound_ready", ".campaign_devices_done", True,
    ),
    RendererScan(
        "RendererBuildSat", ".campaign_enemy", "ENEMY_POOL",
        "POOL_TYPE_ENEMY", "ENEMY_CAPACITY", "a2", "d7",
        ".campaign_enemy_bound_ready", ".campaign_enemies_done", True,
    ),
    RendererScan(
        "RendererBuildSat", ".campaign_carryable", "CARRYABLE_POOL",
        "POOL_TYPE_CARRYABLE", "CARRYABLE_CAPACITY", "a2", "d7",
        ".campaign_carryable_bound_ready", ".campaign_carryables_done", True,
    ),
    RendererScan(
        "RendererBuildSat", ".campaign_hazard", "HAZARD_POOL",
        "POOL_TYPE_HAZARD", "HAZARD_CAPACITY", "a2", "d7",
        ".campaign_hazard_bound_ready", ".campaign_hazards_done", True,
    ),
    RendererScan(
        "RendererBuildSat", ".campaign_projectile", "PROJECTILE_POOL",
        "POOL_TYPE_PROJECTILE", "PROJECTILE_CAPACITY", "a2", "d7",
        ".campaign_projectile_bound_ready", ".campaign_projectiles_done", True,
    ),
)

DOOR_SCAN = RendererScan(
    "RendererDoorRejectionActive", ".campaign_door", "DEVICE_POOL",
    "POOL_TYPE_DEVICE", "DEVICE_CAPACITY", "a1", "d2",
    ".campaign_bound_ready", ".inactive",
)

HUD_SCAN = RendererScan(
    "RendererHudCampaignStatus", ".device", "DEVICE_POOL",
    "POOL_TYPE_DEVICE", "DEVICE_CAPACITY", "a2", "d5",
    ".device_bound_ready", ".status_ready",
)

SCANS = (*SAT_SCANS, DOOR_SCAN, HUD_SCAN)
PATH_BY_ROUTINE = {
    "RendererBuildSat": "src/render/gameplay_renderer_sprites.asm",
    "RendererDoorRejectionActive": "src/render/gameplay_renderer_sprites.asm",
    "RendererHudCampaignStatus": "src/render/gameplay_renderer_hud.asm",
}


def _expected_setup(scan: RendererScan) -> list[str]:
    result = [f"lea ({scan.pool}).l,{scan.cursor}"]
    if scan.clears_admitted:
        result.append("moveq #0,d6")
    result.extend(
        (
            f"moveq #0,{scan.counter}",
            f"move.b (ENGINE_POOL_HIGH_WATER+{scan.pool_type}-1).l,{scan.counter}",
            f"cmpi.w #{scan.capacity},{scan.counter}",
            f"bls.s {scan.bound_ready}",
            f"moveq #{scan.capacity},{scan.counter}",
            f"{scan.bound_ready}:",
            f"tst.w {scan.counter}",
            f"beq.w {scan.zero_exit}",
            f"subq.w #1,{scan.counter}",
            f"{scan.loop}:",
        )
    )
    return result


class RendererPoolScanSourceTests(unittest.TestCase):
    def test_exact_seven_scans_use_unsigned_typed_clamped_bounds(self) -> None:
        for scan in SCANS:
            with self.subTest(routine=scan.routine, loop=scan.loop):
                lines = _lines(_routine(PATH_BY_ROUTINE[scan.routine], scan.routine))
                loop_index = lines.index(f"{scan.loop}:")
                setup_start = max(
                    index
                    for index, line in enumerate(lines[:loop_index])
                    if line == f"lea ({scan.pool}).l,{scan.cursor}"
                )
                self.assertEqual(
                    lines[setup_start : loop_index + 1],
                    _expected_setup(scan),
                )

                exit_index = lines.index(f"{scan.zero_exit}:", loop_index)
                body = lines[loop_index:exit_index]
                self.assertEqual(body.count(f"dbf {scan.counter},{scan.loop}"), 1)
                self.assertEqual(
                    body.count(f"adda.w #OBJECT_BYTES,{scan.cursor}"), 1
                )

    def test_zero_exits_preserve_publication_and_required_outputs(self) -> None:
        sat = _lines(_routine(
            "src/render/gameplay_renderer_sprites.asm", "RendererBuildSat"
        ))
        ordered = (
            ".campaign_devices_done:",
            ".campaign_enemy_bound_ready:",
            ".campaign_enemies_done:",
            ".campaign_carryable_bound_ready:",
            ".campaign_carryables_done:",
            ".campaign_hazard_bound_ready:",
            ".campaign_hazards_done:",
            ".campaign_beams_done:",
            ".campaign_projectile_bound_ready:",
            ".campaign_projectiles_done:",
            ".publish:",
        )
        positions = [sat.index(label) for label in ordered]
        self.assertEqual(positions, sorted(positions))
        publish = sat.index(".publish:")
        self.assertEqual(
            sat[publish + 1 : publish + 5],
            [
                "move.w (RENDERER_SPRITE_COUNT).l,d0",
                "move.w d0,(DEBUG_SPRITE_COUNT).l",
                "move.w (RENDERER_SCANLINE_MAX).l,d0",
                "move.w d0,(DEBUG_SPRITE_SCANLINE_MAX).l",
            ],
        )

        door = _lines(_routine(
            "src/render/gameplay_renderer_sprites.asm",
            "RendererDoorRejectionActive",
        ))
        inactive = door.index(".inactive:")
        self.assertEqual(
            door[inactive + 1 : inactive + 4],
            ["moveq #0,d0", "moveq #0,d1", "rts"],
        )

        hud = _lines(_routine(
            "src/render/gameplay_renderer_hud.asm", "RendererHudCampaignStatus"
        ))
        ready = hud.index(".status_ready:")
        self.assertEqual(
            hud[ready + 1 : ready + 4],
            ["move.w d3,d0", "move.l d6,d1", "movem.l (a7)+,d3-d6/a2"],
        )

    def test_legacy_and_actor_renderer_paths_remain_out_of_scope(self) -> None:
        sprites = _source("src/render/gameplay_renderer_sprites.asm")
        legacy = sprites[
            sprites.index(".legacy_current:") : sprites.index(".campaign:")
        ]
        self.assertIn("move.w  #PROJECTILE_CAPACITY-1,d7", legacy)
        self.assertNotIn("ENGINE_POOL_HIGH_WATER", legacy)
        self.assertIn("moveq   #GAMEPLAY_ECHO_LIMIT-1,d7", sprites)
        self.assertEqual(sprites.count("ENGINE_POOL_HIGH_WATER"), 6)
        self.assertEqual(
            _source("src/render/gameplay_renderer_hud.asm").count(
                "ENGINE_POOL_HIGH_WATER"
            ),
            1,
        )

    def test_renderer_scan_call_closures_cannot_allocate(self) -> None:
        routines = _all_routines()
        for root in (
            "RendererBuildSat",
            "RendererDoorRejectionActive",
            "RendererHudCampaignStatus",
        ):
            with self.subTest(root=root):
                reachable, unresolved = _reachable_routines(root, routines)
                self.assertEqual(unresolved, ())
                self.assertNotIn("EnginePoolAllocateLowest", reachable)
                self.assertNotIn("EnginePoolNoteAllocation", reachable)


class RendererPoolScanModelTests(unittest.TestCase):
    def test_legal_hole_reuse_is_identical_to_full_capacity_scan(self) -> None:
        capacities = {
            C[scan.capacity] for scan in SCANS
        }
        for capacity in sorted(capacities):
            with self.subTest(capacity=capacity):
                history = PoolHistory.empty(capacity)
                self.assertEqual(bounded_active_slots(history), ())
                allocations = min(6, capacity)
                self.assertEqual(
                    [history.allocate_lowest() for _ in range(allocations)],
                    list(range(allocations)),
                )
                if allocations >= 4:
                    history.free(1)
                    history.free(3)
                    self.assertEqual(history.allocate_lowest(), 1)
                self.assertEqual(
                    bounded_active_slots(history), full_active_slots(history)
                )

    def test_zero_capacity_saturation_and_corrupt_bytes_are_bounded(self) -> None:
        for scan in SCANS:
            capacity = C[scan.capacity]
            with self.subTest(loop=scan.loop):
                self.assertEqual(clamped_scan_bound(0, capacity), 0)
                self.assertEqual(clamped_scan_bound(1, capacity), 1)
                self.assertEqual(
                    clamped_scan_bound(capacity, capacity), capacity
                )
                self.assertEqual(
                    clamped_scan_bound(capacity + 1, capacity), capacity
                )
                self.assertEqual(clamped_scan_bound(0xFF, capacity), capacity)


if __name__ == "__main__":
    unittest.main()
