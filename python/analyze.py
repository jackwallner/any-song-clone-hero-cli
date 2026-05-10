#!/usr/bin/env python3
"""AI-powered audio analysis for Clone Hero chart generation.
Uses librosa for beat/onset detection + Gemini for intelligent note mapping."""

import sys, json, warnings, os, math
import numpy as np
warnings.filterwarnings("ignore")

import librosa

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
RESOLUTION = 480  # ticks per beat

def time_to_tick(t, tempo, offset=0):
    """Convert time in seconds to chart ticks at current tempo."""
    return int(round((t - offset) * RESOLUTION * tempo / 60.0))

def ticks_to_time(tick, tempo):
    """Convert ticks to seconds."""
    return tick * 60.0 / (RESOLUTION * tempo)

def analyze_audio(filepath, gemini_key=None):
    """Full audio analysis pipeline."""
    
    print("Loading audio...", file=sys.stderr)
    y, sr = librosa.load(filepath, sr=22050, mono=True)
    duration = len(y) / sr
    
    # Separate harmonic/percussive
    y_harm, y_perc = librosa.effects.hpss(y)
    
    # Beat tracking
    print("Detecting beats...", file=sys.stderr)
    tempo, beat_frames = librosa.beat.beat_track(y=y_perc, sr=sr, units='frames')
    tempo = float(tempo)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    
    # Onset detection
    print("Detecting onsets...", file=sys.stderr)
    onset_env = librosa.onset.onset_strength(y=y_perc, sr=sr)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, 
                                               backtrack=True, units='frames')
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    
    # Chroma for pitch analysis
    print("Analyzing pitch...", file=sys.stderr)
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr, hop_length=512)
    chroma_frames = librosa.feature.chroma_cens(y=y_harm, sr=sr, hop_length=512)
    
    # Estimate key
    chroma_mean = chroma.mean(axis=1)
    estimated_key = int(np.argmax(chroma_mean))
    
    # Get spectral features for section detection
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rms = librosa.feature.rms(y=y)[0]
    
    # Section detection based on energy/spectral changes
    sections = detect_sections(y, sr, rms, spectral_centroid, beat_times, onset_times)
    
    # Map onsets to frets using chroma
    onset_notes = []
    times_for_analysis = []
    chroma_for_analysis = []
    
    hop_len = 512
    for onset_time in onset_times:
        frame = int(onset_time * sr / hop_len)
        if frame < chroma.shape[1]:
            chroma_vec = chroma[:, frame]
            # Get the dominant pitch class
            top_pitches = np.argsort(chroma_vec)[-3:][::-1]  # top 3
            dominant = int(top_pitches[0])
            confidence = float(chroma_vec[dominant])
            
            onset_notes.append({
                "time": float(onset_time),
                "pitch_class": dominant,
                "confidence": confidence,
                "chroma": [float(c) for c in chroma_vec]
            })
            times_for_analysis.append(float(onset_time))
            chroma_for_analysis.append([float(c) for c in chroma_vec])
    
    # Use Gemini to enhance the analysis
    ai_suggestions = None
    if gemini_key:
        try:
            ai_suggestions = get_gemini_analysis(gemini_key, tempo, estimated_key, 
                                                  sections, duration, onset_notes[:200])
        except Exception as e:
            print(f"Gemini analysis failed (continuing without): {e}", file=sys.stderr)
    
    # Map to frets
    fret_map = build_fret_map(estimated_key, ai_suggestions)
    
    # Generate notes for all difficulties
    difficulties = generate_all_difficulties(onset_notes, beat_times, sections, 
                                              tempo, fret_map, ai_suggestions, duration)
    
    # Build tempo map
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
        "ai_enhanced": ai_suggestions is not None,
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
    """Map pitch classes to guitar frets (0=green, 1=red, 2=yellow, 3=blue, 4=orange).
    Default mapping based on the circle of fifths relative to the song key."""
    
    # Default: map based on pentatonic scale of the key
    # Key 0=C, 1=C#, 2=D, 3=D#, 4=E, 5=F, 6=F#, 7=G, 8=G#, 9=A, 10=A#, 11=B
    
    # Pentatonic major: 1, 2, 3, 5, 6 (relative to root)
    # Map to frets: root->green, 2nd->red, 3rd->yellow, 5th->blue, 6th->orange
    pentatonic = [(estimated_key + i) % 12 for i in [0, 2, 4, 7, 9]]
    
    # Build mapping: pitch_class -> fret
    fret_map = {}
    for fret, pc in enumerate(pentatonic):
        fret_map[pc] = fret
    
    # For non-pentatonic notes, map to nearest pentatonic
    for pc in range(12):
        if pc not in fret_map:
            # Find closest pentatonic note
            distances = [min(abs(pc - p), 12 - abs(pc - p)) for p in pentatonic]
            nearest = pentatonic[np.argmin(distances)]
            fret_map[pc] = fret_map[nearest]
    
    return fret_map

