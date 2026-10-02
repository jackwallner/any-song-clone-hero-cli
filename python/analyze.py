#!/usr/bin/env python3
"""AI-powered audio analysis for Clone Hero chart generation.
Uses librosa for beat/onset detection + Gemini for intelligent note mapping."""

import sys, json, warnings, os, math, re
import numpy as np
warnings.filterwarnings("ignore")

import librosa
from scipy.signal import find_peaks, butter, sosfilt

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
RESOLUTION = 480  # ticks per beat

def to_float(x):
    """Coerce a librosa scalar-or-array result to a Python float.
    librosa >= 0.10 returns tempo as a numpy array, and numpy >= 2 refuses
    float() on any array with ndim > 0."""
    arr = np.asarray(x, dtype=float).ravel()
    return float(arr[0]) if arr.size else 0.0

def time_to_tick(t, tempo, offset=0):
    """Convert time in seconds to chart ticks at current tempo."""
    return int(round((t - offset) * RESOLUTION * tempo / 60.0))

def ticks_to_time(tick, tempo):
    """Convert ticks to seconds."""
    return tick * 60.0 / (RESOLUTION * tempo)

def sync_lyrics_to_sections(lyrics_lines, sections, tempo, duration):
    """Distribute lyric lines across detected song sections by time."""
    if not lyrics_lines or not sections:
        return []

    events = []
    section_index = 0
    total_lines = len(lyrics_lines)

    for i, line_data in enumerate(lyrics_lines):
        text = line_data.get("text", "")
        lyric_section = line_data.get("section", "verse")

        # Find matching section or default to current
        target_sec = None
        for j in range(section_index, len(sections)):
            if sections[j].get("label", "") == lyric_section:
                target_sec = sections[j]
                section_index = j
                break

        if not target_sec and section_index < len(sections):
            # Use current section if no match found
            target_sec = sections[section_index]

        if not target_sec:
            # Fallback: evenly distribute across duration
            time_pos = (i / max(total_lines - 1, 1)) * duration
        else:
            sec_start = target_sec["start"]
            sec_end = target_sec["end"]
            sec_duration = sec_end - sec_start

            # Distribute lines within the section
            lines_in_section = sum(
                1 for l in lyrics_lines
                if l.get("section", "") == lyric_section
            )
            if lines_in_section == 0:
                lines_in_section = 1

            # Find which line this is within its section type
            line_idx_in_section = sum(
                1 for j in range(i + 1)
                if lyrics_lines[j].get("section", "") == lyric_section
            ) - 1

            fraction = min(line_idx_in_section / max(lines_in_section, 1), 0.95)
            time_pos = sec_start + (fraction * sec_duration)

        tick = time_to_tick(time_pos, tempo)
        word = text.replace('"', "'")
        events.append({"tick": tick, "word": word})

    events.sort(key=lambda x: x["tick"])
    return events


