#!/usr/bin/env python3
"""AI-powered audio analysis for Clone Hero chart generation.
Uses librosa for beat/onset detection + Gemini for intelligent note mapping."""

import sys, json, warnings, os, math, re
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
    
    # Use Gemini to enhance the analysis
    ai_suggestions = None
    if gemini_key:
        try:
            ai_suggestions = get_gemini_analysis(gemini_key, tempo, estimated_key, 
                                                  sections, duration, onset_notes)
        except Exception as e:
            print(f"  Gemini analysis failed (continuing without): {e}", file=sys.stderr)
    
    # Map to frets (uses AI fret_mapping if available)
    fret_map = build_fret_map(estimated_key, ai_suggestions)
    
    # Generate notes for all difficulties (uses AI difficulty_params if available)
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

def generate_all_difficulties(onset_notes, beat_times, sections, tempo, fret_map, ai_suggestions, duration):
    """Generate notes for Easy, Medium, Hard, Expert. Uses AI params if available."""
    
    # Default params per difficulty
    defaults = {
        "easy":    {"density": 0.25, "min_interval": 0.20, "use_orange": False, "use_chords": False},
        "medium":  {"density": 0.50, "min_interval": 0.12, "use_orange": False, "use_chords": False},
        "hard":    {"density": 0.70, "min_interval": 0.08, "use_orange": True,  "use_chords": True},
        "expert":  {"density": 0.90, "min_interval": 0.06, "use_orange": True,  "use_chords": True},
    }
    
    # Override with AI suggestions if available
    if ai_suggestions and "difficulty_params" in ai_suggestions:
        ai_params = ai_suggestions["difficulty_params"]
        for diff_name in defaults:
            if diff_name in ai_params:
                ap = ai_params[diff_name]
                if isinstance(ap, dict):
                    if "density" in ap:
                        defaults[diff_name]["density"] = float(ap["density"])
                    if "min_interval" in ap:
                        defaults[diff_name]["min_interval"] = float(ap["min_interval"])
    
    difficulties = {}
    
    # Expert: dense, all notes
    p = defaults["expert"]
    difficulties["ExpertSingle"] = generate_notes(onset_notes, beat_times, tempo,
                                                    fret_map, density=p["density"],
                                                    use_orange=p["use_orange"], use_chords=p["use_chords"],
                                                    min_interval=p["min_interval"])
    
    # Hard: medium-high density
    p = defaults["hard"]
    difficulties["HardSingle"] = generate_notes(onset_notes, beat_times, tempo,
                                                  fret_map, density=p["density"],
                                                  use_orange=p["use_orange"], use_chords=p["use_chords"],
                                                  min_interval=p["min_interval"])
    
    # Medium: medium density, no orange
    p = defaults["medium"]
    difficulties["MediumSingle"] = generate_notes(onset_notes, beat_times, tempo,
                                                    fret_map, density=p["density"],
                                                    use_orange=p["use_orange"], use_chords=p["use_chords"],
                                                    min_interval=p["min_interval"])
    
    # Easy: simple
    p = defaults["easy"]
    difficulties["EasySingle"] = generate_notes(onset_notes, beat_times, tempo,
                                                  fret_map, density=p["density"],
                                                  use_orange=p["use_orange"], use_chords=p["use_chords"],
                                                  min_interval=p["min_interval"])
    
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
    """Use Gemini to enhance analysis. Compact prompt, retry on rate limit."""
    import urllib.request, time
    
    # Compact section summary
    sec_json = json.dumps([{
        "start": round(s["start"], 1), 
        "end": round(s["end"], 1), 
        "label": s["label"]
    } for s in sections[:10]])
    
    # Summary stats instead of raw onsets
    onset_times = [o["time"] for o in onset_notes]
    onset_pcs = [o["pitch_class"] for o in onset_notes]
    onset_count = len(onset_notes)
    avg_onsets_per_sec = onset_count / max(duration, 1)
    pc_dist = [onset_pcs.count(i) for i in range(12)]
    
    prompt = f"""Song: {tempo:.0f} BPM, key PC={key}, {duration:.0f}s, {onset_count} onsets ({avg_onsets_per_sec:.1f}/s).
Pitch class distribution: {pc_dist}
Sections: {sec_json}

Return JSON:
{{"fret_mapping": {{0:x,1:x,2:x,3:x,4:x}}, "difficulty_params": {{"easy":{{"density":x,"min_interval":x}},
"medium":{{"density":x,"min_interval":x}},"hard":{{"density":x,"min_interval":x}},"expert":{{"density":x,"min_interval":x}}}}}}

Map pitch classes 0-11→frets 0-4 based on key. Density 0-1. min_interval in seconds.
JSON only, no markdown."""

    req_data = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024}
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
                    return json.loads(text)
                except json.JSONDecodeError:
                    fixed = re.sub(r"'(\w+)':", r'"\1":', text)
                    fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
                    return json.loads(fixed)
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
