"""Offline regression tests for analysis, lyrics and chart serialization."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from analyze import _section_rand, generate_all_difficulties, sync_lyrics_to_sections
from generate_chart import generate_chart
from lyrics import parse_lrc


class CoreTests(unittest.TestCase):
    def test_section_random_is_stable_across_processes(self):
        script = "from analyze import _section_rand; print(_section_rand('chorus', 3, 2))"
        values = [subprocess.check_output(
            [sys.executable, "-c", script], cwd=ROOT / "python",
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"},
        ) for seed in ("1", "2")]
        self.assertEqual(values[0], values[1])
        self.assertGreaterEqual(_section_rand("chorus", 0, 1), 0)
        self.assertLess(_section_rand("chorus", 0, 1), 1)

    def notes(self, ai=None):
        return generate_all_difficulties(
            [{"time": i / 2, "pitch_class": i % 12} for i in range(16)],
            [i / 2 for i in range(16)],
            [{"start": 0, "end": 8, "label": "verse"}],
            120, {i: i % 5 for i in range(12)}, ai, 8,
        )

    def test_invalid_ai_weights_cannot_crash_generation(self):
        for weights in ([0] * 5, [-1] * 5, [float("nan")] * 5, ["bad"] * 5, [1]):
            notes = self.notes({"fret_emphasis": weights, "sections": {}})
            self.assertTrue(notes["ExpertSingle"])

    def test_notes_are_unique_bounded_and_progressive(self):
        notes = self.notes()
        counts = []
        for name in ("EasySingle", "MediumSingle", "HardSingle", "ExpertSingle"):
            track = notes[name]
            pairs = [(n["tick"], n["fret"]) for n in track]
            self.assertEqual(pairs, sorted(set(pairs)))
            counts.append(len(track))
            for note in track:
                self.assertTrue(0 <= note["fret"] <= 4)
                self.assertTrue(0 <= note["tick"] < 7680)
                self.assertTrue(0 <= note["length"] <= 7680 - note["tick"])
            if name in ("EasySingle", "MediumSingle"):
                self.assertTrue(all(n["fret"] < 4 for n in track))
                self.assertEqual(len({n["tick"] for n in track}), len(track))
        self.assertEqual(counts, sorted(counts))

    def test_silence_style_does_not_create_notes(self):
        ai = {"sections": {"0": {"guitar_style": "silence", "energy": 1}}}
        self.assertTrue(all(not notes for notes in self.notes(ai).values()))

    def test_multi_timestamp_lrc_and_offset(self):
        events = parse_lrc("[offset:-500]\n[00:01.00][00:03.00]Hello\n[00:00.10]too early\n[00:99.0]invalid")
        self.assertEqual(events, [{"time": 0.5, "word": "Hello"}, {"time": 2.5, "word": "Hello"}])

    def test_plain_lyrics_advance_repeated_sections(self):
        sections = [{"start": 0, "end": 10, "label": "verse"},
                    {"start": 10, "end": 20, "label": "chorus"},
                    {"start": 20, "end": 30, "label": "verse"}]
        events = sync_lyrics_to_sections(
            [{"text": str(i), "section": "verse"} for i in range(6)],
            sections, 120, 30,
        )
        self.assertTrue(any(e["tick"] >= 19200 for e in events))
        self.assertEqual([e["word"] for e in events], [str(i) for i in range(6)])

    def analysis(self):
        return {"tempo": 120000, "duration_ms": 8000,
                "tempo_map": [{"tick": 0, "bpm": 120000}],
                "sections": [{"start": 0, "end": 8, "label": "verse"}],
                "section_events": [{"tick": 0, "name": "verse"}],
                "difficulties": self.notes(), "lyrics": [{"tick": 480, "word": 'Hi "there"\nnext'}]}

    def test_chart_has_exact_line_endings_all_tracks_and_safe_metadata(self):
        chart = generate_chart(self.analysis(), {"name": 'Song "quoted"\n[bad]', "artist": "Artist"})
        self.assertTrue(chart.startswith("﻿[Song]\r\n"))
        self.assertTrue(chart.endswith("\r\n"))
        self.assertNotIn("\n", chart.replace("\r\n", ""))
        self.assertNotIn('5760 = E "section intro"', chart)
        self.assertNotIn('Name = "Song "quoted"', chart)
        for name in ("EasySingle", "MediumSingle", "HardSingle", "ExpertSingle"):
            self.assertIn(f"[{name}]", chart)

    def test_chart_rejects_bad_notes(self):
        analysis = self.analysis()
        analysis["difficulties"]["ExpertSingle"] = [{"tick": -1, "fret": 5, "length": 0}]
        with self.assertRaises(ValueError):
            generate_chart(analysis, {"name": "Song"})


if __name__ == "__main__":
    unittest.main()
