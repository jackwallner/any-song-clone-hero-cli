#!/usr/bin/env python3
"""Generate Clone Hero .chart file from analysis data."""

import sys, json, os

def generate_chart(analysis_data, metadata):
    """Generate full .chart file content."""
    
    lines = []
    
    # [Song] header - only fields Clone Hero actually parses
    lines.append("[Song]")
    lines.append("{")
    lines.append(f'  Name = "{metadata.get("name", "Unknown")}"')
    lines.append('  Offset = 0')
    lines.append('  Resolution = 480')
    lines.append('  Player2 = bass')
    lines.append('  Difficulty = 0')
    lines.append('  PreviewStart = 0')
    lines.append('  PreviewEnd = 0')
    
    genre = metadata.get("genre", "rock")
    lines.append(f'  Genre = "{genre}"')
    lines.append('  MediaType = "cd"')
    
    # Use song.opus if exists, otherwise song.mp3
    lines.append(f'  MusicStream = "song.opus"')
    lines.append("}")
    
    # [SyncTrack] - Tempo map
    lines.append("[SyncTrack]")
    lines.append("{")
    tempo_map = analysis_data.get("tempo_map", [{"tick": 0, "bpm": 120000}])
    lines.append(f'  0 = TS 4')
    for entry in tempo_map:
        tick = entry["tick"]
        bpm = entry["bpm"]
        lines.append(f'  {tick} = B {bpm}')
    lines.append("}")
    
    # [Events] - Must be sorted by tick ascending
    events = []
    events.append((5760, 'E "crowd_noclap"'))
    events.append((5760, 'E "section intro"'))
    
    # Music start
    sections = analysis_data.get("sections", [])
    if sections:
        first_start = sections[0]["start"] * 480 * (tempo_map[0]["bpm"] / 1000.0) / 60.0
        music_start_tick = int(first_start)
        events.append((music_start_tick, 'E "music_start"'))
    
    for sec in analysis_data.get("section_events", []):
        name = sec["name"]
        tick = sec["tick"]
        events.append((tick, f'E "section {name}"'))
    
    # Lyrics as E "lyric <text>" + E "phrase_start" events
    lyrics = analysis_data.get("lyrics", [])
    if lyrics:
        prev_tick = None
        for i, lyric in enumerate(lyrics):
            tick = lyric["tick"]
            word = lyric["word"].replace('"', "'")
            # Check for phrase boundary (gap > 1 beat at current tempo)
            bpm = tempo_map[0]["bpm"] / 1000.0
            beat_ticks = 480 * 4  # 480 resolution, 4 ticks per beat = 1920
            is_new_phrase = prev_tick is None or (tick - prev_tick) > int(beat_ticks * 1.5)
            if is_new_phrase:
                events.append((tick, 'E "phrase_start"'))
            events.append((tick, f'E "lyric {word}"'))
            prev_tick = tick
    
    # Sort by tick
    events.sort(key=lambda x: x[0])
    
    lines.append("[Events]")
    lines.append("{")
    for tick, event_str in events:
        lines.append(f"  {tick} = {event_str}")
    lines.append("}")
    
    # [PART VOCALS] - Lyrics track for Clone Hero display
    lyrics = analysis_data.get("lyrics", [])
    if lyrics:
        lines.append("")
        lines.append("[PART VOCALS]")
        lines.append("{")
        prev_tick = None
        for i, lyric in enumerate(lyrics):
            tick = lyric["tick"]
            word = lyric["word"].replace('"', "'")
            # Vocal note: N 0 <length> (type 0 = talky/non-pitched)
            # Use a length that spans to the next lyric or 480 ticks
            length = 480
            if i + 1 < len(lyrics):
                next_tick = lyrics[i + 1]["tick"]
                length = max(120, next_tick - tick)
            lines.append(f"  {tick} = N 0 {length}")
            lines.append(f"  {tick} = E \"{word}\"")
        lines.append("}")
    
    # Note tracks - Expert, Hard, Medium, Easy
    difficulties = analysis_data.get("difficulties", {})
    diff_order = ["ExpertSingle", "HardSingle", "MediumSingle", "EasySingle"]
    
    for diff_name in diff_order:
        notes = difficulties.get(diff_name, [])
        if not notes:
            continue
        
        lines.append(f"[{diff_name}]")
        lines.append("{")
        for note in notes:
            tick = note["tick"]
            fret = note["fret"]
            length = note.get("length", 0)
            lines.append(f"  {tick} = N {fret} {length}")
        lines.append("}")
    
    chart_text = "\n".join(lines)
    # Clone Hero requires UTF-8 BOM + CRLF line endings
    chart_text = "\ufeff" + chart_text.replace("\n", "\r\n")
    return chart_text


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: generate_chart.py <analysis.json> <metadata.json>", file=sys.stderr)
        sys.exit(1)
    
    with open(sys.argv[1]) as f:
        analysis = json.load(f)
    with open(sys.argv[2]) as f:
        metadata = json.load(f)
    
    chart = generate_chart(analysis, metadata)
    print(chart)
