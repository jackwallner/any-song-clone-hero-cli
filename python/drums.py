#!/usr/bin/env python3
"""Drum chart generation for Clone Hero.

Separates the drum stem with Demucs, detects hits per frequency band on that
stem, classifies them (kick, snare, toms, hi-hat, ride, crash), quantizes them
to the song's beat grid and builds four pro-drums difficulties.

Usage: drums.py <audio_file> <analysis.json> [--stem <drums.wav>]
Prints {"difficulties": {"ExpertDrums": [...], ...}, "stats": {...}} as JSON.
Notes use the same constant-tempo tick math as analyze.py, so they line up
with the guitar track in the same chart.
"""

import sys, json, os, warnings
import numpy as np
warnings.filterwarnings("ignore")

import librosa
from scipy.ndimage import maximum_filter1d

from drum_plan import Grid, time_to_tick, plan_and_render

# .chart drum lanes (4-lane pro drums) and the cymbal markers that turn a
# yellow/blue/green pad note into a cymbal
KICK, RED, YELLOW, BLUE, GREEN = 0, 1, 2, 3, 4
CYMBAL_FLAG = {YELLOW: 66, BLUE: 67, GREEN: 68}

# What each detected drum becomes on the highway
LANE_OF = {
    "kick": (KICK, False),
    "snare": (RED, False),
    "hihat": (YELLOW, True),
    "ride": (BLUE, True),
    "crash": (GREEN, True),
    "tom_hi": (YELLOW, False),
    "tom_mid": (BLUE, False),
    "tom_lo": (GREEN, False),
}

HOP = 256
N_FFT = 2048


# ─── Separation ──────────────────────────────────────────────────────────────

def _bass_path(stem_path):
    return stem_path[:-4] + "_bass.wav" if stem_path else None


def load_bass(stem_path):
    """Mono bass stem saved next to the drum stem, or None."""
    import soundfile as sf
    p = _bass_path(stem_path)
    if p and os.path.exists(p):
        y, _ = sf.read(p, always_2d=True)
        return y.mean(axis=1).astype(np.float32)
    return None


def separate_drums(audio_path, stem_path=None):
    """Return (mono drum stem, sample rate). Reuses stem_path if it exists.
    The bass stem is saved beside it (see load_bass): Demucs files the sub of
    an electronic 808/909 kick under bass, so kick detection needs both."""
    import soundfile as sf
    if stem_path and os.path.exists(stem_path) and (os.path.exists(_bass_path(stem_path)) or not audio_path):
        y, sr = sf.read(stem_path, always_2d=True)
        return y.mean(axis=1).astype(np.float32), sr

    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    model = get_model("htdemucs")
    model.eval()
    sr = model.samplerate
    y, _ = librosa.load(audio_path, sr=sr, mono=False)
    if y.ndim == 1:
        y = np.stack([y, y])
    wav = torch.tensor(y, dtype=torch.float32)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    wav = (wav - mean) / std

    devices = (["mps"] if torch.backends.mps.is_available() else []) + ["cpu"]
    if torch.cuda.is_available():
        devices.insert(0, "cuda")
    stems = None
    for dev in devices:
        try:
            with torch.no_grad():
                stems = apply_model(model, wav[None], device=dev, split=True,
                                    overlap=0.25, progress=False)[0]
            break
        except Exception as e:
            print(f"  Demucs on {dev} failed ({e}), trying next device", file=sys.stderr)
    if stems is None:
        raise RuntimeError("Demucs separation failed on every device")

    drums = (stems[model.sources.index("drums")] * std + mean).cpu().numpy()
    bass = (stems[model.sources.index("bass")] * std + mean).cpu().numpy()
    if stem_path:
        sf.write(stem_path, drums.T, sr)
        sf.write(_bass_path(stem_path), bass.T, sr)
    return drums.mean(axis=0).astype(np.float32), sr


# ─── Detection ───────────────────────────────────────────────────────────────

def _band(S, freqs, lo, hi):
    idx = np.where((freqs >= lo) & (freqs < hi))[0]
    return S[idx]


