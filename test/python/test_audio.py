"""Exercise the real analyzer with generated, copyright-free recordings."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from analyze import _lyric_events, analyze_audio
from generate_chart import generate_chart
from timeline import Timeline


class AudioTests(unittest.TestCase):
    def test_silent_short_and_empty_inputs_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, samples in [("silent", np.zeros(22050 * 2)), ("short", np.ones(200)), ("empty", np.zeros(0))]:
                filename = Path(directory) / f"{name}.wav"
                sf.write(filename, samples, 22050)
                with self.assertRaises(ValueError):
                    analyze_audio(str(filename))

    def test_no_detected_beats_uses_a_valid_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "tone.wav"
            samples = np.arange(22050 * 2) / 22050
            sf.write(filename, 0.1 * np.sin(2 * np.pi * 440 * samples), 22050)
            with patch("analyze._detect_beats", return_value=(120, np.array([]))):
                analysis = analyze_audio(str(filename))
            self.assertEqual(analysis["tempo_map"], [{"tick": 0, "bpm": 120000}])
            self.assertEqual(analysis["quality"]["beat_coverage"], 0)
            self.assertTrue(analysis["difficulties"]["ExpertSingle"])
            self.assertIn("[EasySingle]", generate_chart(analysis, {"name": "Fixture"}))

    def test_known_click_events_survive_serialization_without_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "clicks.wav"
            sr = 22050
            times = np.arange(sr * 8) / sr
            samples = 0.1 * np.sin(2 * np.pi * 440 * times)
            samples += 0.6 * np.sin(2 * np.pi * 1000 * times) * (np.mod(times, 0.5) < 0.012)
            sf.write(filename, samples, sr)
            known_beats = np.arange(0.5, 8, 0.5)
            with patch("analyze._detect_beats", return_value=(120, known_beats)):
                analysis = analyze_audio(str(filename))
            chart = generate_chart(analysis, {"name": "Fixture"})
            timeline = Timeline(analysis["tempo_map"])
            ticks = {note["tick"] for note in analysis["difficulties"]["ExpertSingle"]}
            for beat in known_beats:
                expected = timeline.seconds_to_ticks(float(beat))
                self.assertIn(expected, ticks)
                self.assertLessEqual(abs(timeline.ticks_to_seconds(expected) - beat), 0.0011)
                self.assertIn(f"  {expected} = N ", chart)
            self.assertEqual(len(analysis["tempo_map"]), 1)

    def test_lyric_duration_mismatch_in_either_direction_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "lyrics.json"
            filename.write_text(json.dumps({"synced": True, "lrc_duration": 100, "events": [{"time": 1, "word": "Hi"}]}))
            for duration in [80, 120]:
                lyrics, estimated, warning = _lyric_events(str(filename), [], 120, duration)
                self.assertEqual(lyrics, [])
                self.assertFalse(estimated)
                self.assertIn("does not match", warning)


if __name__ == "__main__":
    unittest.main()