def analyze_audio(filepath, gemini_key=None, metadata=None, lyrics_file=None):
    """Full audio analysis pipeline."""
    
    print("Loading audio...", file=sys.stderr)
    y, sr = librosa.load(filepath, sr=22050, mono=True)
    duration = len(y) / sr
    
    # Separate harmonic/percussive
    y_harm, y_perc = librosa.effects.hpss(y)
    
    # Beat tracking — use Spotify tempo if available, otherwise detect from audio
    spotify_tempo = float(os.environ.get("SPOTIFY_TEMPO", 0) or 0)
    if spotify_tempo > 0:
        print(f"  Using Spotify tempo: {spotify_tempo:.1f} BPM", file=sys.stderr)
        tempo = spotify_tempo
        beat_frames = librosa.beat.beat_track(y=y_perc, sr=sr, bpm=tempo, units='frames')[1]
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    else:
        print("Detecting beats...", file=sys.stderr)
        tempo, beat_frames = librosa.beat.beat_track(y=y_perc, sr=sr, units='frames')
        tempo = to_float(tempo)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    
    # Onset detection
    print("Detecting onsets...", file=sys.stderr)
    onset_env = librosa.onset.onset_strength(y=y_perc, sr=sr)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, 
                                               backtrack=True, units='frames')
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    
    # Quality gate: octave error detection using tempogram (skip if using Spotify tempo)
    if spotify_tempo <= 0:
        onset_env_full = librosa.onset.onset_strength(y=y_perc, sr=sr, hop_length=512)
        tempogram = librosa.feature.tempogram(onset_envelope=onset_env_full, sr=sr, hop_length=512)
        tg_mean = np.mean(tempogram, axis=1)
        tempo_freqs = librosa.tempo_frequencies(len(tg_mean), sr=sr, hop_length=512)
        
        from scipy.signal import find_peaks
        peaks, props = find_peaks(tg_mean, height=0.2*np.max(tg_mean), distance=3)
        peak_tempos = sorted([(float(tempo_freqs[p]), float(tg_mean[p])) for p in peaks], 
                             key=lambda x: x[1], reverse=True)
        
        if len(peak_tempos) >= 2:
            t1, a1 = peak_tempos[0]
            t2, a2 = peak_tempos[1]
            ratio = max(t1, t2) / min(t1, t2)
            if 1.8 <= ratio <= 2.2 and a2 > a1 * 0.6:
                # Pick the tempo closest to realistic range (most songs 70-200 BPM, center ~120)
                t1_dist = abs(t1 - 120)
                t2_dist = abs(t2 - 120)
                chosen = t1 if t1_dist < t2_dist else t2
                other = t2 if chosen == t1 else t1
                print(f"  ⚠ Octave ambiguity: {t1:.0f}BPM vs {t2:.0f}BPM (strength {a1:.2f}/{a2:.2f}) — using {chosen:.0f}BPM (closer to 120)", file=sys.stderr)
                tempo = chosen
                beat_frames = librosa.beat.beat_track(y=y_perc, sr=sr, bpm=tempo, units='frames')[1]
                beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    
    # Quality gate: beat coverage check — retry tracking on untracked tail
    beat_coverage = beat_times[-1] / duration
    if beat_coverage < 0.85:
        tail_start_s = beat_times[-1] - 2.0  # small overlap
        if tail_start_s > 0 and (duration - tail_start_s) > 10:
            tail_start_sample = int(tail_start_s * sr)
            y_tail_perc = librosa.effects.hpss(y[tail_start_sample:])[1]
            tail_tempo, tail_frames = librosa.beat.beat_track(y=y_tail_perc, sr=sr, units='frames')
            tail_times = librosa.frames_to_time(tail_frames, sr=sr) + tail_start_s
            # Merge, skipping beats that overlap with existing
            existing_last = beat_times[-1] if len(beat_times) > 0 else 0
            new_tail = [t for t in tail_times if t > existing_last + 0.1]
            if new_tail:
                beat_times = np.concatenate([beat_times, new_tail])
                print(f"  ↳ Re-tracked tail: {to_float(tail_tempo):.0f} BPM, {len(new_tail)} beats recovered ({new_tail[0]:.1f}s → {new_tail[-1]:.1f}s)", file=sys.stderr)
        
        beat_coverage = beat_times[-1] / duration
    
    if beat_coverage < 0.85:
        print(f"  ⚠ Beat tracking covers only {beat_coverage*100:.0f}% of audio — last {duration - beat_times[-1]:.1f}s untracked", file=sys.stderr)
    
    # Chroma for pitch analysis
    print("Analyzing pitch...", file=sys.stderr)
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr, hop_length=512)
    chroma_frames = librosa.feature.chroma_cens(y=y_harm, sr=sr, hop_length=512)
    
    # Estimate key — use Spotify key if available
    spotify_key = int(os.environ.get("SPOTIFY_KEY", -1) or -1)
    if spotify_key >= 0 and spotify_key <= 11:
        estimated_key = spotify_key
        print(f"  Using Spotify key: {estimated_key}", file=sys.stderr)
    else:
        chroma_mean = chroma.mean(axis=1)
        estimated_key = int(np.argmax(chroma_mean))
    
    # Get spectral features for section detection
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rms = librosa.feature.rms(y=y)[0]
    
    # Section detection based on energy/spectral changes
    sections = detect_sections(y, sr, rms, spectral_centroid, beat_times, onset_times)
    
    # Quality gate: section sanity checks
    if sections:
        # First section should not be chorus/quiet (should be intro/verse)
        if sections[0].get('label') in ('chorus', 'quiet'):
            print(f"  ⚠ First section labeled '{sections[0]['label']}' — expected intro/verse. Overriding to 'intro'.", file=sys.stderr)
            sections[0]['label'] = 'intro'
        
        # Last section should be outro/ending
        if sections[-1].get('label') not in ('outro', 'ending'):
            present_labels = set(s.get('label') for s in sections)
            if 'outro' not in present_labels:
                sections[-1]['label'] = 'outro'
        
        # Flag sections that are too long (> 90s)
        for i, sec in enumerate(sections):
            dur = sec['end'] - sec['start']
            if dur > 90:
                print(f"  ⚠ Section {i} too long ({dur:.0f}s) with label '{sec['label']}' — possible detection failure", file=sys.stderr)
        
        # Flag if too few sections for song length
        if len(sections) <= 3 and duration > 90:
            print(f"  ⚠ Only {len(sections)} sections detected for {duration:.0f}s song — likely detection failure", file=sys.stderr)
    
    # Map onsets to frets using chroma (compact: only time + pitch_class)
    onset_notes = []
    
    hop_len = 512
    for onset_time in onset_times:
        frame = int(onset_time * sr / hop_len)
        if frame < chroma.shape[1]:
            chroma_vec = chroma[:, frame]
            dominant = int(np.argmax(chroma_vec))
            onset_notes.append({
                "time": float(onset_time),
                "pitch_class": dominant,
            })
    
    # Extract per-section audio features for Gemini musical analysis
    section_features = extract_section_features(
        sections, onset_notes, onset_env, rms, spectral_centroid, 
        beat_times, sr, hop_len, duration, tempo
    )
    
    # Use Gemini to enhance the analysis with musical judgment
    ai_suggestions = None
    lyrics = None
    
    # Load real lyrics from file if provided
    lyrics_scaled = False
    lyrics_offset = 0.0
    if lyrics_file and os.path.exists(lyrics_file):
        try:
            with open(lyrics_file) as f:
                lyrics_data = json.load(f)

            if lyrics_data.get("synced") and lyrics_data.get("events"):
                lrc_events = lyrics_data["events"]
                lrc_duration = lyrics_data.get("lrc_duration", 0)
                
                # Detect timing mismatch: compare audio duration to LRCLIB reference
                if lrc_duration > 0 and duration > 0:
                    dur_ratio = duration / lrc_duration
                    if dur_ratio > 1.08:
                        # Audio is significantly longer than LRCLIB reference (e.g., music video with intro)
                        print(f"  ⚠ Lyrics mismatch: audio={duration:.0f}s vs LRCLIB={lrc_duration:.0f}s (ratio {dur_ratio:.2f}) — dropping lyrics", file=sys.stderr)
                        lyrics = []
                        lyrics_offset = duration - lrc_duration
                    else:
                        lyrics = []
                        for ev in lrc_events:
                            t = ev["time"]
                            tick = time_to_tick(t, tempo)
                            word = ev["word"].replace('"', "'")
                            lyrics.append({"tick": tick, "word": word})
                        lyrics.sort(key=lambda x: x["tick"])
                        print(f"  Lyrics synced: {len(lyrics)} events from LRCLIB", file=sys.stderr)
                else:
                    lyrics = []
                    for ev in lrc_events:
                        t = ev["time"]
                        tick = time_to_tick(t, tempo)
                        word = ev["word"].replace('"', "'")
                        lyrics.append({"tick": tick, "word": word})
                    lyrics.sort(key=lambda x: x["tick"])
                    print(f"  Lyrics synced: {len(lyrics)} events from LRCLIB", file=sys.stderr)

            elif lyrics_data.get("lines"):
                # Plain text lyrics — distribute across sections
                print(
                    f"  Syncing {len(lyrics_data['lines'])} plain lyric lines to sections...",
                    file=sys.stderr,
                )
                lyrics = sync_lyrics_to_sections(
                    lyrics_data["lines"], sections, tempo, duration
                )
                print(f"  Lyrics synced: {len(lyrics)} events (distributed)", file=sys.stderr)

        except Exception as e:
            print(f"  Lyrics sync failed (continuing without): {e}", file=sys.stderr)
    
    if gemini_key:
        try:
            ai_suggestions = get_gemini_analysis(gemini_key, tempo, estimated_key, 
                                                  sections, duration, onset_notes,
                                                  section_features,
                                                  metadata.get("name", ""), metadata.get("artist", ""))
        except Exception as e:
            print(f"  Gemini analysis failed (continuing without): {e}", file=sys.stderr)
        
        # Lyrics come ONLY from real sources (LRCLIB). Never from AI.
    
    # Map to frets (uses AI fret_mapping if available)
    fret_map = build_fret_map(estimated_key, ai_suggestions)
    
    # Generate notes for all difficulties (uses AI difficulty_params if available)
    difficulties = generate_all_difficulties(onset_notes, beat_times, sections, 
                                              tempo, fret_map, ai_suggestions, duration)
    
    # Bass track: detect low-frequency onsets and generate bass difficulties
    print("Detecting bass onsets...", file=sys.stderr)
    bass_onsets = detect_bass_onsets(y_harm, sr)
    print(f"  Bass onsets: {len(bass_onsets)} (of {len(onset_notes)} total)", file=sys.stderr)
    bass_difficulties = generate_bass_difficulties(
        onset_notes, bass_onsets, beat_times, sections,
        tempo, fret_map, ai_suggestions, duration
    )
    difficulties.update(bass_difficulties)
    
    # Build tempo map from actual beat intervals
    tempo_map = []
    if len(beat_times) >= 2:
        last_tick = 0
        last_bpm = None
        for i in range(len(beat_times) - 1):
            interval = beat_times[i+1] - beat_times[i]
            if interval <= 0:
                continue
            local_bpm = round(60.0 / interval)
            tick = time_to_tick(beat_times[i], tempo)
            if last_bpm is None or abs(local_bpm - last_bpm) > 1:
                tempo_map.append({"tick": tick, "bpm": local_bpm * 1000})
                last_bpm = local_bpm
    if not tempo_map:
        tempo_map = [{"tick": 0, "bpm": round(tempo * 1000)}]
    
    # Build sections for events
    section_events = []
    for sec in sections:
        tick = time_to_tick(sec["start"], tempo)
        section_events.append({"tick": tick, "name": sec.get("label", "section")})
    
    return {
        "tempo": round(tempo * 1000),
        "tempo_map": tempo_map,
        "key": estimated_key,
        "duration_ms": int(duration * 1000),
        "sections": sections,
        "section_events": section_events,
        "difficulties": difficulties,
        "beat_times": [float(t) for t in beat_times],
        "onset_count": len(onset_notes),
        "bass_onset_count": len(bass_onsets),
        "ai_enhanced": ai_suggestions is not None,
        "ai_sections": ai_suggestions.get("sections") if ai_suggestions else None,
        "lyrics": lyrics if lyrics else [],
        "lyrics_scaled": lyrics_scaled,
        "lyrics_offset_seconds": round(lyrics_offset, 1),
        "quality": {
            "beat_coverage": round(beat_coverage, 3),
            "section_count": len(sections),
            "max_section_duration": max((s["end"] - s["start"]) for s in sections) if sections else 0,
        }
    }

