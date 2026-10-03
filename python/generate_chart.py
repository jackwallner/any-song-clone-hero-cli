#!/usr/bin/env python3
"""Validate analysis and serialize a UTF-8 BOM, CRLF Clone Hero chart."""

import json
import math
import re
import sys

from timeline import RESOLUTION, Timeline

DIFFICULTIES = ["ExpertSingle", "HardSingle", "MediumSingle", "EasySingle"]


def chart_text(value) -> str:
    return re.sub(r"\s+", " ", str(value).replace('"', "'").replace("\x00", " ")).strip()


def integer(value, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def validate_analysis(data: dict) -> tuple[Timeline, int]:
    duration_ms = data.get("duration_ms")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or not math.isfinite(duration_ms) or duration_ms <= 0:
        raise ValueError("Analysis must have a positive finite duration_ms")
    timeline = Timeline(data.get("tempo_map", []))
    end_tick = timeline.seconds_to_ticks(duration_ms / 1000)
    tracks = data.get("difficulties")
    if not isinstance(tracks, dict):
        raise ValueError("Analysis must contain difficulty tracks")
    counts = []
    for name in reversed(DIFFICULTIES):
        notes = tracks.get(name, [])
        if not isinstance(notes, list):
            raise ValueError(f"Invalid note track: {name}")
        pairs, ticks = [], []
        for note in notes:
            tick = integer(note.get("tick"), "Note tick")
            fret = integer(note.get("fret"), "Note fret")
            length = integer(note.get("length", 0), "Sustain length")
            if tick >= end_tick or tick + length > end_tick or fret > 4:
                raise ValueError("Note is outside the audio timeline or fret range")
            if name in {"EasySingle", "MediumSingle"} and fret == 4:
                raise ValueError("Easy and Medium must not contain orange frets")
            pairs.append((tick, fret))
            ticks.append(tick)
        if pairs != sorted(set(pairs)):
            raise ValueError("Notes must be sorted and unique")
        if name in {"EasySingle", "MediumSingle"} and len(set(ticks)) != len(ticks):
            raise ValueError("Easy and Medium must use single notes")
        by_fret = {}
        for note in notes:
            fret = note["fret"]
            if by_fret.get(fret, 0) > note["tick"]:
                raise ValueError("Sustain overlaps the next note on the same fret")
            by_fret[fret] = note["tick"] + note.get("length", 0)
        counts.append(len(notes))
    if counts != sorted(counts):
        raise ValueError("Note counts must increase from Easy to Expert")
    return timeline, end_tick


def generate_chart(analysis_data: dict, metadata: dict) -> str:
    timeline, end_tick = validate_analysis(analysis_data)
    lines = ["[Song]", "{", f'  Name = "{chart_text(metadata.get("name", "Unknown"))}"',
             f'  Artist = "{chart_text(metadata.get("artist", "Unknown"))}"',
             "  Offset = 0", f"  Resolution = {RESOLUTION}", "  Player2 = bass", "  Difficulty = 0",
             "  PreviewStart = 0", "  PreviewEnd = 0", f'  Genre = "{chart_text(metadata.get("genre", "rock"))}"',
             '  MediaType = "cd"', '  MusicStream = "song.opus"', "}", "[SyncTrack]", "{", "  0 = TS 4"]
    lines.extend(f'  {entry["tick"]} = B {entry["bpm"]}' for entry in timeline.entries)
    lines.append("}")
    events = []
    sections = analysis_data.get("sections", [])
    start = timeline.seconds_to_ticks(sections[0].get("start", 0)) if sections else 0
    if start >= end_tick:
        raise ValueError("Music start lies beyond the audio")
    events.append((start, 'E "music_start"'))
    for section in analysis_data.get("section_events", []):
        tick = integer(section.get("tick"), "Section tick")
        if tick >= end_tick:
            raise ValueError("Section lies beyond the audio")
        events.append((tick, f'E "section {chart_text(section.get("name", "section"))}"'))
    lyrics_by_tick = {}
    for lyric in analysis_data.get("lyrics", []):
        tick = integer(lyric.get("tick"), "Lyric tick")
        if tick >= end_tick:
            raise ValueError("Lyric lies beyond the audio")
        word = chart_text(lyric.get("word", ""))
        if word:
            lyrics_by_tick.setdefault(tick, []).append(word)
    lyrics = [(tick, " ".join(words)) for tick, words in sorted(lyrics_by_tick.items())]
    vocal_notes = []
    previous_end = None
    for index, (tick, word) in enumerate(lyrics):
        next_tick = lyrics[index + 1][0] if index + 1 < len(lyrics) else end_tick
        length = min(RESOLUTION, next_tick - tick, end_tick - tick)
        if previous_end is None or tick - previous_end > RESOLUTION:
            if previous_end is not None:
                events.append((previous_end, 'E "phrase_end"'))
            events.append((tick, 'E "phrase_start"'))
        events.append((tick, f'E "lyric {word}"'))
        vocal_notes.append((tick, length, word))
        previous_end = tick + length
    if previous_end is not None:
        events.append((previous_end, 'E "phrase_end"'))
    events.append((end_tick, 'E "end"'))
    lines.extend(["[Events]", "{"])
    lines.extend(f"  {tick} = {event}" for tick, event in sorted(events, key=lambda item: item[0]))
    lines.append("}")
    if vocal_notes:
        lines.extend(["[PART VOCALS]", "{"])
        for tick, length, word in vocal_notes:
            lines.extend([f"  {tick} = N 0 {length}", f'  {tick} = E "{word}"'])
        lines.append("}")
    for name in DIFFICULTIES:
        lines.extend([f"[{name}]", "{"])
        lines.extend(f'  {note["tick"]} = N {note["fret"]} {note.get("length", 0)}' for note in analysis_data["difficulties"].get(name, []))
        lines.append("}")
    return "﻿" + "\r\n".join(lines) + "\r\n"


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("Usage: generate_chart.py <analysis.json> <metadata.json>")
        with open(sys.argv[1], encoding="utf-8") as source:
            analysis = json.load(source)
        with open(sys.argv[2], encoding="utf-8") as source:
            metadata = json.load(source)
        sys.stdout.buffer.write(generate_chart(analysis, metadata).encode("utf-8"))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
