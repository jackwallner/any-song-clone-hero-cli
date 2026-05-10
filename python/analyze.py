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

def analyze_audio(filepath, gemini_key=None, metadata=None):
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
    lyrics = None
    if gemini_key:
        try:
            ai_suggestions = get_gemini_analysis(gemini_key, tempo, estimated_key, 
                                                  sections, duration, onset_notes,
                                                  metadata.get("name", ""), metadata.get("artist", ""))
        except Exception as e:
            print(f"  Gemini analysis failed (continuing without): {e}", file=sys.stderr)
        
        # Get lyrics from Gemini too
        try:
            lyrics = get_gemini_lyrics(gemini_key, metadata.get("name", ""), 
                                        metadata.get("artist", ""), tempo, sections, duration)
        except Exception as e:
            print(f"  Gemini lyrics failed (continuing without): {e}", file=sys.stderr)
    
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
        "lyrics": lyrics if lyrics else [],
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
    """Generate notes per difficulty using independent grid selection + AI section patterns."""
    
    beat_interval = 60.0 / tempo
    res = RESOLUTION
    use_16th = tempo > 140
    base_spacing = beat_interval / (4 if use_16th else 2)
    steps_per_beat = 4 if use_16th else 2
    
    grid_times, is_beat_pos, grid_has_onset = [], [], []
    t = 0.0
    while t < duration + base_spacing:
        grid_times.append(t)
        is_beat_pos.append(len(is_beat_pos) % steps_per_beat == 0)
        grid_has_onset.append(False)
        t += base_spacing
    
    for o in onset_notes:
        idx = int(o["time"] / base_spacing)
        if 0 <= idx < len(grid_times):
            grid_has_onset[idx] = True
    
    def fret_at(time):
        d_best, f_best = 99, 0
        for o in onset_notes:
            d = abs(o["time"] - time)
            if d < d_best:
                d_best, f_best = d, fret_map.get(o["pitch_class"], 0)
        return f_best if d_best < 0.15 else None
    
    # AI intelligence
    style = ai_suggestions.get("style", "pop") if ai_suggestions else "pop"
    fw = ai_suggestions.get("fret_emphasis", [5,5,5,5,3]) if ai_suggestions else [5,5,5,5,3]
    if isinstance(fw, list) and len(fw) >= 5:
        fw = [float(x)*10 for x in fw[:5]]
    else:
        fw = [5,5,5,5,3]
    sp = ai_suggestions.get("sections", {}) if ai_suggestions else {}
    
    w_sum = sum(fw)
    fret_probs = [w/w_sum for w in fw]
    def wf(): return int(np.random.choice(5, p=fret_probs))
    
    pcr = {"single_note":0.05,"chords":0.45,"arpeggios":0.10,"power_chords":0.55,"strumming":0.40}
    psr = {"single_note":0.20,"chords":0.12,"arpeggios":0.15,"power_chords":0.05,"strumming":0.08}
    
    sec_map = []
    for sec in sections:
        label = sec.get("label","")
        s = sp.get(label, {})
        p = s.get("pattern","single_note") if isinstance(s,dict) else "single_note"
        i = float(s.get("intensity",5)) if isinstance(s,dict) else 5
        sec_map.append((sec["start"], sec["end"], p, i))
    
    def sec_at(t):
        for st,en,p,i in sec_map:
            if st <= t < en: return p, i
        return "single_note", 5
    
    difficulties = {}
    
    # Expert: all grid positions, heavy chords, orange allowed
    exp = []
    for i, time in enumerate(grid_times):
        if time > duration: break
        pat, its = sec_at(time)
        cr = pcr.get(pat, 0.2) * (its/5.0) * 1.5
        f = fret_at(time) if grid_has_onset[i] else None
        fret = min(f, 4) if f is not None else wf()
        tick = time_to_tick(time, tempo)
        exp.append({"tick":tick,"fret":fret,"length":0})
        if np.random.random() < cr:
            exp.append({"tick":tick,"fret":(fret+2)%5,"length":0})
    exp.sort(key=lambda n:(n["tick"],n["fret"]))
    difficulties["ExpertSingle"] = exp
    
    # Hard: beats + every 2nd subdivision, orange, moderate chords
    hrd = []
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_beat_pos[i] and i%2 != 0: continue
        pat, its = sec_at(time)
        cr = pcr.get(pat, 0.2) * (its/5.0)
        f = fret_at(time) if grid_has_onset[i] else None
        fret = min(f, 4) if f is not None else wf()
        tick = time_to_tick(time, tempo)
        hrd.append({"tick":tick,"fret":fret,"length":0})
        if is_beat_pos[i] and np.random.random() < cr:
            hrd.append({"tick":tick,"fret":(fret+2)%5,"length":0})
    hrd.sort(key=lambda n:(n["tick"],n["fret"]))
    difficulties["HardSingle"] = hrd
    
    # Medium: beats only, no orange, some sustains, medium chords
    med = []
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_beat_pos[i]: continue
        pat, its = sec_at(time)
        cr = pcr.get(pat, 0.1) * (its/5.0) * 0.8
        sr = psr.get(pat, 0.1)
        f = fret_at(time) if grid_has_onset[i] else None
        fret = min(f, 3) if f is not None else min(wf(), 3)
        tick = time_to_tick(time, tempo)
        length = int(beat_interval*1.5*res*tempo/60.0) if np.random.random()<sr else 0
        med.append({"tick":tick,"fret":fret,"length":length})
        if np.random.random() < cr:
            med.append({"tick":tick,"fret":min((fret+2)%5,3),"length":0})
    med.sort(key=lambda n:(n["tick"],n["fret"]))
    difficulties["MediumSingle"] = med
    
    # Easy: every other beat, heavy sustains, minimal chords
    easy = []
    bi = 0
    for i, time in enumerate(grid_times):
        if time > duration: break
        if not is_beat_pos[i]: continue
        bi += 1
        if bi % 2 != 0: continue
        pat, its = sec_at(time)
        sr = psr.get(pat, 0.1) * 2.0
        f = fret_at(time) if grid_has_onset[i] else None
        fret = min(f, 3) if f is not None else min(wf(), 3)
        tick = time_to_tick(time, tempo)
        length = int(beat_interval*2.0*res*tempo/60.0) if np.random.random()<sr else 0
        easy.append({"tick":tick,"fret":fret,"length":length})
        if np.random.random() < 0.03:
            easy.append({"tick":tick,"fret":min((fret+1)%5,3),"length":0})
    easy.sort(key=lambda n:(n["tick"],n["fret"]))
    difficulties["EasySingle"] = easy
    
    return difficulties