def detect_sections(y, sr, rms, spectral_centroid, beat_times, onset_times):
    """Detect song sections using energy and spectral changes."""
    hop_len = 512
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_len)
    
    # Smooth RMS for section detection
    rms_smooth = np.convolve(rms, np.ones(50)/50, mode='same')
    
    # Find significant changes
    sections = []
    current_start = 0.0
    current_label = "intro"
    last_rms_mean = 0
    
    # Use beat segments as section boundaries
    for i, beat_time in enumerate(beat_times):
        if beat_time < 2.0:
            continue
        if beat_time > times[-1]:
            break
        
        idx = np.argmin(np.abs(times - beat_time))
        rms_val = rms_smooth[min(idx, len(rms_smooth)-1)]
        
        duration = beat_time - current_start
        if duration >= 8.0 and abs(rms_val - last_rms_mean) > 0.02:
            label = classify_section(current_start, beat_time, rms_val, y, sr, onset_times)
            sections.append({
                "start": float(current_start),
                "end": float(beat_time),
                "label": label,
                "rms": float(rms_val)
            })
            current_start = float(beat_time)
            last_rms_mean = rms_val
    
    # Final section
    if current_start < len(y) / sr:
        sections.append({
            "start": float(current_start),
            "end": float(len(y) / sr),
            "label": "outro",
            "rms": float(rms_smooth[-1])
        })
    
    return sections

def classify_section(start, end, rms, y, sr, onset_times):
    """Classify a section based on energy and onset density."""
    onset_count = sum(1 for t in onset_times if start <= t < end)
    duration = end - start
    density = onset_count / max(duration, 0.1)
    
    if rms < 0.05:
        return "quiet"
    elif density > 4:
        return "chorus"
    elif density > 2.5:
        return "verse"
    elif rms > 0.15:
        return "bridge"
    else:
        return "verse"

def build_fret_map(estimated_key, ai_suggestions=None):
    """Map pitch classes to guitar frets. Uses AI mapping if available."""
    
    # Use AI's fret mapping if available
    if ai_suggestions and "fret_mapping" in ai_suggestions:
        ai_map = ai_suggestions["fret_mapping"]
        if isinstance(ai_map, dict) and len(ai_map) >= 5:
            # Convert string keys to int, validate values 0-4
            fret_map = {}
            for k, v in ai_map.items():
                pc = int(k)
                fret = int(v)
                if 0 <= pc <= 11 and 0 <= fret <= 4:
                    fret_map[pc] = fret
            if len(fret_map) >= 5:
                # Fill in missing pitch classes
                for pc in range(12):
                    if pc not in fret_map:
                        fret_map[pc] = min(fret_map.values())
                return fret_map
    
    # Default: map based on pentatonic scale of the key
    pentatonic = [(estimated_key + i) % 12 for i in [0, 2, 4, 7, 9]]
    
    fret_map = {}
    for fret, pc in enumerate(pentatonic):
        fret_map[pc] = fret
    
    for pc in range(12):
        if pc not in fret_map:
            distances = [min(abs(pc - p), 12 - abs(pc - p)) for p in pentatonic]
            nearest = pentatonic[np.argmin(distances)]
            fret_map[pc] = fret_map[nearest]
    
    return fret_map

def _section_rand(pattern, beat_idx, seed):
    """Deterministic pseudo-random [0,1) based on pattern + beat index.
    Same pattern at same beat offset always produces the same value,
    so repeated sections (e.g. multiple choruses) get identical note patterns."""
    h = hash(pattern) ^ (beat_idx * 0x9E3779B9) ^ (seed * 0x517CC1B7)
    h = (h ^ (h >> 16)) * 0x85ebca6b
    h = (h ^ (h >> 13)) * 0xc2b2ae35
    h = h ^ (h >> 16)
    return (h & 0x7FFFFFFF) / 0x7FFFFFFF

def _deterministic_fret(pattern, beat_idx, seed, weights, max_fret):
    """Pick a fret deterministically from weighted distribution."""
    r = _section_rand(pattern, beat_idx, seed)
    total = sum(weights[:max_fret+1])
    cumsum = 0.0
    for i, w in enumerate(weights[:max_fret+1]):
        cumsum += w / total
        if r < cumsum:
            return i
    return max_fret