def _flux(Sb):
    """Log-magnitude spectral flux of one band, normalized to ~[0, 1]."""
    L = np.log1p(100.0 * Sb)
    d = np.maximum(0.0, np.diff(L, axis=1, prepend=L[:, :1]))
    env = d.sum(axis=0)
    env = np.convolve(env, np.hanning(5) / np.hanning(5).sum(), mode="same")
    scale = np.percentile(env, 99.5) + 1e-9
    return np.clip(env / scale, 0, 1.5)


def _peaks(env, sr, delta, wait_s):
    wait = max(1, int(wait_s * sr / HOP))
    return librosa.util.peak_pick(env, pre_max=3, post_max=3, pre_avg=25, post_avg=25,
                                  delta=delta, wait=wait)


def _near(frames, f, tol):
    if len(frames) == 0:
        return False
    i = np.searchsorted(frames, f)
    for j in (i - 1, i):
        if 0 <= j < len(frames) and abs(frames[j] - f) <= tol:
            return True
    return False


def detect_hits(y, sr, y_bass=None):
    """Detect drum hits on a separated drum stem (plus the bass stem, when
    given, for the kick). Returns a list of {"time", "kind", "strength"}
    sorted by time."""
    S = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    frame_s = HOP / sr
    tol = max(1, int(0.03 / frame_s))  # 30 ms coincidence window

    # Kick band: drums + bass, keeping only the percussive part so sustained
    # bass notes do not count. Electronic kicks put their sub in the bass stem.
    if y_bass is not None and len(y_bass) == len(y):
        L = np.abs(librosa.stft(y + y_bass, n_fft=N_FFT, hop_length=HOP))
        low_rows = (freqs >= 30) & (freqs < 120)
        _, perc = librosa.decompose.hpss(L[low_rows], margin=2.0)
        S_kick = np.zeros_like(S)
        S_kick[low_rows] = perc
    else:
        S_kick = S

    # Gate out near-silent frames so separation bleed does not become notes
    rms = librosa.feature.rms(S=S, frame_length=N_FFT)[0]
    gate = rms > (np.percentile(rms, 95) * 0.04)

    kick_env = _flux(_band(S_kick, freqs, 30, 120))
    snare_env = _flux(_band(S, freqs, 1500, 5000))
    cym_env = _flux(_band(S, freqs, 7000, 16000))
    tom_env = _flux(_band(S, freqs, 70, 350))

    kick_f = [f for f in _peaks(kick_env, sr, 0.08, 0.07) if gate[f]]
    snare_f = [f for f in _peaks(snare_env, sr, 0.08, 0.07) if gate[f]]
    cym_f = [f for f in _peaks(cym_env, sr, 0.10, 0.06) if gate[f]]
    tom_f = [f for f in _peaks(tom_env, sr, 0.18, 0.08) if gate[f]]

    hi_energy = _band(S, freqs, 7000, 16000).sum(axis=0)
    low_energy = _band(S, freqs, 70, 350)
    low_freqs = freqs[(freqs >= 70) & (freqs < 350)]

    # Band energy at a hit relative to the loudest moment within +-4 s. A flux
    # peak alone also fires on bleed and on other drums' transients; a real
    # hit is loud in its own band compared with the surrounding passage.
    # Thresholds tuned against human-charted songs (kick F1 .59 -> .75,
    # snare .49 -> .84 on a live-drummer recording; the snare bar was then raised
    # to 0.4 to reject distorted-guitar bleed, which Demucs leaves in the stem).
    win = int(8.0 / frame_s)
    def rel_level(lo, hi, spec=S):
        e = (_band(spec, freqs, lo, hi) ** 2).sum(axis=0)
        peak = np.array([e[f:f + 4].max() for f in range(len(e))])
        return peak / (maximum_filter1d(e, size=win) + 1e-12)
    kick_lvl = rel_level(30, 120, S_kick)
    body_lvl = rel_level(150, 400)   # snare shell; kick clicks and hats lack it
    noise_lvl = rel_level(1500, 5000)

    hits = []
    kick_kept = [f for f in kick_f if kick_lvl[f] > 0.12]
    for f in kick_kept:
        hits.append({"frame": f, "kind": "kick", "strength": float(kick_lvl[f])})
    kick_arr = np.array(kick_kept)

    # A "snare" on a kick is often the kick's own click and body (electronic
    # kicks especially). A real snare rattles on: its noise band still rings
    # 80 ms later (backbeat snares measured 0.11-0.5 of the peak, kick clicks
    # ~0.0). Short hits away from any kick stay, so a lone clap still counts.
    noise_e = (_band(S, freqs, 1500, 5000) ** 2).sum(axis=0)
    ring = int(0.08 / frame_s)
    near_kick = max(1, int(0.06 / frame_s))
    def rattles(f):
        return noise_e[min(f + ring, len(noise_e) - 1)] >= 0.06 * (noise_e[f:f + 4].max() + 1e-12)
    snare_kept = [f for f in snare_f if body_lvl[f] > 0.4 and noise_lvl[f] > 0.02
                  and (rattles(f) or not _near(kick_arr, f, near_kick))]
    for f in snare_kept:
        hits.append({"frame": f, "kind": "snare", "strength": float(body_lvl[f])})
    snare_arr = np.array(snare_kept)

    # Cymbals: classify by how long the high band rings after the hit
    decay_frames = int(0.35 / frame_s)
    for f in cym_f:
        peak = hi_energy[f:f + 3].max() + 1e-9
        tail = hi_energy[min(f + decay_frames, len(hi_energy) - 1)]
        ratio = tail / peak
        strength = float(cym_env[f])
        if ratio > 0.45 and strength > 0.45:
            kind = "crash"
        elif ratio > 0.30:
            kind = "ride"
        else:
            kind = "hihat"
        hits.append({"frame": f, "kind": kind, "strength": strength, "decay": float(ratio)})

    # Toms: loud, ringing low-mid hits away from any kick/snare candidate.
    # Kept conservative — a wrong tom is worse to play than a missing one.
    tom_lvl = rel_level(70, 350)
    tom_e = low_energy.sum(axis=0)
    ring = int(0.08 / frame_s)
    kick_any, snare_any = np.array(kick_f), np.array(snare_f)
    tom_hits = []
    for f in tom_f:
        if _near(kick_any, f, tol) or _near(snare_any, f, tol):
            continue
        if tom_lvl[f] < 0.35:
            continue
        if tom_e[min(f + ring, len(tom_e) - 1)] < 0.3 * tom_e[f:f + 4].max():
            continue
        spec = low_energy[:, f:f + int(0.1 / frame_s)].mean(axis=1)
        pitch = float(low_freqs[int(np.argmax(spec))])
        tom_hits.append((f, pitch, float(tom_env[f])))
    if tom_hits:
        pitches = np.array([p for _, p, _ in tom_hits])
        if len(tom_hits) >= 6 and pitches.max() / max(pitches.min(), 1) > 1.25:
            lo_cut, hi_cut = np.percentile(pitches, [33, 67])
        else:
            lo_cut, hi_cut = -1, 1e9  # too few or too similar: one tom lane
        for f, p, st in tom_hits:
            kind = "tom_hi" if p > hi_cut else ("tom_lo" if p < lo_cut else "tom_mid")
            hits.append({"frame": f, "kind": kind, "strength": st})

    for h in hits:
        h["time"] = float(h.pop("frame") * frame_s)
    hits.sort(key=lambda h: h["time"])

    # Ride vs hi-hat is a per-passage decision in real playing: a groove sits
    # on one or the other. Smooth isolated flips by majority over neighbors.
    cyms = [h for h in hits if h["kind"] in ("hihat", "ride")]
    kinds = [h["kind"] for h in cyms]
    for i, h in enumerate(cyms):
        window = kinds[max(0, i - 4):i + 5]
        h["kind"] = "ride" if window.count("ride") > len(window) / 2 else "hihat"
    return hits


