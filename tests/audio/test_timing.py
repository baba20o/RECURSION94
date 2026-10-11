from __future__ import annotations

import unittest

from tools.audio_reference.timing import MAX_TICKS_PER_VINT, TempoClock


class TempoClockTests(unittest.TestCase):
    def test_all_sector_tempos_produce_exact_twelve_second_ticks(self) -> None:
        expected = {80: 384, 100: 480, 120: 576, 140: 672, 160: 768}
        for refresh_hz in (50, 60):
            for bpm, ticks in expected.items():
                with self.subTest(refresh_hz=refresh_hz, bpm=bpm):
                    clock = TempoClock(bpm, refresh_hz)
                    self.assertEqual(ticks, clock.advance_seconds(12))
                    self.assertEqual(0, clock.accumulator)

    def test_twelve_second_ticks_equal_exact_four_beat_bar_counts(self) -> None:
        expected_bars = {80: 4, 100: 5, 120: 6, 140: 7, 160: 8}
        for bpm, bars in expected_bars.items():
            clock = TempoClock(bpm, 60)
            clock.advance_seconds(12)
            self.assertEqual(bars, clock.total_ticks // (24 * 4))

    def test_pal_and_ntsc_finish_with_identical_musical_phase(self) -> None:
        for bpm in (80, 100, 120, 140, 160):
            ntsc = TempoClock(bpm, 60)
            pal = TempoClock(bpm, 50)
            ntsc.advance_seconds(12)
            pal.advance_seconds(12)
            self.assertEqual(ntsc.total_ticks, pal.total_ticks)
            self.assertEqual(ntsc.accumulator, pal.accumulator)
            self.assertEqual(720, ntsc.vint_count)
            self.assertEqual(600, pal.vint_count)

    def test_worst_case_never_exceeds_two_ticks_per_vint(self) -> None:
        for refresh_hz in (50, 60):
            clock = TempoClock(160, refresh_hz)
            schedule = clock.schedule(refresh_hz * 60)
            self.assertLessEqual(
                max(ticks for _vint, ticks in schedule),
                MAX_TICKS_PER_VINT,
            )
            self.assertIn(2, {ticks for _vint, ticks in schedule})

    def test_schedule_uses_absolute_one_based_vint_numbers(self) -> None:
        clock = TempoClock(120, 60)
        first = clock.schedule(5)
        second = clock.schedule(5)
        self.assertTrue(all(vint >= 1 for vint, _ticks in first))
        self.assertTrue(all(vint > 5 for vint, _ticks in second))

    def test_schedule_is_repeatable(self) -> None:
        left = TempoClock(140, 50).schedule(500)
        right = TempoClock(140, 50).schedule(500)
        self.assertEqual(left, right)

    def test_invalid_refresh_rate_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "50 or 60"):
            TempoClock(120, 59)

    def test_bool_and_out_of_budget_bpm_rejected(self) -> None:
        for bpm in (True, 0, 161):
            with self.subTest(bpm=bpm):
                with self.assertRaises(ValueError):
                    TempoClock(bpm, 60)

    def test_non_24_ppqn_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "24 PPQN"):
            TempoClock(120, 60, ppqn=48)

    def test_negative_advance_rejected(self) -> None:
        clock = TempoClock(80, 60)
        with self.assertRaises(ValueError):
            clock.advance_vints(-1)
        with self.assertRaises(ValueError):
            clock.advance_seconds(-1)


if __name__ == "__main__":
    unittest.main()