def generate_all_difficulties(onset_notes, beat_times, sections, tempo, fret_map, ai_suggestions, duration):
    """Generate notes per difficulty using beat-driven grid + AI section patterns."""
    
    res = RESOLUTION
    use_16th = tempo > 140
    steps_per_beat = 4 if use_16th else 2
    
    # Guitar style presets: (min_fret, max_fret, chord_mult, sustain_mult)
    GUITAR_STYLES = {
        "clean_arpeggios":     (0, 4, 0.1, 2.5),
        "palm_muted_chugs":    (0, 1, 0.2, 0.2),
        "open_chords":         (0, 2, 1.0, 1.5),
        "power_chords":        (0, 3, 1.2, 0.5),
        "lead_melody":         (1, 4, 0.05, 0.8),
        "single_note_riff":    (0, 3, 0.1, 0.3),
        "silence":             (0, 0, 0.0, 0.0),
        "octave_chords":       (0, 2, 0.9, 0.7),
        "arpeggiated_chords":  (0, 3, 0.2, 1.8),
    }
    DEFAULT_STYLE = "power_chords"
    
    # Build grid from actual beat_times with interpolated subdivisions
    grid_times = []
    is_beat_pos = []
    
    if len(beat_times) >= 2:
        # Estimate local tempo from last beats for the untracked tail
        tail_interval = np.mean(np.diff(beat_times[-10:])) if len(beat_times) >= 10 else (60.0 / tempo)
        
        for i in range(len(beat_times) - 1):
            bt_start = beat_times[i]
            bt_end = beat_times[i + 1]
            subdiv_spacing = (bt_end - bt_start) / steps_per_beat
            for step in range(steps_per_beat):
                grid_times.append(bt_start + step * subdiv_spacing)
                is_beat_pos.append(step == 0)
        
        # Continue past the last beat using the tail tempo
        t = beat_times[-1]
        tail_spacing = tail_interval / steps_per_beat
        step_idx = len(is_beat_pos)
        while t < duration + tail_spacing:
            grid_times.append(t)
            is_beat_pos.append(step_idx % steps_per_beat == 0)
            t += tail_spacing
            step_idx += 1
    else:
        # Fallback: uniform grid (shouldn't happen in practice)
        base_spacing = (60.0 / tempo) / (4 if use_16th else 2)
        t = 0.0
        while t < duration + base_spacing:
            grid_times.append(t)
            is_beat_pos.append(len(is_beat_pos) % steps_per_beat == 0)
            t += base_spacing
    
    # Mark grid positions that fall on onsets
    grid_has_onset = [False] * len(grid_times)
    for o in onset_notes:
        idx = min(range(len(grid_times)), key=lambda i: abs(grid_times[i] - o["time"]))
        if abs(grid_times[idx] - o["time"]) < 0.05:
            grid_has_onset[idx] = True
    
    def fret_at(time):
        d_best, f_best = 99, 0
        for o in onset_notes:
            d = abs(o["time"] - time)
            if d < d_best:
                d_best, f_best = d, fret_map.get(o["pitch_class"], 0)
        return f_best if d_best < 0.15 else None
    
    # AI intelligence: global fret emphasis + per-section guitar styles
    fw = ai_suggestions.get("fret_emphasis", [5,5,5,5,3]) if ai_suggestions else [5,5,5,5,3]
    if isinstance(fw, list) and len(fw) >= 5:
        fw = [float(x)*10 for x in fw[:5]]
    else:
        fw = [5,5,5,5,3]
    w_sum = sum(fw)
    fret_probs = [w/w_sum for w in fw]
    
    ai_sections = ai_suggestions.get("sections", {}) if ai_suggestions else {}
    
    # Build per-section map: (start, end, guitar_style, energy, pattern_key, fret_range)
    sec_map = []
    for idx, sec in enumerate(sections):
        sec_start = sec["start"]
        sec_end = sec["end"]
        label = sec.get("label", "")
        
        # Match AI section by index (AI returns sections keyed by integer string)
        ai_sec = ai_sections.get(str(idx)) if isinstance(ai_sections, dict) else None
        
        if ai_sec and isinstance(ai_sec, dict):
            gs = ai_sec.get("guitar_style", DEFAULT_STYLE)
            energy = ai_sec.get("energy", 5)
            pattern_key = ai_sec.get("_pattern_key", f"{gs}_{idx}")
            sec_label = ai_sec.get("label", label)
        else:
            gs = DEFAULT_STYLE
            energy = 5
            pattern_key = f"{label}_{idx}"
            sec_label = label
        
        style_preset = GUITAR_STYLES.get(gs, GUITAR_STYLES[DEFAULT_STYLE])
        
        # Blend AI energy with actual audio onset density
        onset_count = sum(1 for o in onset_notes if sec_start <= o["time"] < sec_end)
        dur = sec_end - sec_start
        onset_density = onset_count / max(dur, 0.5)
        audio_intensity = min(10.0, onset_density * 2.0)
        blended_energy = energy * 0.4 + audio_intensity * 0.6
        
        sec_map.append((sec_start, sec_end, gs, blended_energy, pattern_key, style_preset, sec_label))
    
    def sec_at(t):
        for st, en, gs, ene, pk, pr, lb in sec_map:
            if st <= t < en:
                return gs, ene, pk, pr
        return DEFAULT_STYLE, 5, "default", GUITAR_STYLES[DEFAULT_STYLE]
    
    difficulties = {}
    # Average beat interval for sustain calculations
    avg_beat_interval = 60.0 / tempo if len(beat_times) < 2 else np.mean(np.diff(beat_times[:min(50, len(beat_times))]))
    
    # Expert: all grid positions, heavy chords, orange allowed
    exp = []
    current_sec_key = None
    pos_in_sec = 0
    for i, time in enumerate(grid_times):
        if time > duration: break
        gs, ene, pk, (min_f, max_f, cm, sm) = sec_at(time)
        # Reset position counter when section changes
        if pk != current_sec_key:
            current_sec_key = pk
            pos_in_sec = 0
        cr = cm * (ene/5.0) * 1.5
        f = fret_at(time) if grid_has_onset[i] else None
        fret = min(f, max_f) if f is not None else _deterministic_fret(pk, pos_in_sec, 1, fret_probs, max_f)
        tick = time_to_tick(time, tempo)
        exp.append({"tick":tick,"fret":fret,"length":0})
        if _section_rand(pk, pos_in_sec, 2) < cr:
            exp.append({"tick":tick,"fret":min((fret+2)%5,max_f),"length":0})
        pos_in_sec += 1
    exp.sort(key=lambda n:(n["tick"],n["fret"]))
    difficulties["ExpertSingle"] = exp
    
    # Medium: beats only, no orange, some sustains, lower chord density
    med = []
    current_sec_key = None
    beat_in_sec = 0
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_beat_pos[i]: continue
        gs, ene, pk, (min_f, max_f, cm, sm) = sec_at(time)
        if pk != current_sec_key:
            current_sec_key = pk
            beat_in_sec = 0
        cr = cm * (ene/5.0) * 0.5
        cr = min(cr, 0.35)  # Cap chord probability for Medium
        sr = sm * (ene/5.0) * 0.5
        # Note density gate: skip beats in lower-energy sections
        note_prob = 0.35 + (ene / 10.0) * 0.65
        if _section_rand(pk, beat_in_sec, 4) > note_prob:
            beat_in_sec += 1
            continue
        max_mf = min(max_f, 3)  # Medium: no orange
        f = fret_at(time) if grid_has_onset[i] else None
        fret = min(f, max_mf) if f is not None else _deterministic_fret(pk, beat_in_sec, 1, fret_probs, max_mf)
        tick = time_to_tick(time, tempo)
        length = int(avg_beat_interval*1.5*res*tempo/60.0) if _section_rand(pk, beat_in_sec, 2) < sr else 0
        med.append({"tick":tick,"fret":fret,"length":length})
        if _section_rand(pk, beat_in_sec, 3) < cr:
            med.append({"tick":tick,"fret":min((fret+2)%5,max_mf),"length":0})
        beat_in_sec += 1
    med.sort(key=lambda n:(n["tick"],n["fret"]))
    difficulties["MediumSingle"] = med
    
    # Hard: derived from Medium + ~20% deterministic orange (fret 4) upgrades
    hrd = [{"tick": n["tick"], "fret": n["fret"], "length": n["length"]} for n in med]
    for i, n in enumerate(hrd):
        # Deterministic: same beat across same section gets same orange decision
        if _section_rand("hard_orange", i, 1) < 0.20:
            n["fret"] = 4
    difficulties["HardSingle"] = hrd
    
    # Easy: every other beat, heavy sustains, minimal chords
    easy = []
    current_sec_key = None
    beat_in_sec = 0
    bi = 0
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_beat_pos[i]: continue
        bi += 1
        if bi % 2 != 0: continue
        gs, ene, pk, (min_f, max_f, cm, sm) = sec_at(time)
        if pk != current_sec_key:
            current_sec_key = pk
            beat_in_sec = 0
        sr = sm * (ene/5.0) * 2.0
        max_mf = min(max_f, 3)
        f = fret_at(time) if grid_has_onset[i] else None
        fret = min(f, max_mf) if f is not None else _deterministic_fret(pk, beat_in_sec, 1, fret_probs, max_mf)
        tick = time_to_tick(time, tempo)
        length = int(avg_beat_interval*2.0*res*tempo/60.0) if _section_rand(pk, beat_in_sec, 2) < sr else 0
        easy.append({"tick":tick,"fret":fret,"length":length})
        if _section_rand(pk, beat_in_sec, 3) < 0.03:
            easy.append({"tick":tick,"fret":min((fret+1)%5,max_mf),"length":0})
        beat_in_sec += 1
    easy.sort(key=lambda n:(n["tick"],n["fret"]))
    difficulties["EasySingle"] = easy
    
    return difficulties