def get_gemini_lyrics(api_key, song_name, artist, tempo, sections, duration):
    """Get synced lyrics from Gemini, aligned to section boundaries."""
    import urllib.request, time
    
    if not song_name:
        return []
    
    sec_json = json.dumps([{
        "start": round(s["start"], 1), 
        "end": round(s["end"], 1), 
        "label": s["label"]
    } for s in sections[:10]])
    
    prompt = f"""Write the full lyrics for "{song_name}" by {artist}. 
Include ALL verses, choruses, bridges. Distribute lyrics across these sections:
{sec_json}

Return ONLY a JSON array of lyric events: [{{"time":seconds,"word":"word"}},...].
Each word/phrase at its approximate time. Include ALL lyrics.
JSON only, no markdown."""

    req_data = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096}
    }).encode()
    
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
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read())
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                text = text.strip()
                
                # Extract JSON array
                start = text.find('[')
                end = text.rfind(']')
                if start >= 0 and end > start:
                    text = text[start:end+1]
                
                if text.startswith("```"):
                    first_nl = text.find('\n')
                    text = text[first_nl+1:] if first_nl > 0 else text[3:]
                if text.rstrip().endswith("```"):
                    text = text.rstrip()[:-3].strip()
                
                lyrics = json.loads(text)
                # Convert to events with tick
                lyric_events = []
                for item in lyrics:
                    if isinstance(item, dict) and "time" in item and "word" in item:
                        t = float(item["time"])
                        tick = time_to_tick(t, tempo)
                        word = str(item["word"]).replace('"', "'")
                        lyric_events.append({"tick": tick, "word": word})
                lyric_events.sort(key=lambda x: x["tick"])
                return lyric_events
            except Exception:
                if attempt < 1:
                    time.sleep(2)
                else:
                    break
    
    return []


def get_gemini_analysis(api_key, tempo, key, sections, duration, onset_notes, song_name="", artist=""):
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
    
    prompt = f"""You are an expert Clone Hero charter. Design the Medium difficulty guitar chart for this song.

Song: "{song_name}" by {artist}
Detected: {tempo:.0f} BPM, key PC={key}, {duration:.0f}s
Section timestamps: {sec_json}

Based on your knowledge of this song, return EXACTLY this JSON:

{{
  "style": "pop|rock|punk|ballad|metal",
  "fret_emphasis": [green_weight, red_weight, yellow_weight, blue_weight, orange_weight],
  "sections": {{
    "label_name": {{"pattern": "single_note|chords|power_chords|strumming|arpeggios", "intensity": 1-10}},
    ...
  }},
  "charter_note": "one sentence of musical direction for the Medium charter"
}}

RULES:
- fret_emphasis: 5 floats that sum to 1.0. Higher = more notes on that color. orange=0 for Medium.
- pattern: match what the guitar actually plays in this section
- intensity: 1=very sparse notes, 5=normal, 10=wall of notes
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
                if "fret_emphasis" not in data:
                    valid = False
                elif isinstance(data["fret_emphasis"], list):
                    fw = data["fret_emphasis"]
                    # Normalize to sum to 1.0
                    total = sum(float(x) for x in fw[:5])
                    if total > 0:
                        data["fret_emphasis"] = [float(x)/total for x in fw[:5]]
                if "sections" not in data:
                    valid = False
                if not valid:
                    continue  # Try next model
                
                print(f"  AI: style={data.get('style','?')} sections={len(data.get('sections',{}))} patterns", file=sys.stderr)
                if data.get("charter_note"):
                    print(f"  AI note: {data['charter_note'][:120]}", file=sys.stderr)
                
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
        print(json.dumps({"error": "Usage: analyze.py <audio_file> [--gemini]"}))
        sys.exit(1)
    
    audio_file = sys.argv[1]
    use_gemini = "--gemini" in sys.argv
    
    if not os.path.exists(audio_file):
        print(json.dumps({"error": f"File not found: {audio_file}"}))
        sys.exit(1)
    
    key = GEMINI_KEY if use_gemini else None
    # Build metadata from env vars if available
    metadata = {
        "name": os.environ.get("SONG_NAME", ""),
        "artist": os.environ.get("SONG_ARTIST", ""),
    }
    result = analyze_audio(audio_file, gemini_key=key, metadata=metadata)
    print(json.dumps(result))
