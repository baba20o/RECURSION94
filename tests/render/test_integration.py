from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOOT_STAGE_VIDEO_READY = 3
BOOT_STAGE_RUNNING = 4
VDP_STATUS_VBLANK = 0x0008
WORD_MAX = 0xFFFF


def _vblank_overrun_count_after_interrupt(
    *,
    boot_stage: int,
    entry_status: int,
    exit_status: int,
    counter: int,
) -> int:
    """Model the RUNNING VBlank entry/exit detector contract."""

    if not 0 <= counter <= WORD_MAX:
        raise ValueError(counter)
    if boot_stage != BOOT_STAGE_RUNNING:
        return counter
    if (
        entry_status & VDP_STATUS_VBLANK
        and exit_status & VDP_STATUS_VBLANK
    ):
        return counter
    return min(counter + 1, WORD_MAX)


def symbols(path: Path) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for line in path.read_text("utf-8").splitlines():
        address, name = line.split(maxsplit=1)
        parsed[name] = int(address, 16)
    return parsed


class RendererIntegrationTests(unittest.TestCase):
    def test_running_vblank_detector_requires_entry_and_exit_bits(self) -> None:
        for entry_bit, exit_bit, increment in (
            (False, False, 1),
            (False, True, 1),
            (True, False, 1),
            (True, True, 0),
        ):
            with self.subTest(entry=entry_bit, exit=exit_bit):
                entry_status = VDP_STATUS_VBLANK if entry_bit else 0
                exit_status = VDP_STATUS_VBLANK if exit_bit else 0
                self.assertEqual(
                    _vblank_overrun_count_after_interrupt(
                        boot_stage=BOOT_STAGE_RUNNING,
                        entry_status=entry_status,
                        exit_status=exit_status,
                        counter=7,
                    ),
                    7 + increment,
                )

    def test_pre_running_vblank_is_acknowledgement_only(self) -> None:
        for entry_bit in (False, True):
            for exit_bit in (False, True):
                with self.subTest(entry=entry_bit, exit=exit_bit):
                    self.assertEqual(
                        _vblank_overrun_count_after_interrupt(
                            boot_stage=BOOT_STAGE_VIDEO_READY,
                            entry_status=(
                                VDP_STATUS_VBLANK if entry_bit else 0
                            ),
                            exit_status=(
                                VDP_STATUS_VBLANK if exit_bit else 0
                            ),
                            counter=7,
                        ),
                        7,
                    )

    def test_vblank_overrun_counter_saturates(self) -> None:
        for counter in (WORD_MAX - 1, WORD_MAX):
            with self.subTest(counter=counter):
                self.assertEqual(
                    _vblank_overrun_count_after_interrupt(
                        boot_stage=BOOT_STAGE_RUNNING,
                        entry_status=0,
                        exit_status=0,
                        counter=counter,
                    ),
                    WORD_MAX,
                )

    def test_translation_unit_includes_renderer_after_gameplay(self) -> None:
        source = (ROOT / "src/main.asm").read_text("utf-8")
        gameplay = 'include "gameplay/gameplay.asm"'
        renderer = 'include "render/gameplay_renderer.asm"'

        self.assertIn(gameplay, source)
        self.assertIn(renderer, source)
        self.assertLess(source.index(gameplay), source.index(renderer))

    def test_startup_exclusively_calls_renderer_sat_path(self) -> None:
        source = (ROOT / "src/boot/startup.asm").read_text("utf-8")

        self.assertNotIn("bsr.w   SpriteInitialize", source)
        self.assertNotIn("bsr.w   SpriteUpdateAndQueue", source)
        self.assertIn("jsr     (GameplayRendererInitialize).l", source)
        self.assertIn("jsr     (GameplayRendererBuildAndQueue).l", source)
        self.assertLess(
            source.index("bsr.w   GameplayInitialize"),
            source.index("jsr     (GameplayRendererInitialize).l"),
        )
        self.assertLess(
            source.index("MainLoopPostGameplayStep:"),
            source.index("jsr     (GameplayRendererBuildAndQueue).l"),
        )

    def test_main_loop_masks_only_the_frame_token_transaction(self) -> None:
        source = (ROOT / "src/boot/startup.asm").read_text("utf-8")
        loop = source.split("MainLoop:", 1)[1].split(
            "MainLoopWaitForVBlank:", 1
        )[0]
        mask = loop.index("move.w  #$2700,sr")
        consume = loop.index("subq.w  #1,(DEBUG_FRAME_TOKEN).l")
        restore = loop.index("move.w  #$2500,sr")
        first_work = loop.index("bsr.w   M2DebugFatalFixture")

        self.assertLess(mask, consume)
        self.assertLess(consume, restore)
        self.assertLess(restore, first_work)

    def test_startup_primes_one_ack_only_interrupt_before_running(self) -> None:
        source = (ROOT / "src/boot/startup.asm").read_text("utf-8")
        startup = source.split("MainLoop:", 1)[0]
        status_read = "move.w  (VDP_CONTROL).l,d0"
        display_on = "move.w  #VDP_REGISTER_DISPLAY_ON,(VDP_CONTROL).l"
        prime = "stop    #$2500"
        remask = "move.w  #$2700,sr"
        running = "move.w  #BOOT_STAGE_RUNNING,(DEBUG_BOOT_STAGE).l"
        wait = "bra.w   MainLoopWaitForVBlank"

        first_ack = startup.index(status_read)
        enable = startup.index(display_on)
        second_ack = startup.index(status_read, first_ack + 1)
        prime_interrupt = startup.index(prime, second_ack)
        remask_after_prime = startup.index(remask, prime_interrupt)
        running_stage = startup.index(running)
        wait_for_edge = startup.index(wait)

        self.assertLess(first_ack, enable)
        self.assertLess(enable, second_ack)
        self.assertLess(second_ack, prime_interrupt)
        self.assertLess(prime_interrupt, remask_after_prime)
        self.assertLess(remask_after_prime, running_stage)
        self.assertLess(running_stage, wait_for_edge)
        self.assertNotIn("clr.w   (DEBUG_FRAME_TOKEN).l", startup)
        self.assertNotIn("clr.l   (DEBUG_FRAME_COUNTER).l", startup)
        self.assertNotIn("clr.l   (DEBUG_HEARTBEAT).l", startup)
        self.assertNotIn("RENDERER_BACKLOG_FRAMES", startup)
        self.assertIn(
            "MainLoopWaitForVBlank:\n        stop    #$2500",
            source,
        )

    def test_pre_running_vblank_path_has_no_runtime_side_effects(self) -> None:
        source = (ROOT / "src/hardware/interrupts.asm").read_text("utf-8")
        handler = source.split("VBlankInterrupt:", 1)[1]
        ack = handler.index("move.w  (VDP_CONTROL).l,d0")
        stage = handler.index(
            "cmpi.w  #BOOT_STAGE_RUNNING,(DEBUG_BOOT_STAGE).l"
        )
        fast_return = handler.index("bne.w   .startup_return")
        return_label = handler.index(".startup_return:")

        self.assertLess(ack, stage)
        self.assertLess(stage, fast_return)
        side_effects = (
            "addq.l  #1,(DEBUG_SEQUENCE).l",
            "move.w  d0,(DEBUG_LAST_VDP_STATUS).l",
            "bsr.w   ControllerPoll",
            "addq.l  #1,(DEBUG_FRAME_COUNTER).l",
            "addq.l  #1,(DEBUG_HEARTBEAT).l",
            "addq.w  #1,(DEBUG_FRAME_TOKEN).l",
            "bsr.w   DmaCommitPreparedQueue",
            "move.l  #VDP_COMMAND_PULSE_CRAM_WRITE,(VDP_CONTROL).l",
            "move.w  d0,(DEBUG_ENGINE_STATE).l",
        )
        for statement in side_effects:
            side_effect = handler.index(statement)
            self.assertLess(fast_return, side_effect)
            self.assertLess(side_effect, return_label)
        self.assertEqual(handler.count("\n        rte"), 1)
        self.assertIn(
            ".startup_return:\n"
            "        movem.l (a7)+,d0-d7/a0-a6\n"
            "        rte",
            handler,
        )

    def test_current_debug_image_links_both_renderer_entry_points(self) -> None:
        table = symbols(ROOT / "build/recursion94-debug.sym")
        raw = (ROOT / "build/recursion94-debug.raw").read_bytes()

        initialize = table["GameplayRendererInitialize"]
        per_frame = table["GameplayRendererBuildAndQueue"]
        self.assertGreater(initialize, 0x200)
        self.assertGreater(per_frame, initialize)
        self.assertLess(per_frame, len(raw))


if __name__ == "__main__":
    unittest.main()