def detect_bass_onsets(y, sr, hop_length=512, cutoff_hz=220):
    """Detect onsets in the bass register (low-pass filtered harmonic content).
    Bass root notes produce the strongest low-frequency attacks, which guitars
    and cymbals don't contribute to."""
    sos = butter(6, cutoff_hz / (sr / 2.0), btype='low', output='sos')
    y_low = sosfilt(sos, y)
    onset_env = librosa.onset.onset_strength(y=y_low, sr=sr, hop_length=hop_length,
                                             aggregate=np.mean)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr,
                                              hop_length=hop_length, backtrack=True)
    return librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_length)

def generate_bass_difficulties(onset_notes, bass_onsets, beat_times, sections, tempo, fret_map, ai_suggestions, duration):
    """Generate bass (5-lane) difficulties.
    Bass follows the low-frequency onsets: single notes on the low frets,
    occasional octave double-stops, and long natural sustains."""
    
    res = RESOLUTION
    use_16th = tempo > 140
    steps_per_beat = 4 if use_16th else 2
    avg_beat_interval = 60.0 / tempo if len(beat_times) < 2 else np.mean(np.diff(beat_times[:min(50, len(beat_times))]))
    
    # Bass weights favor low frets (green/red/yellow); blue only on harder diffs
    bass_weights = [5, 4, 3, 1, 0]
    
    # Build subdivision grid from beats
    grid_times = []
    is_beat = []
    
    if len(beat_times) >= 2:
        tail_interval = np.mean(np.diff(beat_times[-10:])) if len(beat_times) >= 10 else (60.0 / tempo)
        for i in range(len(beat_times) - 1):
            bt_start = beat_times[i]
            bt_end = beat_times[i + 1]
            subdiv_spacing = (bt_end - bt_start) / steps_per_beat
            for step in range(steps_per_beat):
                grid_times.append(bt_start + step * subdiv_spacing)
                is_beat.append(step == 0)
        
        t = beat_times[-1]
        tail_spacing = tail_interval / steps_per_beat
        step_idx = len(is_beat)
        while t < duration + tail_spacing:
            grid_times.append(t)
            is_beat.append(step_idx % steps_per_beat == 0)
            t += tail_spacing
            step_idx += 1
    else:
        base_spacing = (60.0 / tempo) / steps_per_beat
        t = 0.0
        while t < duration + base_spacing:
            grid_times.append(t)
            is_beat.append(len(is_beat) % steps_per_beat == 0)
            t += base_spacing
    
    # Mark grid positions that fall on bass onsets
    grid_has_bass = [False] * len(grid_times)
    for bt in bass_onsets:
        idx = min(range(len(grid_times)), key=lambda i: abs(grid_times[i] - bt))
        if abs(grid_times[idx] - bt) < 0.08:
            grid_has_bass[idx] = True
    
    def bass_fret_at(time):
        d_best, f_best = 99, 0
        for o in onset_notes:
            d = abs(o["time"] - time)
            if d < d_best:
                d_best, f_best = d, fret_map.get(o["pitch_class"], 0)
        return f_best if d_best < 0.15 else None
    
    def sec_energy(t):
        for sec in sections:
            if sec["start"] <= t < sec["end"]:
                dur = max(sec["end"] - sec["start"], 0.5)
                count = sum(1 for bt in bass_onsets if sec["start"] <= bt < sec["end"])
                return min(10.0, 3.0 + (count / dur) * 2.5)
        return 5.0
    
    def sustain_len(mult):
        return int(avg_beat_interval * mult * res * tempo / 60.0)
    
    def is_eighth(i):
        if steps_per_beat == 4:
            return i % 2 == 0  # 16th grid → every other position is an eighth
        return True            # 8th grid → every position already is
    
    difficulties = {}
    max_frets = {"EasyDoubleBass": 2, "MediumDoubleBass": 2, "HardDoubleBass": 3, "ExpertDoubleBass": 3}
    
    # Expert: full bass groove on eighths, occasional octave double-stops
    exp = []
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_eighth(i): continue
        ene = sec_energy(time)
        has = grid_has_bass[i]
        if not has and _section_rand("bx", i, 1) > 0.30 * (ene / 5.0):
            continue
        fret = bass_fret_at(time)
        fret = min(fret, 3) if fret is not None else _deterministic_fret("bx", i, 1, bass_weights, 3)
        tick = time_to_tick(time, tempo)
        length = sustain_len(1.0) if has and _section_rand("bx", i, 2) < 0.35 * (ene / 5.0) else 0
        exp.append({"tick": tick, "fret": fret, "length": length})
        if has and _section_rand("bx", i, 3) < 0.08:
            exp.append({"tick": tick, "fret": min((fret + 2) % 5, 3), "length": 0})
    exp.sort(key=lambda n: (n["tick"], n["fret"]))
    difficulties["ExpertDoubleBass"] = exp
    
    # Hard: eighths only where the bass actually plays, some fills on beats
    hrd = []
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_eighth(i): continue
        ene = sec_energy(time)
        has = grid_has_bass[i]
        if not has:
            if not is_beat[i] or _section_rand("bh", i, 1) > 0.20 * (ene / 5.0):
                continue
        fret = bass_fret_at(time)
        fret = min(fret, 3) if fret is not None else _deterministic_fret("bh", i, 1, bass_weights, 3)
        tick = time_to_tick(time, tempo)
        length = sustain_len(1.0) if _section_rand("bh", i, 2) < 0.40 * (ene / 5.0) else 0
        hrd.append({"tick": tick, "fret": fret, "length": length})
        if has and _section_rand("bh", i, 3) < 0.04:
            hrd.append({"tick": tick, "fret": min((fret + 2) % 5, 3), "length": 0})
    hrd.sort(key=lambda n: (n["tick"], n["fret"]))
    difficulties["HardDoubleBass"] = hrd
    
    # Medium: beats only, low frets, no double-stops, more sustains
    med = []
    beat_idx = 0
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_beat[i]: continue
        ene = sec_energy(time)
        has = grid_has_bass[i]
        note_prob = 0.55 + (ene / 10.0) * 0.45
        if not has and _section_rand("bm", beat_idx, 4) > note_prob:
            beat_idx += 1
            continue
        fret = bass_fret_at(time)
        fret = min(fret, 2) if fret is not None else _deterministic_fret("bm", beat_idx, 1, bass_weights, 2)
        tick = time_to_tick(time, tempo)
        length = sustain_len(1.5) if _section_rand("bm", beat_idx, 2) < 0.50 * (ene / 5.0) else 0
        med.append({"tick": tick, "fret": fret, "length": length})
        beat_idx += 1
    med.sort(key=lambda n: (n["tick"], n["fret"]))
    difficulties["MediumDoubleBass"] = med
    
    # Easy: every other beat, heavy sustains
    easy = []
    beat_idx = 0
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_beat[i]: continue
        if beat_idx % 2 != 0:
            beat_idx += 1
            continue
        ene = sec_energy(time)
        has = grid_has_bass[i]
        note_prob = 0.45 + (ene / 10.0) * 0.55
        if not has and _section_rand("be", beat_idx, 4) > note_prob:
            beat_idx += 1
            continue
        fret = bass_fret_at(time)
        fret = min(fret, 2) if fret is not None else _deterministic_fret("be", beat_idx, 1, bass_weights, 2)
        tick = time_to_tick(time, tempo)
        length = sustain_len(2.0) if _section_rand("be", beat_idx, 2) < 0.65 * (ene / 5.0) else 0
        easy.append({"tick": tick, "fret": fret, "length": length})
        beat_idx += 1
    easy.sort(key=lambda n: (n["tick"], n["fret"]))
    difficulties["EasyDoubleBass"] = easy
    
    return difficulties

