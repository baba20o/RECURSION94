from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.m3_reference.contracts import (
    ContractError,
    EXPECTED_CONSTANTS,
    EXPECTED_ENCOUNTER_SUMMARY,
    RUNTIME_SOURCE_SHA256,
    load_engine_contract,
)


ROOT = Path(__file__).resolve().parents[2]


class AssemblyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_engine_contract(
            ROOT, verify_source_hashes=False
        )

    def test_current_semantic_inputs_are_not_relabelled_frozen_m3(self) -> None:
        self.assertNotEqual(
            dict(self.contract.source_sha256),
            RUNTIME_SOURCE_SHA256,
        )
        with self.assertRaisesRegex(ContractError, "runtime source drift"):
            load_engine_contract(ROOT)

    def test_all_model_constants_match_assembly(self) -> None:
        for name, expected in EXPECTED_CONSTANTS.items():
            with self.subTest(name=name):
                self.assertEqual(self.contract.constants[name], expected)
        projection_bytes = (
            self.contract.constants["PROJECTION_HEADER_BYTES"]
            + self.contract.constants["ACTOR_CAPACITY"]
            * self.contract.constants["ACTOR_PROJECTION_BYTES"]
            + self.contract.constants["POOL_TYPE_COUNT"]
            * self.contract.constants["PROJECTION_POOL_HEADER_BYTES"]
            + self.contract.constants["TOTAL_POOL_CAPACITY"]
            * self.contract.constants["OBJECT_PROJECTION_BYTES"]
        )
        self.assertEqual(projection_bytes, 3638)

    def test_fixture_bytes_parse_to_expected_semantics(self) -> None:
        self.assertEqual(self.contract.encounter_order, (0, 1))
        for encounter_id, expected in EXPECTED_ENCOUNTER_SUMMARY.items():
            encounter = self.contract.encounters[encounter_id]
            actual = {
                "room": (
                    encounter.room_left,
                    encounter.room_top,
                    encounter.room_right,
                    encounter.room_bottom,
                ),
                "spawn": (encounter.spawn_x, encounter.spawn_y),
                "rects": tuple(
                    (rect.x, rect.y, rect.width, rect.height)
                    for rect in encounter.rects
                ),
                "objects": tuple(
                    (
                        item.pool_type,
                        item.logical_id,
                        item.state,
                        item.x,
                        item.y,
                        item.param_0,
                        item.param_1,
                    )
                    for item in encounter.objects
                ),
            }
            self.assertEqual(actual, expected)

    def _copy_runtime(self, destination: Path) -> None:
        (destination / "src").mkdir(parents=True)
        shutil.copytree(ROOT / "src/game", destination / "src/game")

    def test_numeric_drift_fails_closed_without_hash_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            layout = root / "src/game/engine_layout.asm"
            text = layout.read_text(encoding="utf-8")
            self.assertIn("ENGINE_ATTEMPT_TICKS            equ     720", text)
            layout.write_text(
                text.replace(
                    "ENGINE_ATTEMPT_TICKS            equ     720",
                    "ENGINE_ATTEMPT_TICKS            equ     721",
                ),
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(ContractError, "constant drift"):
                load_engine_contract(root, verify_source_hashes=False)

    def test_fixture_drift_fails_closed_without_hash_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            encounters = root / "src/game/encounters.asm"
            text = encounters.read_text(encoding="utf-8")
            self.assertIn("dc.w    112,48,16,128", text)
            encounters.write_text(
                text.replace("dc.w    112,48,16,128", "dc.w    113,48,16,128"),
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(ContractError, "semantic fixture drift"):
                load_engine_contract(root, verify_source_hashes=False)

    def test_frozen_source_hash_guard_rejects_later_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            with self.assertRaisesRegex(ContractError, "runtime source drift"):
                load_engine_contract(root)

    def test_inactive_object_projection_zero_record_invariant_is_explicit(self) -> None:
        projection = (ROOT / "src/game/projection.asm").read_text(
            encoding="utf-8"
        )
        pools = (ROOT / "src/game/object_pool.asm").read_text(
            encoding="utf-8"
        )
        publication = projection[
            projection.index("EngineBuildProjection:") :
            projection.index("Crc32ProjectionIsoHdlc:")
        ]
        self.assertNotIn(
            "bsr.w   Crc32ProjectionIsoHdlc",
            publication,
        )
        self.assertEqual(
            projection.count("clr.l   (a1)+"),
            6,
        )
        self.assertNotIn("movem.l d0-d2/d4-d6,(a1)", projection)
        self.assertIn("tst.b   (a1)", projection)
        self.assertIn("bsr.w   Crc32AdvanceZeroRecords", projection)
        self.assertIn("move.l  d6,d2", projection)
        self.assertIn(
            'assert  OBJECT_BYTES=OBJECT_PROJECTION_BYTES,'
            '"inactive object projection size drift"',
            projection,
        )
        self.assertEqual(
            pools.count("moveq   #(OBJECT_BYTES/4)-1"),
            2,
        )
        self.assertGreaterEqual(
            pools.count("move.l  d0,(a1)+"),
            2,
        )
        self.assertEqual(
            self.contract.constants["OBJECT_BYTES"],
            self.contract.constants["OBJECT_PROJECTION_BYTES"],
        )
        self.assertEqual(self.contract.constants["OBJECT_BYTES"], 24)

    def test_t16_word_recurrence_and_unaligned_prefix_are_exact(self) -> None:
        crc = (ROOT / "src/game/crc32.asm").read_text(encoding="utf-8")
        word = crc[
            crc.index(".next_word:") :
            crc.index(".tail_ready:")
        ]
        required_order = (
            "move.w  (a0)+,d2",
            "ror.w   #8,d2",
            "eor.w   d1,d2",
            "lsl.l   #2,d2",
            "move.l  (a1,d2.l),d2",
            "clr.w   d1",
            "swap    d1",
            "eor.l   d2,d1",
        )
        positions = [word.index(fragment) for fragment in required_order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("btst    #0,d2", crc)
        self.assertIn("lea     (Crc32ByteTable).l,a1", crc)
        self.assertIn("lea     (Crc32T16Table).l,a1", crc)
        self.assertLess(
            crc.index("lea     (Crc32T16Table).l,a1"),
            crc.index(".next_word:"),
        )
        self.assertNotIn("adda.l  d2,a1", word)
        self.assertNotIn("(a1,d2.w)", word)

    def test_common_composite_zero_runs_dispatch_before_power_fallback(self) -> None:
        projection = (ROOT / "src/game/projection.asm").read_text(
            encoding="utf-8"
        )
        advance = projection[
            projection.index("Crc32AdvanceZeroRecords:") :
            projection.index("; Apply one generated four-plane linear transition.")
        ]
        fallback = advance.index("move.l  d3,-(a7)")
        for records in (7, 12, 15, 24, 31):
            compare = f"cmpi.w  #{records},d0"
            label = f".direct_zero_records_{records}:"
            table = f"lea     (Crc32ZeroRecords{records}Table).l,a1"
            self.assertIn(compare, advance)
            self.assertIn(label, advance)
            self.assertIn(table, advance)
            self.assertLess(advance.index(compare), fallback)
            self.assertGreater(advance.index(label), fallback)
        for records in (1, 2, 4, 8, 16, 32):
            self.assertIn(
                f"lea     (Crc32ZeroRecords{records}Table).l,a1",
                advance,
            )
        self.assertEqual(advance.count("bra.w   .apply_zero_transition"), 5)


if __name__ == "__main__":
    unittest.main()