# ─── Quantization & difficulties ─────────────────────────────────────────────

def quantize(grid, t):
    """Snap to the nearest 16th of the beat grid, or to an 8th-note triplet
    when that fits much better. Returns the snapped time."""
    b = grid.beat_pos(t)
    q16 = round(b * 4) / 4
    q12 = round(b * 3) / 3
    best = q12 if abs(b - q12) < 0.5 * abs(b - q16) else q16
    return grid.beat_time(best)


PAD_PRIORITY = ["snare", "crash", "hihat", "ride", "tom_hi", "tom_mid", "tom_lo"]


def build_expert(hits, tempo_bpm, beat_times, duration):
    """Expert: every detected hit on its grid slot (16ths or triplets), at most
    two pads plus kick at once."""
    grid = Grid(tempo_bpm, beat_times, duration)

    slots = {}
    for h in hits:
        if h["time"] < 0 or h["time"] > duration:
            continue
        qt = quantize(grid, h["time"])
        if qt < 0:
            continue
        tick = time_to_tick(qt, tempo_bpm)
        kinds = slots.setdefault(tick, {})
        kinds[h["kind"]] = max(kinds.get(h["kind"], 0.0), h["strength"])

    out = []
    for tick in sorted(slots):
        kinds = slots[tick]
        pads = sorted((k for k in kinds if k != "kick"), key=PAD_PRIORITY.index)
        used, chosen = set(), []
        for k in pads:
            lane, _ = LANE_OF[k]
            if lane in used:
                continue
            used.add(lane)
            chosen.append(k)
            if len(chosen) >= 2:
                break
        if "kick" in kinds:
            chosen.append("kick")
        for k in chosen:
            lane, cymbal = LANE_OF[k]
            out.append({"tick": tick, "lane": lane, "length": 0})
            if cymbal:
                out.append({"tick": tick, "lane": CYMBAL_FLAG[lane], "length": 0})
    return out


