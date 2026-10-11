from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INTERACTIONS_PATH = ROOT / "src/gameplay/vocabulary_interactions.asm"
COMBAT_PATH = ROOT / "src/gameplay/vocabulary_combat.asm"
WARNING_RE = re.compile(r"(?i)(?:^|\s)warning(?:\s+\w+)?\s*(?::|\[)")
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


class VocabularyAssemblySemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.interactions = INTERACTIONS_PATH.read_text(encoding="utf-8")
        cls.combat = COMBAT_PATH.read_text(encoding="utf-8")

    def test_disabled_records_are_filtered_before_they_can_act(self) -> None:
        resolve = _routine(
            self.interactions, "VocabularyResolveInteraction"
        )
        devices = _routine(self.interactions, "VocabularyUpdateDevices")
        beams = _routine(self.interactions, "VocabularyBuildBeamCache")
        enemies = _routine(self.combat, "VocabularyUpdateEnemies")
        spawn = _routine(self.combat, "VocabularySpawnEnemyProjectile")
        hazards = _routine(
            self.combat, "VocabularyUpdateActorsAndHazards"
        )

        self.assertGreaterEqual(
            resolve.count("#VOCAB_DEVICE_STATE_DISABLED"), 2
        )
        self.assertGreaterEqual(
            devices.count("#VOCAB_DEVICE_STATE_DISABLED"), 2
        )
        self.assertIn(
            "beq.w   .next                   ; zero has no legacy group",
            devices,
        )
        self.assertGreaterEqual(
            beams.count("#VOCAB_DEVICE_STATE_DISABLED"), 2
        )
        self.assertIn("#VOCAB_ENEMY_STATE_DISABLED", enemies)
        self.assertIn("#VOCAB_ENEMY_STATE_DISABLED", spawn)
        self.assertGreaterEqual(
            hazards.count("#VOCAB_DEVICE_STATE_DISABLED"), 2
        )

    def test_repair_terminal_resolves_a_logical_id_in_bounded_pools(self) -> None:
        resolve = _routine(
            self.interactions, "VocabularyResolveInteraction"
        )
        repair = _routine(
            self.interactions, "VocabularyRepairLinkedTarget"
        )

        self.assertIn("bsr.w   VocabularyRepairLinkedTarget", resolve)
        disabled = resolve.index("#VOCAB_DEVICE_STATE_DISABLED")
        repair_kind = resolve.index(
            "#VOCAB_DEVICE_REPAIR_TERMINAL", disabled
        )
        passive_target = resolve.index(".device_actionable:", repair_kind)
        self.assertLess(disabled, repair_kind)
        self.assertLess(repair_kind, passive_target)
        self.assertIn("(OBJECT_PARAM+1,a2)", repair)
        self.assertIn("(OBJECT_LOGICAL_ID,a3)", repair)
        self.assertIn("(DEVICE_POOL).l", repair)
        self.assertIn("#DEVICE_CAPACITY-1", repair)
        self.assertIn("(ENEMY_POOL).l", repair)
        self.assertIn("#ENEMY_CAPACITY-1", repair)
        self.assertIn("#VOCAB_DEVICE_SECURITY_NODE", repair)
        self.assertIn("#VOCAB_ENEMY_HEALTH_MASK", repair)

    def test_friendly_projectile_priority_and_consumption_are_explicit(
        self,
    ) -> None:
        update = _routine(
            self.combat, "VocabularyUpdateProjectilesAndDamage"
        )
        enemy_pool = update.index("lea     (ENEMY_POOL).l,a3")
        device_pool = update.index("lea     (DEVICE_POOL).l,a3")
        node_damage = update.index("bsr.w   VocabularyDamageSecurityNode")
        applied_check = update.index("tst.w   d0", node_damage)
        consume = update.index("bsr.w   EnginePoolFreeSlot", applied_check)

        self.assertLess(enemy_pool, device_pool)
        self.assertLess(device_pool, node_damage)
        self.assertLess(node_damage, applied_check)
        self.assertLess(applied_check, consume)
        self.assertIn("#DEVICE_CAPACITY-1", update)

        damage = _routine(
            self.combat, "VocabularyDamageSecurityNode"
        )
        self.assertIn("#VOCAB_DEVICE_SECURITY_NODE", damage)
        self.assertIn("#VOCAB_DEVICE_STATE_ACTIVE", damage)
        self.assertIn("#VOCAB_DEVICE_STATE_DISABLED", damage)
        self.assertIn("#$00FE,d4", damage)

    def test_beam_cache_selects_nearest_forward_reflector_and_truncates(
        self,
    ) -> None:
        beams = _routine(self.interactions, "VocabularyBuildBeamCache")

        for label in (
            ".reflector_right:",
            ".reflector_down:",
            ".reflector_left:",
            ".reflector_distance:",
        ):
            self.assertIn(label, beams)
        self.assertIn(
            "#VOCAB_BEAM_STEP_PIXELS*VOCAB_BEAM_MAX_STEPS,d1", beams
        )
        self.assertIn("cmp.w   d3,d1", beams)
        self.assertIn("movea.l a3,a5", beams)
        self.assertIn("move.b  d3,(VOCAB_BEAM_LENGTH,a4)", beams)
        self.assertIn("#VOCAB_DEVICE_STATE_DISABLED", beams)

    def test_beam_contact_is_finite_directional_and_tolerance_inclusive(
        self,
    ) -> None:
        contact = _routine(self.combat, "VocabularyActorOnBeam")

        self.assertIn("#VOCAB_BEAM_DIRECTION_UP,d2", contact)
        self.assertIn("bne.w   .outside", contact)
        self.assertEqual(contact.count("cmpi.w  #4,d2"), 3)
        self.assertGreaterEqual(contact.count("bhi.s   .outside"), 6)
        self.assertGreaterEqual(contact.count("bmi.s   .outside"), 4)

    def test_vocabulary_owns_exactly_one_motion_and_timer_update_per_pool(
        self,
    ) -> None:
        projectiles = _routine(
            self.combat, "VocabularyUpdateProjectilesAndDamage"
        )
        enemies = _routine(self.combat, "VocabularyUpdateEnemies")
        devices = _routine(self.interactions, "VocabularyUpdateDevices")

        self.assertEqual(
            projectiles.count("add.l   d0,(OBJECT_X,a2)"), 1
        )
        self.assertEqual(
            projectiles.count("add.l   d0,(OBJECT_Y,a2)"), 1
        )
        self.assertEqual(
            projectiles.count("subq.w  #1,(OBJECT_TIMER,a2)"), 1
        )
        self.assertNotIn("add.l   d0,(OBJECT_X,a2)", enemies)
        self.assertNotIn("add.l   d0,(OBJECT_Y,a2)", enemies)
        self.assertEqual(
            enemies.count(
                "bsr.w   GameplayCampaignMoveObjectAxisSeparated"
            ),
            1,
        )
        self.assertEqual(
            enemies.count("subq.w  #1,(OBJECT_TIMER,a2)"), 1
        )
        self.assertEqual(
            devices.count("subq.w  #1,(OBJECT_TIMER,a2)"), 2
        )
        self.assertIn(".timed:", devices)
        self.assertIn(".door:", devices)
        self.assertIn(
            "#VOCAB_CAMPAIGN_FLAG_ACTIVE|"
            "VOCAB_CAMPAIGN_FLAG_PROFILE_VALID",
            devices,
        )

    def test_timed_switch_uses_authored_v3_duration_with_bounded_fallback(
        self,
    ) -> None:
        interaction = _routine(
            self.interactions, "VocabularyResolveInteraction"
        )
        timed = interaction.split(".timed:", 1)[1].split(
            ".rotate:",
            1,
        )[0]
        self.assertIn("bsr.w   CampaignGetSemanticDuration", timed)
        self.assertIn("#VOCAB_TIMED_SWITCH_TICKS,d0", timed)
        self.assertIn("move.w  d0,(OBJECT_TIMER,a4)", timed)

    def test_standalone_translation_unit_remains_warning_free(self) -> None:
        assembler = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(assembler.is_file(), assembler)
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="m5-vocabulary-semantics-", dir=ROOT / "build"
        ) as temporary:
            output = Path(temporary) / "vocabulary.bin"
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
                    "tests/m5/vocabulary_standalone.asm",
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
