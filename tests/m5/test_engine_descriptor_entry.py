from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENGINE_CORE = ROOT / "src/game/engine_core.asm"


def _routine(source: str, label: str, next_label: str) -> str:
    return source.split(f"\n{label}:", 1)[1].split(
        f"\n{next_label}:",
        1,
    )[0]


def _ordered_positions(source: str, fragments: tuple[str, ...]) -> list[int]:
    positions: list[int] = []
    start = 0
    for fragment in fragments:
        position = source.index(fragment, start)
        positions.append(position)
        start = position + len(fragment)
    return positions


class EngineDescriptorEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ENGINE_CORE.read_text(encoding="utf-8")
        cls.legacy = _routine(
            cls.source,
            "EngineLoadEncounter",
            "EngineLoadDescriptor",
        )
        cls.descriptor = _routine(
            cls.source,
            "EngineLoadDescriptor",
            "EngineReconstructCanonical",
        )

    def test_legacy_lookup_preserves_not_found_and_tail_delegates_on_match(
        self,
    ) -> None:
        required_lookup_order = (
            "move.w  d0,d3",
            "lea     (EncounterDescriptorTable,pc),a2",
            "move.w  #ENCOUNTER_DESCRIPTOR_COUNT-1,d4",
            "movea.l (a2)+,a0",
            "cmp.w   (ENCOUNTER_DESCRIPTOR_ID,a0),d3",
            "dbf     d4,.find",
            (
                "move.w  #ENGINE_ERROR_ENCOUNTER_NOT_FOUND,"
                "(ENGINE_LAST_ERROR).l"
            ),
            "moveq   #ENGINE_RESULT_NOT_FOUND,d0",
        )
        positions = _ordered_positions(self.legacy, required_lookup_order)
        self.assertEqual(positions, sorted(positions))

        found = self.legacy.split("\n.found:", 1)[1].split("\n.done:", 1)[0]
        self.assertIn("move.w  d3,d0", found)
        self.assertIn("movem.l (a7)+,d3-d4/a2", found)
        self.assertIn("bra.w   EngineLoadDescriptor", found)
        self.assertLess(
            found.index("movem.l (a7)+,d3-d4/a2"),
            found.index("bra.w   EngineLoadDescriptor"),
        )
        self.assertNotIn("EngineReconstructCanonical", self.legacy)
        self.assertNotIn("ENCOUNTER_DESCRIPTOR_VERSION_VALUE", self.legacy)

    def test_public_entry_rejects_null_odd_and_mismatched_descriptors(
        self,
    ) -> None:
        self.assertIn(
            "; In: a0=non-null even descriptor pointer, d0.w=encounter ID.",
            self.source,
        )
        required_guard_order = (
            "move.w  d0,d3",
            "movea.l a0,a3",
            "move.l  a0,d1",
            "beq.w   .bad_descriptor",
            "btst    #0,d1",
            "bne.w   .bad_descriptor",
            "cmp.w   (ENCOUNTER_DESCRIPTOR_ID,a3),d3",
            "bne.w   .bad_descriptor",
            (
                "cmpi.w  #ENCOUNTER_DESCRIPTOR_VERSION_VALUE,"
                "(ENCOUNTER_DESCRIPTOR_VERSION,a3)"
            ),
        )
        positions = _ordered_positions(self.descriptor, required_guard_order)
        self.assertEqual(positions, sorted(positions))

        first_descriptor_read = self.descriptor.index(
            "(ENCOUNTER_DESCRIPTOR_ID,a3)"
        )
        self.assertLess(
            self.descriptor.index("beq.w   .bad_descriptor"),
            first_descriptor_read,
        )
        self.assertLess(
            self.descriptor.index("bne.w   .bad_descriptor"),
            first_descriptor_read,
        )

    def test_public_entry_owns_validation_staging_commit_and_reconstruction(
        self,
    ) -> None:
        required_pipeline_order = (
            "cmpi.w  #ENCOUNTER_DESCRIPTOR_SIZE",
            "lea     (ENGINE_STAGING_BASE).l,a0",
            "lea     (CANONICAL_DESCRIPTOR).l,a1",
            "lea     (CANONICAL_RECTS).l,a5",
            "lea     (CANONICAL_OBJECTS).l,a5",
            "move.w  d3,(ENGINE_ENCOUNTER_ID).l",
            "bsr.w   EngineReconstructCanonical",
        )
        positions = _ordered_positions(self.descriptor, required_pipeline_order)
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("EncounterDescriptorTable", self.descriptor)
        self.assertIn(
            "movem.l d3-d7/a2-a6,-(a7)",
            self.descriptor,
        )
        self.assertIn(
            "movem.l (a7)+,d3-d7/a2-a6",
            self.descriptor,
        )

    def test_all_descriptor_guard_failures_share_the_existing_error_exit(
        self,
    ) -> None:
        error = self.descriptor.split("\n.bad_descriptor:", 1)[1].split(
            "\n.done:",
            1,
        )[0]
        required_error_state = (
            "move.w  #ENGINE_STATE_ERROR,(ENGINE_STATE).l",
            "andi.w  #$FFFC,(ENGINE_FLAGS).l",
            "clr.w   (ENGINE_PROJECTION_LENGTH).l",
            (
                "move.w  #ENGINE_ERROR_BAD_DESCRIPTOR,"
                "(ENGINE_LAST_ERROR).l"
            ),
            "moveq   #ENGINE_RESULT_BAD_DESCRIPTOR,d0",
        )
        positions = _ordered_positions(error, required_error_state)
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("EngineReconstructCanonical", error)


if __name__ == "__main__":
    unittest.main()