def build_difficulties(hits, tempo_bpm, beat_times, duration, y, sr, sections=None,
                       ai_provider=None, api_key="", song=""):
    """Expert from the raw hits; Hard/Medium/Easy planned per section."""
    planned, info = plan_and_render(hits, y, sr, tempo_bpm, beat_times, sections,
                                    duration, ai_provider, api_key, song)
    return {"ExpertDrums": build_expert(hits, tempo_bpm, beat_times, duration), **planned}, info


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: drums.py <audio_file> <analysis.json> [--stem <drums.wav>] [--ai gemini|claude|codex]"}))
        sys.exit(1)
    audio_path, analysis_path = sys.argv[1], sys.argv[2]
    stem_path = None
    if "--stem" in sys.argv:
        i = sys.argv.index("--stem")
        stem_path = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    ai_provider = None
    if "--ai" in sys.argv:
        i = sys.argv.index("--ai")
        ai_provider = sys.argv[i + 1] if i + 1 < len(sys.argv) else None

    with open(analysis_path) as f:
        analysis = json.load(f)
    tempo = analysis["tempo"] / 1000.0
    duration = analysis.get("duration_ms", 0) / 1000.0

    print("  Separating drum stem (Demucs)...", file=sys.stderr)
    try:
        y, sr = separate_drums(audio_path, stem_path)
        y_bass = load_bass(stem_path)
    except ImportError:
        print(json.dumps({"error": "Demucs is not installed (pip install demucs in the SongHero venv)"}))
        return
    print("  Detecting drum hits...", file=sys.stderr)
    duration = duration or len(y) / sr
    hits = detect_hits(y, sr, y_bass)
    song = " - ".join(x for x in (os.environ.get("SONG_ARTIST"), os.environ.get("SONG_NAME")) if x)
    diffs, plan = build_difficulties(hits, tempo, analysis.get("beat_times", []), duration, y, sr,
                                     analysis.get("sections", []), ai_provider,
                                     os.environ.get("GEMINI_API_KEY", ""), song)

    counts = {}
    for h in hits:
        counts[h["kind"]] = counts.get(h["kind"], 0) + 1
    print(json.dumps({"difficulties": diffs, "stats": {"hits": counts, "planner": plan.get("planner")},
                      "plan": plan}))


if __name__ == "__main__":
    main()