def extract_section_features(sections, onset_notes, onset_env, rms, spectral_centroid, 
                              beat_times, sr, hop_len, duration, tempo):
    """Extract per-section audio features for Gemini musical analysis.
    Returns compact summaries: onset patterns, pitch distribution, energy, spectral data."""
    
    features = []
    
    for sec in sections:
        s, e = sec["start"], sec["end"]
        label = sec.get("label", "")
        
        # Onsets in this section
        sec_onsets = [o for o in onset_notes if s <= o["time"] < e]
        onset_times_in_sec = [o["time"] for o in sec_onsets]
        
        # Pitch class distribution
        pc_hist = [0] * 12
        for o in sec_onsets:
            pc_hist[o["pitch_class"]] += 1
        total = sum(pc_hist)
        top_pcs = sorted([(i, pc_hist[i]/max(total,1)) for i in range(12)], 
                        key=lambda x: x[1], reverse=True)[:4]
        pc_summary = " ".join(f"PC{i}={p*100:.0f}%" for i, p in top_pcs if p > 0.05)
        bass_heavy = sum(pc_hist[0:4]) / max(total, 1)  # PC0-3
        mid_heavy = sum(pc_hist[4:8]) / max(total, 1)     # PC4-7
        high_heavy = sum(pc_hist[8:12]) / max(total, 1)    # PC8-11
        
        # Onset density per beat
        sec_beats = [bt for bt in beat_times if s <= bt < e]
        if len(sec_beats) >= 2:
            onsets_per_beat = []
            for i in range(len(sec_beats) - 1):
                count = sum(1 for ot in onset_times_in_sec if sec_beats[i] <= ot < sec_beats[i+1])
                onsets_per_beat.append(count)
            
            avg_onsets = sum(onsets_per_beat) / max(len(onsets_per_beat), 1)
            # Detect pattern: alternating, constant, building, sparse
            if len(onsets_per_beat) >= 8:
                half = len(onsets_per_beat) // 2
                first_half_avg = sum(onsets_per_beat[:half]) / max(half, 1)
                second_half_avg = sum(onsets_per_beat[half:]) / max(len(onsets_per_beat)-half, 1)
                
                if second_half_avg > first_half_avg * 1.4:
                    onset_trend = "building"
                elif second_half_avg < first_half_avg * 0.6:
                    onset_trend = "fading"
                else:
                    # Check for alternating pattern (even vs odd beats)
                    even_avg = sum(onsets_per_beat[0::2]) / max(len(onsets_per_beat[0::2]), 1)
                    odd_avg = sum(onsets_per_beat[1::2]) / max(len(onsets_per_beat[1::2]), 1)
                    if abs(even_avg - odd_avg) > 1.5 and max(even_avg, odd_avg) > 0:
                        onset_trend = "alternating"
                    else:
                        onset_trend = "constant"
            else:
                onset_trend = "constant"
            
            # Compress onset pattern for display (max 16 values)
            display_onsets = onsets_per_beat
            if len(display_onsets) > 16:
                step = len(display_onsets) / 16
                display_onsets = [display_onsets[int(i * step)] for i in range(16)]
        else:
            avg_onsets = len(sec_onsets) / max(e - s, 0.5) * (60.0 / max(tempo, 60))
            onset_trend = "sparse"
            display_onsets = [0]
        
        # Energy (RMS) stats
        s_frame = int(s * sr / hop_len)
        e_frame = int(e * sr / hop_len)
        s_frame = max(0, min(s_frame, len(rms) - 2))
        e_frame = max(s_frame + 1, min(e_frame, len(rms)))
        sec_rms = rms[s_frame:e_frame]
        rms_mean = float(np.mean(sec_rms)) if len(sec_rms) > 0 else 0
        if len(sec_rms) >= 4:
            early = np.mean(sec_rms[:len(sec_rms)//3])
            late = np.mean(sec_rms[2*len(sec_rms)//3:])
            if late > early * 1.3:
                rms_trend = "rising"
            elif late < early * 0.7:
                rms_trend = "falling"
            else:
                rms_trend = "flat"
        else:
            rms_trend = "flat"
        
        # Spectral centroid (brightness)
        sec_sc = spectral_centroid[s_frame:e_frame]
        sc_mean = float(np.mean(sec_sc)) if len(sec_sc) > 0 else 0
        sc_trend = "flat"
        if len(sec_sc) >= 4:
            early_sc = np.mean(sec_sc[:len(sec_sc)//3])
            late_sc = np.mean(sec_sc[2*len(sec_sc)//3:])
            if late_sc > early_sc * 1.2:
                sc_trend = "brightening"
            elif late_sc < early_sc * 0.8:
                sc_trend = "darkening"
        
        # Onset attack sharpness (from onset envelope)
        s_sample = int(s * sr / hop_len)
        e_sample = int(e * sr / hop_len)
        if s_sample < len(onset_env) and e_sample > s_sample:
            sec_env = onset_env[s_sample:e_sample]
            if len(sec_env) > 0:
                # Sharp attacks = high peak-to-mean ratio
                env_mean = np.mean(sec_env)
                env_peak = np.max(sec_env)
                attack_ratio = env_peak / max(env_mean, 0.001)
                if attack_ratio > 3.0:
                    attack_style = "very sharp"
                elif attack_ratio > 2.0:
                    attack_style = "sharp"
                elif attack_ratio > 1.5:
                    attack_style = "moderate"
                else:
                    attack_style = "soft"
            else:
                attack_style = "unknown"
        else:
            attack_style = "unknown"
        
        features.append({
            "start": s,
            "end": e,
            "label": label,
            "num_beats": len(sec_beats),
            "onset_density": round(avg_onsets, 2),
            "onset_pattern": ",".join(str(x) for x in display_onsets[:16]),
            "onset_trend": onset_trend,
            "pc_summary": pc_summary,
            "bass_heavy": round(bass_heavy, 2),
            "mid_heavy": round(mid_heavy, 2),
            "high_heavy": round(high_heavy, 2),
            "rms": round(rms_mean, 3),
            "rms_trend": rms_trend,
            "spectral_centroid": round(sc_mean, 0),
            "sc_trend": sc_trend,
            "attack": attack_style,
        })
    
    # Detect similar sections using onset pattern cross-correlation
    for i, fi in enumerate(features):
        fi["similar_to"] = None
        fi["similarity"] = 0.0
        if fi["num_beats"] < 4:
            continue
        
        oi = [int(x) for x in fi["onset_pattern"].split(",")]
        for j, fj in enumerate(features):
            if j >= i:
                break
            if fj["num_beats"] < 4:
                continue
            oj = [int(x) for x in fj["onset_pattern"].split(",")]
            
            # Simple correlation on aligned patterns
            min_len = min(len(oi), len(oj))
            if min_len < 4:
                continue
            oi_trim = oi[:min_len]
            oj_trim = oj[:min_len]
            
            # Normalized cross-correlation
            mi = sum(oi_trim) / min_len
            mj = sum(oj_trim) / min_len
            num = sum((a - mi) * (b - mj) for a, b in zip(oi_trim, oj_trim))
            den = (sum((a - mi)**2 for a in oi_trim) * sum((b - mj)**2 for b in oj_trim)) ** 0.5
            if den > 0:
                corr = num / den
                if corr > 0.7:
                    fi["similar_to"] = j
                    fi["similarity"] = round(corr, 2)
                    break
    
    return features


def get_gemini_analysis(api_key, tempo, key, sections, duration, onset_notes, section_features=None, song_name="", artist=""):
    """Use Gemini to enhance analysis with musical judgment from audio features."""
    import urllib.request, time
    
    if not section_features:
        return None
    
    # Build audio feature summary for the prompt
    feature_lines = []
    for i, sf in enumerate(section_features):
        sim_note = ""
        if sf["similar_to"] is not None:
            sim_note = f" ⚠ SCRIPT SAYS: similar to section {sf['similar_to']} (corr={sf['similarity']})"
        
        feature_lines.append(
            f"Section {i} ({sf['start']:.1f}s-{sf['end']:.1f}s, {sf['num_beats']} beats):\n"
            f"  label={sf['label']}  onsets/beat={sf['onset_density']} [{sf['onset_trend']}]\n"
            f"  onset_pattern: [{sf['onset_pattern']}]\n"
            f"  pitch: {sf['pc_summary']}  bass={sf['bass_heavy']:.0%} mid={sf['mid_heavy']:.0%} high={sf['high_heavy']:.0%}\n"
            f"  energy={sf['rms']:.3f} [{sf['rms_trend']}]  brightness={sf['spectral_centroid']:.0f}Hz [{sf['sc_trend']}]\n"
            f"  attack={sf['attack']}{sim_note}"
        )
    
    feature_text = "\n".join(feature_lines)
    
    prompt = f"""You are analyzing audio features to determine guitar playing style. Do NOT use song memory — use ONLY the audio data below.

Song metadata: {song_name} by {artist} (for context only — base decisions on audio features)
Tempo: {tempo:.0f} BPM, Key PC: {key}, Duration: {duration:.0f}s

AUDIO FEATURES PER SECTION:
{feature_text}

For each section, determine the guitar playing style from the audio evidence. Return ONLY this JSON:

{{
  "fret_emphasis": [green, red, yellow, blue, orange],
  "sections": {{
    "<section_index>": {{
      "label": "intro|verse|pre_chorus|chorus|bridge|solo|breakdown|outro",
      "guitar_style": "clean_arpeggios|palm_muted_chugs|open_chords|power_chords|lead_melody|single_note_riff|silence|octave_chords|arpeggiated_chords",
      "energy": 1-10,
      "identical_to": null or section_index of musically identical section
    }}
  }}
}}

HOW TO READ THE AUDIO FEATURES:
- onset_density: beats with 1-2 onsets = single notes/arpeggios, 3-5 = strummed chords, 6+ = dense chords/wall of sound
- onset_pattern: alternating (2,1,2,1...) = picked arpeggio. constant (3,3,3,3...) = strumming. gallop (3,1,3,1 or 3,3,3,1) = palm-muted chugs. sparse (1,1,1...) = single note line.
- pitch distribution: bass-heavy (PC0-3) = low power chords/chugs. mid-heavy (PC4-7) = open chords. high-heavy (PC8-11) = lead melody. spread = arpeggios.
- energy (rms): low <0.05 = clean/quiet, 0.05-0.15 = moderate, >0.15 = loud/distorted
- brightness: <800Hz = dark/muffled (palm muting), 800-2000Hz = normal, >2000Hz = bright/trebly (lead, open chords)
- attack sharpness: "very sharp" + low brightness + bass-heavy = palm muting. "soft" + spread pitch = arpeggios. "sharp" + high brightness = lead picking.

GUITAR STYLE MAPPING:
- palm_muted_chugs: bass-heavy, very sharp attack, low brightness, onset_pattern has 3,1,3,1 or gallop feel, energy moderate
- power_chords: bass-heavy + mid-heavy, sharp attack, brightness normal, onset_density 3-6, energy moderate-high
- open_chords: mid-heavy, moderate attack, brightness normal-high, onset_density 3-5, ringing feel
- clean_arpeggios: spread pitch, soft attack, onset_pattern alternating (2,1,2,1...), brightness normal, energy low-moderate
- lead_melody: high-heavy, sharp attack, high brightness, onset_density 1-3, single note focus
- single_note_riff: bass or mid focused, moderate attack, onset_density 1-3, energy moderate
- silence: very low energy, very few onsets

RULES:
- fret_emphasis: 5 floats summing to 1.0. Use pitch distribution to weight: bass-heavy = more green/red, mid-heavy = balanced, high-heavy = more yellow/blue/orange. orange=0 for Medium.
- energy: based on RMS level. <0.05=1-3, 0.05-0.10=4-6, 0.10-0.20=7-8, >0.20=9-10
- identical_to: use the script's similarity hints (marked "SCRIPT SAYS"). If script found similarity >0.75, sections are likely identical.
- Fix any wrong labels. The script sometimes confuses bridges for choruses.
- Consider the onset_trend: "building" = rising energy section (pre-chorus?), "constant" = stable section.
- Only output JSON. No markdown, no explanation."""

    req_data = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048}
    }).encode()
    
    # Try primary model, fall back to backup
    models = [
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash-lite",
    ]
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=req_data, 
                                              headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=45) as resp:
                    result = json.loads(resp.read())
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                
                # Strip markdown fences
                text = text.strip()
                if text.startswith("```"):
                    first_nl = text.find('\n')
                    text = text[first_nl+1:] if first_nl > 0 else text[3:]
                if text.rstrip().endswith("```"):
                    text = text.rstrip()[:-3].strip()
                
                # Extract JSON object
                start_brace = text.find('{')
                end_brace = text.rfind('}')
                if start_brace >= 0 and end_brace > start_brace:
                    text = text[start_brace:end_brace+1]
                text = text.strip()
                
                # Parse JSON with fallback
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    fixed = re.sub(r"'(\w+)':", r'"\1":', text)
                    fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
                    data = json.loads(fixed)
                
                # Validate AI output
                valid = True
                
                # fret_emphasis validation
                if "fret_emphasis" not in data or not isinstance(data["fret_emphasis"], list):
                    valid = False
                else:
                    fw = data["fret_emphasis"]
                    total = sum(float(x) for x in fw[:5])
                    if total > 0:
                        data["fret_emphasis"] = [float(x)/total for x in fw[:5]]
                
                # sections validation
                if "sections" not in data or not isinstance(data["sections"], dict):
                    valid = False
                else:
                    valid_styles = {"clean_arpeggios","palm_muted_chugs","open_chords","power_chords",
                                    "lead_melody","single_note_riff","silence","octave_chords","arpeggiated_chords"}
                    for key, sec in list(data["sections"].items()):
                        if not isinstance(sec, dict): 
                            continue
                        gs = sec.get("guitar_style", "")
                        if gs not in valid_styles:
                            sec["guitar_style"] = "power_chords"  # safe default
                        en = sec.get("energy", 5)
                        sec["energy"] = max(1, min(10, int(en)))
                        lb = sec.get("label", "verse")
                        sec["label"] = lb
                        # Resolve identical_to references
                        ref = sec.get("identical_to")
                        if ref and ref in data["sections"]:
                            sec["_pattern_key"] = ref
                        else:
                            sec["_pattern_key"] = key
                
                if not valid:
                    continue  # Try next model
                
                ns = len(data.get("sections", {}))
                styles = set(s.get("guitar_style","?") for s in data["sections"].values())
                linked = sum(1 for s in data["sections"].values() if s.get("identical_to"))
                print(f"  AI: {ns} sections, styles={styles}, {linked} linked repeats", file=sys.stderr)
                
                return data
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 1:
                    time.sleep(5)
                else:
                    break  # Try next model
            except Exception:
                if attempt < 1:
                    time.sleep(2)
                else:
                    break  # Try next model
    
    print(f"  Gemini unavailable (all models/retries exhausted)", file=sys.stderr)
    return None
    return None

def time_to_tick(t, bpm):
    """Convert time in seconds to ticks."""
    return int(round(t * RESOLUTION * bpm / 60.0))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: analyze.py <audio_file> [--gemini] [--lyrics-file <path>]"}))
        sys.exit(1)
    
    audio_file = sys.argv[1]
    use_gemini = "--gemini" in sys.argv
    
    lyrics_file = None
    if "--lyrics-file" in sys.argv:
        idx = sys.argv.index("--lyrics-file")
        if idx + 1 < len(sys.argv):
            lyrics_file = sys.argv[idx + 1]
    
    if not os.path.exists(audio_file):
        print(json.dumps({"error": f"File not found: {audio_file}"}))
        sys.exit(1)
    
    key = GEMINI_KEY if use_gemini else None
    # Build metadata from env vars if available
    metadata = {
        "name": os.environ.get("SONG_NAME", ""),
        "artist": os.environ.get("SONG_ARTIST", ""),
    }
    result = analyze_audio(audio_file, gemini_key=key, metadata=metadata, lyrics_file=lyrics_file)
    print(json.dumps(result))