def generate_all_difficulties(onset_notes, beat_times, sections, tempo, fret_map, ai_suggestions, duration):
    """Generate notes for Easy, Medium, Hard, Expert."""
    
    difficulties = {}
    resolution = 480
    
    # Expert: dense, all notes
    difficulties["ExpertSingle"] = generate_notes(onset_notes, beat_times, tempo, 
                                                    fret_map, density=0.9, 
                                                    use_orange=True, use_chords=True,
                                                    min_interval=0.06)
    
    # Hard: medium density, includes orange, chords
    difficulties["HardSingle"] = generate_notes(onset_notes, beat_times, tempo,
                                                  fret_map, density=0.7,
                                                  use_orange=True, use_chords=True,
                                                  min_interval=0.08)
    
    # Medium: faster, no orange, some chords
    difficulties["MediumSingle"] = generate_notes(onset_notes, beat_times, tempo,
                                                    fret_map, density=0.5,
                                                    use_orange=False, use_chords=False,
                                                    min_interval=0.12)
    
    # Easy: simple, slow, no chords, no orange
    difficulties["EasySingle"] = generate_notes(onset_notes, beat_times, tempo,
                                                  fret_map, density=0.25,
                                                  use_orange=False, use_chords=False,
                                                  min_interval=0.20)
    
    return difficulties

def generate_notes(onset_notes, beat_times, bpm, fret_map, density=0.5,
                   use_orange=False, use_chords=False, min_interval=0.12):
    """Generate note events for a specific difficulty."""
    
    notes = []
    last_note_time = -min_interval
    beat_set = set(np.round(beat_times, 3))
    
    for onset in onset_notes:
        t = onset["time"]
        
        # Skip if too close to last note
        if t - last_note_time < min_interval:
            continue
        
        # Density filter based on beat alignment
        is_on_beat = any(abs(t - bt) < 0.05 for bt in beat_times)
        is_strong_beat = any(abs(t - bt) < 0.05 for i, bt in enumerate(beat_times) if i % 2 == 0)
        
        # Probability of including this note
        if is_strong_beat:
            prob = density * 1.5
        elif is_on_beat:
            prob = density
        else:
            prob = density * 0.3
        
        if np.random.random() > min(prob, 0.95):
            continue
        
        # Get fret
        pc = onset["pitch_class"]
        fret = fret_map.get(pc, 0)
        
        # Filter orange for lower difficulties
        if fret == 4 and not use_orange:
            # Remap to a non-orange fret
            fret = min(fret_map.values())  # fallback to root
        
        # Note length based on position in beat
        length = 0
        if is_strong_beat and density > 0.5:
            # Occasional sustains on strong beats
            if np.random.random() < 0.15:
                # Find next beat for sustain
                for bt in beat_times:
                    if bt > t + 0.1:
                        length_sec = min(bt - t, 1.0)
                        length = int(length_sec * RESOLUTION * bpm / 60.0)
                        break
        
        tick = time_to_tick(t, bpm)
        
        notes.append({
            "tick": tick,
            "fret": fret,
            "length": length
        })
        
        last_note_time = t
    
    return notes

def get_gemini_analysis(api_key, tempo, key, sections, duration, onset_notes):
    """Use Gemini to enhance the analysis with musical intelligence."""
    import urllib.request
    
    # Prepare a concise analysis request
    section_summary = "\n".join([
        f"  {s['start']:.1f}s-{s['end']:.1f}s: {s['label']} (energy={s['rms']:.3f})"
        for s in sections[:15]
    ])
    
    # Sample of onset notes for context
    onset_sample = onset_notes[:30] if len(onset_notes) > 30 else onset_notes
    onset_summary = ", ".join([
        f"t={o['time']:.2f}s pc={o['pitch_class']}"
        for o in onset_sample[:20]
    ])
    
    prompt = f"""Analyze this song for Clone Hero chart generation. Provide ONLY a JSON response.

Song info:
- BPM: {tempo:.1f}
- Key (pitch class, 0=C): {key}
- Duration: {duration:.1f}s
- Sections: 
{section_summary}

Sample onsets (time, pitch_class): {onset_summary}

Return JSON with:
1. "fret_mapping": map pitch classes 0-11 to frets 0-4 (0=green,1=red,2=yellow,3=blue,4=orange). Make musically sensible choices.
2. "section_patterns": for each section label, suggest note density multiplier (0-1) and whether to use chords.
3. "difficulty_params": for each difficulty (easy, medium, hard, expert), suggest density (0-1), min_note_interval (seconds), use_orange (bool), and use_chords (bool).

Respond ONLY with valid JSON, no markdown or explanation."""

    req_data = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048}
    }).encode()
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    req = urllib.request.Request(url, data=req_data, 
                                  headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            # Strip markdown code fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
            return json.loads(text)
    except Exception as e:
        print(f"Gemini API error: {e}", file=sys.stderr)
        return None

def time_to_tick(t, bpm):
    """Convert time in seconds to ticks."""
    return int(round(t * RESOLUTION * bpm / 60.0))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: analyze.py <audio_file> [--gemini]"}))
        sys.exit(1)
    
    audio_file = sys.argv[1]
    use_gemini = "--gemini" in sys.argv
    
    if not os.path.exists(audio_file):
        print(json.dumps({"error": f"File not found: {audio_file}"}))
        sys.exit(1)
    
    key = GEMINI_KEY if use_gemini else None
    result = analyze_audio(audio_file, gemini_key=key)
    print(json.dumps(result))
