from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tools.campaign_runtime.compiler import build_runtime_artifacts


ROOT = Path(__file__).resolve().parents[2]
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


def _local_section(routine: str, label: str, next_label: str) -> str:
    return routine.split(f"{label}:", 1)[1].split(f"{next_label}:", 1)[0]


class TerminalHoldContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.actor_sim = _source("src/gameplay/actor_sim.asm")
        cls.interactions = _source(
            "src/gameplay/vocabulary_interactions.asm"
        )
        cls.campaign_runtime = _source(
            "src/gameplay/campaign_runtime.asm"
        )
        cls.layout = _source("src/gameplay/vocabulary_layout.asm")
        artifacts = build_runtime_artifacts(ROOT / "content")
        cls.report = json.loads(
            artifacts.files["campaign_runtime_report.json"].decode("utf-8")
        )

    def _objects(self, encounter_id: str) -> dict[str, dict[str, object]]:
        encounter = next(
            item
            for item in self.report["encounters"]
            if item["id"] == encounter_id
        )
        return {
            item["authored_logical_id"]: item
            for item in encounter["objects"]
        }

    def test_b_edge_resolves_and_later_held_b_uses_sustain_path(
        self,
    ) -> None:
        apply_input = _routine(
            self.actor_sim, "GameplayApplyActorInput"
        )
        interaction = _local_section(
            apply_input, ".interaction", ".outputs"
        )
        edge, held = interaction.split(".interaction_held:", 1)

        self.assertIn("move.b  d5,d0", edge)
        self.assertIn("andi.b  #INPUT_B,d0", edge)
        self.assertIn("bsr.w   VocabularyResolveInteraction", edge)
        self.assertNotIn("VocabularySustainHackInteraction", edge)

        self.assertIn("move.b  d3,d0", held)
        self.assertIn("andi.b  #INPUT_B,d0", held)
        self.assertIn("bsr.w   VocabularySustainHackInteraction", held)
        self.assertNotIn("VocabularyResolveInteraction", held)

    def test_terminal_advance_uses_gate_and_generated_semantic_duration(
        self,
    ) -> None:
        advance = _routine(
            self.interactions, "VocabularyAdvanceHackInteraction"
        )
        gate = advance.index(
            "bsr.w   CampaignDependencyGateSatisfied"
        )
        gate_failure = advance.index("beq.w   .no_target", gate)
        fallback = advance.index("moveq   #1,d4", gate_failure)
        logical_id = advance.index(
            "move.b  (OBJECT_LOGICAL_ID,a3),d0", fallback
        )
        duration = advance.index(
            "bsr.w   CampaignGetSemanticDuration", logical_id
        )
        valid = advance.index("tst.w   d1", duration)
        nonzero = advance.index("tst.w   d0", valid)
        accept = advance.index("move.w  d0,d4", nonzero)

        self.assertEqual(
            [gate, gate_failure, fallback, logical_id, duration, valid,
             nonzero, accept],
            sorted(
                [gate, gate_failure, fallback, logical_id, duration, valid,
                 nonzero, accept]
            ),
        )
        self.assertIn("bne.s   .duration_ready", advance[valid:nonzero])
        self.assertIn("beq.s   .duration_ready", advance[nonzero:accept])

    def test_already_hacked_terminal_fails_closed_without_relabeling(
        self,
    ) -> None:
        advance = _routine(
            self.interactions, "VocabularyAdvanceHackInteraction"
        )
        already_hacked = _local_section(
            advance, ".already_hacked", ".no_target"
        )
        self.assertIn(
            "moveq   #VOCAB_RESULT_NO_TARGET,d0", already_hacked
        )
        self.assertNotIn("#VOCAB_RESULT_HACKED", already_hacked)
        self.assertIn("bra.s   .done", already_hacked)

        resolve = _routine(
            self.interactions, "VocabularyResolveInteraction"
        )
        hack = _local_section(resolve, ".hack", ".instant_hack")
        advance_call = hack.index(
            "bsr.w   VocabularyAdvanceHackInteraction"
        )
        no_target = hack.index(
            "cmpi.w  #VOCAB_RESULT_NO_TARGET,d0", advance_call
        )
        reject = hack.index("beq.w   .no_target", no_target)
        action_ready = hack.index("bra.s   .action_ready", reject)
        self.assertEqual(
            [advance_call, no_target, reject, action_ready],
            sorted([advance_call, no_target, reject, action_ready]),
        )

    def test_progress_owns_touches_and_resets_when_not_sustained(
        self,
    ) -> None:
        self.assertIn(
            "VOCAB_DEVICE_HOLD_OWNER         equ     OBJECT_VX+2",
            self.layout,
        )
        self.assertIn(
            "VOCAB_DEVICE_HOLD_TOUCHED       equ     OBJECT_VY+2",
            self.layout,
        )

        advance = _routine(
            self.interactions, "VocabularyAdvanceHackInteraction"
        )
        owner = advance.index(
            "cmp.w   (VOCAB_DEVICE_HOLD_OWNER,a3),d5"
        )
        clear_timer = advance.index(
            "clr.w   (OBJECT_TIMER,a3)", owner
        )
        clear_owner_word = advance.index("clr.l   (OBJECT_VX,a3)", owner)
        clear_touch_word = advance.index("clr.l   (OBJECT_VY,a3)", owner)
        store_owner = advance.index(
            "move.w  d5,(VOCAB_DEVICE_HOLD_OWNER,a3)", owner
        )
        load_progress = advance.index(
            "move.w  (OBJECT_TIMER,a3),d6", store_owner
        )
        increment = advance.index("addq.w  #1,d6", load_progress)
        store_progress = advance.index(
            "move.w  d6,(OBJECT_TIMER,a3)", increment
        )
        store_complete = advance.index(
            "move.w  d4,(OBJECT_TIMER,a3)", store_progress
        )
        tick = advance.index("move.w  (ENGINE_TICK).l,d0", store_complete)
        next_tick = advance.index("addq.w  #1,d0", tick)
        clear_touch = advance.index("clr.l   (OBJECT_VY,a3)", next_tick)
        touch = advance.index(
            "move.w  d0,(VOCAB_DEVICE_HOLD_TOUCHED,a3)", clear_touch
        )
        self.assertEqual(
            [
                owner,
                clear_timer,
                clear_owner_word,
                clear_touch_word,
                store_owner,
                load_progress,
                increment,
                store_progress,
                store_complete,
                tick,
                next_tick,
                clear_touch,
                touch,
            ],
            sorted(
                [
                    owner,
                    clear_timer,
                    clear_owner_word,
                    clear_touch_word,
                    store_owner,
                    load_progress,
                    increment,
                    store_progress,
                    store_complete,
                    tick,
                    next_tick,
                    clear_touch,
                    touch,
                ]
            ),
        )
        self.assertIn(
            "#VOCAB_DEVICE_STATE_ACTIVE|VOCAB_DEVICE_STATE_HACKED",
            advance[store_complete:tick],
        )

        update = _routine(
            self.interactions, "VocabularyUpdateDevices"
        )
        terminal = _local_section(update, ".hack_terminal", ".pressure")
        hacked = terminal.index("#VOCAB_DEVICE_STATE_HACKED")
        timer = terminal.index("tst.w   (OBJECT_TIMER,a2)", hacked)
        tick = terminal.index("move.w  (ENGINE_TICK).l,d0", timer)
        touch = terminal.index(
            "cmp.w   (VOCAB_DEVICE_HOLD_TOUCHED,a2),d0", tick
        )
        reset_timer = terminal.index("clr.w   (OBJECT_TIMER,a2)", touch)
        reset_owner = terminal.index("clr.l   (OBJECT_VX,a2)", reset_timer)
        reset_touch = terminal.index("clr.l   (OBJECT_VY,a2)", reset_owner)
        clear_active = terminal.index(
            "andi.w  #$FFFE,(OBJECT_STATE,a2)", reset_touch
        )
        self.assertEqual(
            [
                hacked,
                timer,
                tick,
                touch,
                reset_timer,
                reset_owner,
                reset_touch,
                clear_active,
            ],
            sorted(
                [
                    hacked,
                    timer,
                    tick,
                    touch,
                    reset_timer,
                    reset_owner,
                    reset_touch,
                    clear_active,
                ]
            ),
        )
        self.assertIn("addq.w  #1,d0", terminal[tick:touch])
        self.assertIn("beq.w   .next", terminal[touch:reset_timer])

    def test_sustain_advances_only_the_owned_terminal_and_publishes(
        self,
    ) -> None:
        sustain = _routine(
            self.interactions, "VocabularySustainHackInteraction"
        )
        self.assertIn("lea     (DEVICE_POOL).l,a3", sustain)
        self.assertIn("moveq   #DEVICE_CAPACITY-1,d7", sustain)
        self.assertIn("dbf     d7,.terminal", sustain)
        self.assertIn("move.w  d3,d4", sustain)
        self.assertIn("addq.w  #1,d4", sustain)

        active = sustain.index("tst.b   (OBJECT_ACTIVE,a3)")
        kind = sustain.index(
            "cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d0", active
        )
        owner = sustain.index(
            "cmp.w   (VOCAB_DEVICE_HOLD_OWNER,a3),d4", kind
        )
        progress = sustain.index("tst.w   (OBJECT_TIMER,a3)", owner)
        range_check = sustain.index(
            "bsr.w   VocabularyWithinRange", progress
        )
        advance = sustain.index(
            "bsr.w   VocabularyAdvanceHackInteraction", range_check
        )
        self.assertEqual(
            [active, kind, owner, progress, range_check, advance],
            sorted([active, kind, owner, progress, range_check, advance]),
        )

        success = sustain[
            advance : sustain.index(".next:", advance)
        ]
        self.assertIn(
            "(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l", success
        )
        self.assertIn(
            "(VOCAB_CAMPAIGN_LAST_INTERACTION_OBJECT).l", success
        )
        self.assertIn("(VOCAB_ACTOR_LAST_ACTION,a4)", success)
        self.assertIn("bra.s   .publish", success)

        publish = _local_section(sustain, ".publish", ".done")
        self.assertEqual(
            publish.count(
                "move.w  d0,(VOCAB_INTERACTION_RESULT).l"
            ),
            1,
        )
        self.assertEqual(
            publish.count("move.w  d0,(VOCAB_RESULT).l"),
            1,
        )

    def test_dependency_helper_validates_group_and_declared_result(
        self,
    ) -> None:
        helper = _routine(
            self.campaign_runtime, "CampaignDependencyGateSatisfied"
        )
        group = helper.index("andi.w  #VOCAB_PARAM_LINK_MASK,d3")
        count = helper.index(
            "move.b  (VOCAB_CAMPAIGN_GROUP_COUNT).l,d4", group
        )
        bound = helper.index("cmp.w   d4,d3", count)
        bound_failure = helper.index("bhi.w   .contract_error", bound)
        pointer = helper.index(
            "movea.l (VOCAB_CAMPAIGN_GROUP_PTR).l,a3", bound_failure
        )
        pointer_zero = helper.index("beq.w   .contract_error", pointer)
        pointer_odd = helper.index("btst    #0,d0", pointer_zero)
        stride = helper.index(
            "mulu.w  #CAMPAIGN_RUNTIME_DEPENDENCY_GROUP_BYTES,d0",
            pointer_odd,
        )
        consumer = helper.index(
            "move.b  (CAMPAIGN_RUNTIME_GROUP_CONSUMER,a3),d0", stride
        )
        logical_id = helper.index(
            "move.b  (OBJECT_LOGICAL_ID,a2),d1", consumer
        )
        consumer_match = helper.index("cmp.w   d1,d0", logical_id)
        mask = helper.index(
            "move.w  (CAMPAIGN_RUNTIME_GROUP_RESULT_MASK,a3),d5",
            consumer_match,
        )
        field = helper.index(
            "move.b  (CAMPAIGN_RUNTIME_GROUP_RESULT_FIELD,a3),d0", mask
        )
        read_field = helper.index(
            "bsr.w   CampaignReadObjectField", field
        )
        live_mask = helper.index("and.w   d5,d0", read_field)
        value = helper.index(
            "move.w  (CAMPAIGN_RUNTIME_GROUP_RESULT_VALUE,a3),d6",
            live_mask,
        )
        value_mask = helper.index("and.w   d5,d6", value)
        compare = helper.index("cmp.w   d6,d0", value_mask)
        self.assertEqual(
            [
                group,
                count,
                bound,
                bound_failure,
                pointer,
                pointer_zero,
                pointer_odd,
                stride,
                consumer,
                logical_id,
                consumer_match,
                mask,
                field,
                read_field,
                live_mask,
                value,
                value_mask,
                compare,
            ],
            sorted(
                [
                    group,
                    count,
                    bound,
                    bound_failure,
                    pointer,
                    pointer_zero,
                    pointer_odd,
                    stride,
                    consumer,
                    logical_id,
                    consumer_match,
                    mask,
                    field,
                    read_field,
                    live_mask,
                    value,
                    value_mask,
                    compare,
                ]
            ),
        )
        self.assertIn("beq.w   .contract_error", helper[mask:field])
        self.assertIn("tst.w   d1", helper[read_field:live_mask])
        self.assertIn(
            "#VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR", helper[compare:]
        )

    def test_b01_b03_authored_states_and_durations_are_truthful(
        self,
    ) -> None:
        b01 = json.loads(
            (ROOT / "content/encounters/boot/B01.json").read_text(
                encoding="utf-8"
            )
        )
        b01_devices = {
            item["logical_id"]: item
            for item in b01["entities"]["devices"]
        }
        self.assertEqual(
            b01_devices["B01_DOOR_A"]["state"]["states"],
            ["closed", "open"],
        )
        self.assertEqual(
            b01_devices["B01_COMMIT"]["state"]["states"],
            ["locked", "armed", "committed"],
        )
        self.assertEqual(
            b01_devices["B01_COMMIT"]["parameters"]["interact_frames"],
            24,
        )
        b01_objects = self._objects("B01")
        self.assertEqual(
            b01_objects["B01_DOOR_A"]["semantic_record"][
                "duration_frames"
            ],
            0,
        )
        self.assertEqual(
            b01_objects["B01_COMMIT"]["semantic_record"][
                "duration_frames"
            ],
            24,
        )

        b03 = json.loads(
            (ROOT / "content/encounters/boot/B03.json").read_text(
                encoding="utf-8"
            )
        )
        b03_devices = {
            item["logical_id"]: item
            for item in b03["entities"]["devices"]
        }
        self.assertEqual(
            b03_devices["B03_SWITCH_B"]["state"]["states"],
            ["idle", "active"],
        )
        self.assertEqual(
            b03_devices["B03_DOOR"]["state"]["states"],
            ["closed", "open"],
        )
        self.assertEqual(
            b03_devices["B03_COMMIT"]["state"]["states"],
            ["locked", "armed", "committed"],
        )
        self.assertEqual(
            b03_devices["B03_COMMIT"]["parameters"]["interact_frames"],
            20,
        )
        b03_hazard = b03["entities"]["hazards"][0]
        self.assertEqual(
            b03_hazard["state"]["states"],
            ["safe", "warning", "closing"],
        )

        b03_objects = self._objects("B03")
        for logical_id, duration in (
            ("B03_SWITCH_B", 180),
            ("B03_DOOR", 150),
            ("B03_COMMIT", 20),
            ("B03_SHUTTER_FIELD", 20),
        ):
            self.assertEqual(
                b03_objects[logical_id]["semantic_record"][
                    "duration_frames"
                ],
                duration,
            )


if __name__ == "__main__":
    unittest.main()
