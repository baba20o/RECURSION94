from __future__ import annotations

import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_PATH = ROOT / "src/gameplay/campaign_runtime.asm"
GENERATED_PATH = (
    ROOT / "assets/generated/campaign_runtime/campaign_runtime.inc.asm"
)
WARNING_RE = re.compile(r"(?i)(?:^|\s)warning(?:\s+\w+)?\s*(?::|\[)")
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


def _positions(source: str, fragments: tuple[str, ...]) -> list[int]:
    positions: list[int] = []
    start = 0
    for fragment in fragments:
        position = source.index(fragment, start)
        positions.append(position)
        start = position + len(fragment)
    return positions


class CampaignRuntimeControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CONTROLLER_PATH.read_text(encoding="utf-8")
        cls.generated = GENERATED_PATH.read_text(encoding="utf-8")

    def test_load_validates_v3_profile_before_engine_commit(self) -> None:
        load = _routine(self.source, "CampaignLoadEncounter")

        for assertion in (
            "CAMPAIGN_RUNTIME_FORMAT_VERSION=CAMPAIGN_CONTROLLER_FORMAT_VERSION",
            "CAMPAIGN_RUNTIME_PROFILE_BYTES=64",
            "CAMPAIGN_RUNTIME_SEMANTIC_BYTES=16",
            "CAMPAIGN_RUNTIME_DEPENDENCY_GROUP_BYTES=16",
            "CAMPAIGN_RUNTIME_DEPENDENCY_EDGE_BYTES=8",
        ):
            self.assertIn(assertion, self.source)

        required = (
            "#CAMPAIGN_RUNTIME_FORMAT_VERSION",
            "#CAMPAIGN_RUNTIME_ENCOUNTER_COUNT,d7",
            "(CampaignRuntimeDescriptorTable).l",
            "(CampaignRuntimeProfileTable).l",
            "(CAMPAIGN_RUNTIME_PROFILE_ECHO_CAP,a4)",
            "#GAMEPLAY_ECHO_LIMIT,d4",
            "(CAMPAIGN_RUNTIME_PROFILE_SEMANTIC_COUNT,a4)",
            "(CAMPAIGN_RUNTIME_PROFILE_GROUP_COUNT,a4)",
            "(CAMPAIGN_RUNTIME_PROFILE_EDGE_COUNT,a4)",
            "(CAMPAIGN_RUNTIME_PROFILE_SEMANTIC_PTR,a4)",
            "(CAMPAIGN_RUNTIME_PROFILE_GROUP_PTR,a4)",
            "(CAMPAIGN_RUNTIME_PROFILE_EDGE_PTR,a4)",
            "bsr.w   EngineLoadDescriptor",
            (
                "move.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|"
                "VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,"
                "(VOCAB_CAMPAIGN_FLAGS).l"
            ),
        )
        positions = _positions(load, required)
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("EngineLoadEncounter", load)
        self.assertIn(
            "cmp.w   (ENGINE_OBJECT_INIT_COUNT).l,d5",
            load,
        )

    def test_load_binds_projected_values_and_only_derived_rom_pointers(
        self,
    ) -> None:
        load = _routine(self.source, "CampaignLoadEncounter")

        for projected in (
            "VOCAB_CAMPAIGN_DIRECTORY_INDEX",
            "VOCAB_CAMPAIGN_ECHO_CAP",
            "VOCAB_CAMPAIGN_SEMANTIC_COUNT",
            "VOCAB_CAMPAIGN_GROUP_COUNT",
            "VOCAB_CAMPAIGN_EDGE_COUNT",
            "VOCAB_CAMPAIGN_COMPLETION_OBJECT",
            "VOCAB_CAMPAIGN_COMPLETION_FIELD",
            "VOCAB_CAMPAIGN_COMPLETION_MASK",
            "VOCAB_CAMPAIGN_COMPLETION_VALUE",
            "VOCAB_STAGE_ID",
        ):
            self.assertIn(f"({projected}).l", load)
        for pointer in (
            "VOCAB_CAMPAIGN_PROFILE_PTR",
            "VOCAB_CAMPAIGN_SEMANTIC_PTR",
            "VOCAB_CAMPAIGN_GROUP_PTR",
            "VOCAB_CAMPAIGN_EDGE_PTR",
        ):
            self.assertRegex(load, rf"move\.l\s+a[2456],\({pointer}\)\.l")
        self.assertIn(
            "VOCAB_CAMPAIGN_DERIVED_END<=VOCAB_SCRATCH_BASE",
            self.source,
        )

    def test_reset_and_begin_frame_touch_only_bounded_attempt_state(self) -> None:
        reset = _routine(self.source, "CampaignResetAttemptState")
        begin = _routine(self.source, "CampaignBeginFrame")

        self.assertIn(
            "#(VOCAB_CAMPAIGN_DEPENDENCY_LATCH_BYTES/4)-1,d1",
            reset,
        )
        self.assertEqual(reset.count("dbf     d1,.clear_latches"), 1)
        self.assertIn(
            "#$FFFF,(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l",
            reset,
        )
        self.assertIn(
            "#$FFFF,(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l",
            begin,
        )
        self.assertIn("clr.w   (VOCAB_CAMPAIGN_EVENT_FLAGS).l", begin)
        self.assertNotIn("ENGINE_TICK", begin)
        self.assertNotIn("OBJECT_TIMER", begin)

    def test_semantic_and_live_object_lookup_cannot_alias_transients(
        self,
    ) -> None:
        semantic = _routine(self.source, "CampaignFindSemantic")
        objects = _routine(self.source, "CampaignFindObject")

        self.assertIn("(VOCAB_CAMPAIGN_SEMANTIC_COUNT).l", semantic)
        self.assertIn("lsl.w   #4,d1", semantic)
        self.assertIn(
            "(CAMPAIGN_RUNTIME_SEMANTIC_OBJECT,a0)",
            semantic,
        )
        self.assertIn("(EnginePoolDescriptorTable).l", objects)
        self.assertIn("#POOL_TYPE_PROJECTILE,d3", objects)
        self.assertIn("#POOL_TYPE_EFFECT,d3", objects)
        self.assertIn("(OBJECT_LOGICAL_ID,a3)", objects)
        self.assertIn("dbf     d4,.slot", objects)

    def test_field_access_maps_live_offsets_and_virtual_health(self) -> None:
        read = _routine(self.source, "CampaignReadObjectField")
        write = _routine(self.source, "CampaignWriteObjectField")

        for field in (
            "CAMPAIGN_RUNTIME_REFERENCE_STATE",
            "CAMPAIGN_RUNTIME_REFERENCE_TIMER",
            "CAMPAIGN_RUNTIME_REFERENCE_PARAM",
            "CAMPAIGN_RUNTIME_REFERENCE_HEALTH",
        ):
            self.assertIn(field, read)
            self.assertIn(field, write)
        for offset in ("OBJECT_STATE", "OBJECT_TIMER", "OBJECT_PARAM"):
            self.assertIn(f"({offset},a0)", read)
            self.assertIn(f"({offset},a0)", write)
        self.assertIn("move.b  (OBJECT_STATE,a0),d0", read)
        self.assertIn("andi.w  #$00FF,d2", write)
        self.assertIn("lsl.w   #8,d1", write)

    def test_live_lookup_skips_only_zero_count_pools_in_original_order(self) -> None:
        objects = _routine(self.source, "CampaignFindObject")
        required = (
            "bsr.w   CampaignFindSemantic",
            "beq.s   .not_found",
            "lea     (ENGINE_POOL_COUNTS-1).l,a1",
            "moveq   #POOL_TYPE_ENEMY,d3",
            "cmpi.w  #POOL_TYPE_PROJECTILE,d3",
            "beq.s   .next_pool",
            "cmpi.w  #POOL_TYPE_EFFECT,d3",
            "beq.s   .next_pool",
            "tst.b   (a1,d3.w)",
            "beq.s   .next_pool",
            "movea.l (a2),a3",
            "move.w  4(a2),d4",
            "moveq   #0,d5",
            "tst.b   (OBJECT_ACTIVE,a3)",
            "move.b  (OBJECT_LOGICAL_ID,a3),d0",
            "cmp.w   d7,d0",
            "beq.s   .found",
            "addq.w  #1,d5",
            "dbf     d4,.slot",
            "addq.w  #1,d3",
            "cmpi.w  #POOL_TYPE_COUNT,d3",
            "bls.s   .pool",
        )
        positions = _positions(objects, required)
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(objects.count("(a1,d3.w)"), 1)
        self.assertNotIn("ENGINE_POOL_HIGH_WATER", objects)
        self.assertNotIn("CANONICAL_OBJECTS", objects)

    def test_live_lookup_retains_register_abi_and_has_no_state_writes(self) -> None:
        objects = _routine(self.source, "CampaignFindObject")
        self.assertIn("movem.l d3-d5/d7/a2-a3,-(a7)", objects)
        self.assertIn("movem.l (a7)+,d3-d5/d7/a2-a3", objects)
        self.assertIn("move.w  d3,d0", objects)
        self.assertIn("move.w  d5,d1", objects)
        self.assertIn("movea.l a3,a0", objects)
        self.assertIn("moveq   #-1,d0", objects)
        self.assertIn("moveq   #-1,d1", objects)
        self.assertIn("suba.l  a0,a0", objects)
        instructions = [
            line.split(";", 1)[0].strip()
            for line in objects.splitlines()
            if line.startswith("        ") and line.split(";", 1)[0].strip()
        ]
        read_or_control = {
            "bsr.w", "cmpi.w", "beq.s", "tst.b", "cmp.w", "dbf",
            "bls.s", "bra.s", "rts",
        }
        register_writers = {
            "move.w", "lea", "moveq", "movea.l", "subq.w", "move.b",
            "adda.w", "addq.w", "suba.l",
        }
        for instruction in instructions:
            if instruction.startswith("movem.l"):
                continue  # The balanced ABI save/restore is the only RAM write.
            mnemonic = instruction.split()[0]
            self.assertIn(mnemonic, read_or_control | register_writers)
            if mnemonic in register_writers:
                destination = instruction.rsplit(",", 1)[-1].strip()
                self.assertRegex(destination, r"^(?:d[0-7]|a[0-6])$", instruction)
        self.assertEqual(objects.count("bsr.w"), 1)
        self.assertNotRegex(objects, r"\b(?:d6|a[4-6])\b")

    def test_lookup_count_invariant_matches_pool_lifecycle_owners(self) -> None:
        pools = (ROOT / "src/game/object_pool.asm").read_text(encoding="utf-8")
        engine = (ROOT / "src/game/engine_core.asm").read_text(encoding="utf-8")
        combat = (ROOT / "src/gameplay/vocabulary_combat.asm").read_text(
            encoding="utf-8"
        )
        note = _routine(pools, "EnginePoolNoteAllocation")
        free = _routine(pools, "EnginePoolFreeSlot")
        self.assertIn("lea     (ENGINE_POOL_COUNTS).l,a1", note)
        self.assertIn("addq.b  #1,(a1)", note)
        self.assertIn("lea     (ENGINE_POOL_COUNTS).l,a1", free)
        self.assertIn("subq.b  #1,(a1)", free)
        self.assertLess(free.index(".clear:"), free.index("subq.b  #1,(a1)"))
        for source, label in (
            (pools, "EngineInstantiateCanonicalObject"),
            (pools, "EngineSpawnProjectileFromActor"),
            (combat, "VocabularySpawnEnemyProjectile"),
        ):
            routine = _routine(source, label)
            positions = _positions(routine, (
                "bsr.w   EnginePoolAllocateLowest",
                "bsr.w   EnginePoolNoteAllocation",
            ))
            self.assertEqual(positions, sorted(positions))
        reconstruct = _routine(engine, "EngineReconstructCanonicalState")
        positions = _positions(reconstruct, (
            "lea     (ENGINE_POOL_COUNTS).l,a0",
            ".clear_pool_counters:",
            "bsr.w   EngineInstantiateCanonicalObject",
        ))
        self.assertEqual(positions, sorted(positions))

    def test_dependencies_are_directed_latched_and_frame_independent(
        self,
    ) -> None:
        evaluate = _routine(self.source, "CampaignEvaluateDependencies")

        self.assertIn("#CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ALL,d4", evaluate)
        self.assertIn("#CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ANY,d4", evaluate)
        self.assertIn("(CAMPAIGN_RUNTIME_EDGE_PROVIDER,a5)", evaluate)
        self.assertIn("dbf     d3,.edge", evaluate)
        self.assertIn("dbf     d7,.group", evaluate)
        self.assertIn("btst    d1,(a2)", evaluate)
        self.assertIn("bset    d1,(a2)", evaluate)
        self.assertIn("bclr    d1,(a2)", evaluate)
        self.assertEqual(
            evaluate.count("#VOCAB_CAMPAIGN_EVENT_DEPENDENCY"),
            2,
        )
        self.assertIn("(CAMPAIGN_RUNTIME_GROUP_DURATION,a6)", evaluate)

        maintain = evaluate.split("\n; While latched", 1)[1].split(
            "\n.rise:",
            1,
        )[0]
        self.assertIn("CampaignReadObjectField", maintain)
        self.assertNotIn("CampaignWriteObjectField", maintain)
        self.assertNotIn("ENGINE_TICK", evaluate)
        self.assertNotIn("subq.w  #1,(OBJECT_TIMER", evaluate)
        self.assertNotIn("EngineUpdateAllPools", evaluate)
        self.assertNotRegex(evaluate, r"(?i)union|find[_ ]?root")

    def test_completion_requires_current_interaction_before_terminalizing(
        self,
    ) -> None:
        completion = _routine(self.source, "CampaignCheckCompletion")
        required = (
            (
                "cmpi.b  #GAMEPLAY_CURRENT_ACTOR_SLOT,"
                "(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l"
            ),
            (
                "cmp.b   (VOCAB_CAMPAIGN_LAST_INTERACTION_OBJECT).l,"
                "d3"
            ),
            "bsr.w   CampaignFindObject",
            "bsr.w   CampaignReadObjectField",
            "bsr.w   VocabularyFinalizeStage",
            "#ENGINE_STATE_TERMINAL,(ENGINE_STATE).l",
            (
                "#GAMEPLAY_TERMINAL_PROGRAM_STABLE,"
                "(ENGINE_TERMINAL_REASON).l"
            ),
            "#GAMEPLAY_RESULT_PROGRAM_STABLE,d0",
        )
        positions = _positions(completion, required)
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            "#VOCAB_CAMPAIGN_FLAG_COMPLETE",
            completion,
        )

    def test_generated_tables_and_temporary_fixture_assemble_warning_free(
        self,
    ) -> None:
        self.assertIn(
            "CAMPAIGN_RUNTIME_FORMAT_VERSION equ $0003",
            self.generated,
        )
        assembler = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(assembler.is_file(), assembler)
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="m5-campaign-controller-",
            dir=ROOT / "build",
        ) as temporary:
            temporary_path = Path(temporary)
            fixture = temporary_path / "campaign_controller.asm"
            output = temporary_path / "campaign_controller.bin"
            fixture.write_text(
                textwrap.dedent(
                    """\
                            include "src/game/engine_layout.asm"
                            include "src/gameplay/gameplay_layout.asm"
                            include "src/gameplay/vocabulary_layout.asm"

                            org     $000000

                            include "src/gameplay/campaign_runtime.asm"

                    EngineLoadDescriptor:
                            moveq   #0,d0
                            rts

                    VocabularyFinalizeStage:
                            moveq   #VOCAB_RESULT_STAGE_COMPLETE,d0
                            rts

                    EnginePoolDescriptorTable:
                            dc.l    ENEMY_POOL
                            dc.w    ENEMY_CAPACITY,OVERFLOW_ENEMY
                            dc.l    PROJECTILE_POOL
                            dc.w    PROJECTILE_CAPACITY,OVERFLOW_PROJECTILE
                            dc.l    DEVICE_POOL
                            dc.w    DEVICE_CAPACITY,OVERFLOW_DEVICE
                            dc.l    CARRYABLE_POOL
                            dc.w    CARRYABLE_CAPACITY,OVERFLOW_CARRYABLE
                            dc.l    HAZARD_POOL
                            dc.w    HAZARD_CAPACITY,OVERFLOW_HAZARD
                            dc.l    BOSS_COMPONENT_POOL
                            dc.w    BOSS_COMPONENT_CAPACITY,OVERFLOW_BOSS
                            dc.l    EFFECT_POOL
                            dc.w    EFFECT_CAPACITY,OVERFLOW_EFFECT

                            include "assets/generated/content/campaign_content.inc.asm"
                            include "assets/generated/campaign_runtime/campaign_runtime.inc.asm"
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
            completed = subprocess.run(
                [
                    str(assembler),
                    "-Fbin",
                    "-m68000",
                    "-no-opt",
                    "-x",
                    "-Isrc",
                    "-o",
                    output.relative_to(ROOT).as_posix(),
                    fixture.relative_to(ROOT).as_posix(),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIsNone(WARNING_RE.search(completed.stdout), completed.stdout)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
