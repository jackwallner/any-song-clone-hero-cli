"""Plan the reduced drum difficulties like a drum teacher would.

Raw hit detection is noisy: a missed hit becomes a gap, a misclassified cymbal
becomes a lane jump, and no two bars look alike. That is playable at Expert
(it is what the drummer played), but it is hard to learn from. So Hard, Medium
and Easy are not filtered copies of the detected hits. Instead:

1. Bars and sections: find the downbeat (snare on 2 and 4, kick on 1), which
   bars have drums, and map the song sections onto whole bars.
2. Groove per section: for every 16th of the bar, how often each drum is hit
   across the section's bars; which bars are fills; one cymbal for the section.
3. Plan per section (optionally revised by an LLM acting as drum teacher),
   then validated against playability rules and the audio evidence.
4. Render: the section's groove repeats in every bar where drums play, with
   the real variations and fills Hard players train on, and simple fills for
   Medium.

Difficulty intent:
  Hard   - players who can play a kit and are training: the song's real groove
           at 16th resolution, both hands, real fills, one cymbal per section.
  Medium - drum learners: a steady cymbal pulse, backbeat snare, kick on the
           strong beats, one hand at a time, the same groove every bar, short
           fills on the last beats of a phrase.
  Easy   - first steps: one drum at a time on the quarter notes.
"""

import sys, json
import numpy as np

RESOLUTION = 480
KICK, RED, YELLOW, BLUE, GREEN = 0, 1, 2, 3, 4
CYMBAL_FLAG = {YELLOW: 66, BLUE: 67, GREEN: 68}
CYM_LANE = {"hihat": YELLOW, "ride": BLUE, "crash": GREEN}
TOM_LANE = {"tom_hi": YELLOW, "tom_mid": BLUE, "tom_lo": GREEN}
CYMBALS = ("hihat", "ride", "crash")
TOMS = tuple(TOM_LANE)

# Fastest cymbal pulse a learner should keep up with, in hits per second:
# 8th notes up to ~100 BPM, quarter notes above that
MEDIUM_MAX_PULSE_RATE = 3.4
# ...and all Medium hits together (kick, snare and cymbal onsets), so a kick on
# every beat plus "&" hi-hats does not become a constant 8th-note stream
MEDIUM_MAX_ONSET_RATE = 3.4
EASY_MAX_PULSE_RATE = 3.0
# Adjacent sections whose grooves are at least this similar are one drum section
GROOVE_MERGE_SIMILARITY = 0.8


def time_to_tick(t, bpm):
    return int(round(t * RESOLUTION * bpm / 60.0))


# ─── 1. Bars and sections ────────────────────────────────────────────────────

def regular_beats(beat_times, period, duration):
    """Tracked beats made regular: doubled beats dropped, missed beats filled
    in, and extended at the song's period to cover 0..duration."""
    bt = sorted(float(t) for t in beat_times)
    clean = []
    for t in bt:
        if clean and t - clean[-1] < 0.5 * period:
            continue
        while clean and t - clean[-1] > 1.5 * period:
            n = int(round((t - clean[-1]) / period))
            step = (t - clean[-1]) / n
            clean.append(clean[-1] + step)
        clean.append(t)
    if len(clean) < 2:
        clean = [0.0, period]
    while clean[0] > 0:
        clean.insert(0, clean[0] - period)
    while clean[-1] < duration + 2 * period:
        clean.append(clean[-1] + period)
    return np.array(clean)


class Grid:
    """16th-note grid that follows the tracked beats, so bars stay aligned with
    the music even when a live band drifts. Notes are still written with the
    chart's single tempo, so they land at the right time in the audio."""

    def __init__(self, tempo, beat_times, duration):
        self.tempo = tempo
        self.period = 60.0 / tempo
        self.beats = regular_beats(beat_times, self.period, duration)
        self.downbeat = 0  # beat index (0-3) that starts a bar, set later

    def beat_pos(self, t):
        """Fractional beat index of time t."""
        B = self.beats
        i = int(np.clip(np.searchsorted(B, t, side="right") - 1, 0, len(B) - 2))
        return i + (t - B[i]) / (B[i + 1] - B[i])

    def beat_time(self, x):
        """Time of fractional beat index x."""
        B = self.beats
        i = int(np.clip(np.floor(x), 0, len(B) - 2))
        return B[i] + (x - i) * (B[i + 1] - B[i])

    def slot(self, t):
        return int(round(self.beat_pos(t) * 4))

    def slot_time(self, g):
        return self.beat_time(g / 4)

    def tick(self, g):
        return time_to_tick(self.slot_time(g), self.tempo)

    def bar_of(self, g):
        return (g // 4 - self.downbeat) // 4

    def bar_start(self, bar):
        return (bar * 4 + self.downbeat) * 4


def slot_table(hits, grid):
    """{16th slot: {drum kind: strongest hit}} from the detected hits."""
    table = {}
    for h in hits:
        g = grid.slot(h["time"])
        if g < 0:
            continue
        kinds = table.setdefault(g, {})
        kinds[h["kind"]] = max(kinds.get(h["kind"], 0.0), h["strength"])
    return table


def find_downbeat(table):
    """Pick which of the 4 beats starts the bar: snare on 2 and 4, kick and
    crash on 1."""
    best, best_score = 0, -1e9
    for o in range(4):
        score = 0.0
        for g, kinds in table.items():
            if g % 4:
                continue
            rb = (g // 4 - o) % 4
            if "snare" in kinds:
                score += 1.0 if rb in (1, 3) else -0.5
            if "kick" in kinds and rb == 0:
                score += 0.5
            if "crash" in kinds and rb == 0:
                score += 0.5
        if score > best_score:
            best, best_score = o, score
    return best


def beat_activity(y, sr, grid, n_beats):
    """Per beat: is the drum stem actually playing? A beat counts as active when
    its RMS reaches 15% of the typical level of the song's drummed beats."""
    rms = np.zeros(n_beats)
    for b in range(n_beats):
        a = int(grid.beat_time(b) * sr)
        z = int(grid.beat_time(b + 1) * sr)
        seg = y[max(0, a):max(0, z)]
        rms[b] = np.sqrt(np.mean(seg ** 2)) if len(seg) else 0.0
    loud = rms[rms > np.percentile(rms, 30)] if n_beats else rms
    ref = np.median(loud) if len(loud) else 0.0
    return rms > 0.15 * ref


def section_bars(sections, grid, n_bars):
    """Map analysis sections (seconds) onto bar ranges covering the song."""
    bar_len = grid.period * 4
    t0 = grid.slot_time(grid.bar_start(0))
    starts = sorted({max(0, int(round((s["start"] - t0) / bar_len))) for s in sections} | {0})
    labels = {}
    for s in sections:
        labels.setdefault(max(0, int(round((s["start"] - t0) / bar_len))), s.get("label", "section"))
    out = []
    for i, a in enumerate(starts):
        z = starts[i + 1] if i + 1 < len(starts) else n_bars
        if z <= a:
            continue
        if out and z - a < 2:  # too short for a groove: merge into the previous one
            out[-1]["end"] = z
            continue
        out.append({"start": a, "end": z, "label": labels.get(a, "section")})
    # Long sections get phrases of 8 bars so a groove change inside is caught
    phrased = []
    for sec in out:
        a = sec["start"]
        while sec["end"] - a > 16:
            phrased.append({"start": a, "end": a + 8, "label": sec["label"]})
            a += 8
        phrased.append({"start": a, "end": sec["end"], "label": sec["label"]})
    return phrased


# ─── 2. Groove analysis ──────────────────────────────────────────────────────

def bar_pattern(table, grid, bar):
    """{slot in bar 0-15: {kind: strength}} for one bar."""
    g0 = grid.bar_start(bar)
    return {s: table[g0 + s] for s in range(16) if g0 + s in table}


def analyze_section(sec, table, grid, active_bars):
    bars = [b for b in range(sec["start"], sec["end"]) if active_bars.get(b)]
    sec["active"] = bars
    if not bars:
        return sec
    pats = {b: bar_pattern(table, grid, b) for b in bars}

    def freq(bar_list):
        f = {k: np.zeros(16) for k in ("kick", "snare", "cym", "crash", "tom")}
        for b in bar_list:
            for s, kinds in pats[b].items():
                fams = {"cym" if k in ("hihat", "ride") else "tom" if k in TOMS else k
                        for k in kinds}
                for fam in fams:
                    f[fam][s] += 1
        n = max(1, len(bar_list))
        return {k: v / n for k, v in f.items()}

    # Fill bars: snare/tom hits the groove does not have, bunched late in the bar
    f_all = freq(bars)
    fills = []
    for b in bars:
        extra = sum(1 for s, kinds in pats[b].items()
                    if s >= 8 and (("snare" in kinds and f_all["snare"][s] < 0.5) or
                                   any(k in TOMS for k in kinds)))
        if extra >= 2:
            fills.append(b)
    groove_bars = [b for b in bars if b not in fills] or bars
    f = freq(groove_bars)
    sec["freq"] = {k: [round(float(x), 2) for x in v] for k, v in f.items()}
    sec["fills"] = fills

    counts = {k: 0 for k in CYMBALS}
    for b in groove_bars:
        for kinds in pats[b].values():
            for k in CYMBALS:
                if k in kinds:
                    counts[k] += 1
    total = sum(counts.values())
    if total and counts["crash"] > 0.6 * total:
        sec["cymbal"] = "crash"  # crash-riding, typical of loud choruses
    else:
        sec["cymbal"] = "ride" if counts["ride"] > counts["hihat"] else "hihat"

    # Did the drummer hit a crash to open this section?
    first = grid.bar_start(bars[0])
    sec["opens_with_crash"] = any("crash" in table.get(g, {}) for g in (first - 1, first, first + 1))
    sec["tom_fills"] = any(any(k in TOMS for k in pats[b].get(s, {})) for b in fills for s in range(16))
    return sec


def _groove_vector(sec):
    return np.concatenate([np.array(sec["freq"][k]) for k in ("kick", "snare", "cym")])


def _same_groove(a, b):
    if not a.get("active") and not b.get("active"):
        return True  # two drumless stretches
    if not a.get("freq") or not b.get("freq"):
        return False
    if a["cymbal"] != b["cymbal"]:
        return False  # hi-hat verse into ride chorus is a real change
    va, vb = _groove_vector(a), _groove_vector(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return denom > 0 and float(va @ vb) / denom >= GROOVE_MERGE_SIMILARITY


def merge_same_grooves(secs, table, grid, active_bars):
    """Song sections come from the guitar analysis and are often 4 bars long.
    Drums change less often: merge neighbours that share a groove, so fills and
    crashes mark real changes instead of every fourth bar."""
    merged = []
    for sec in secs:
        if merged and _same_groove(merged[-1], sec):
            prev = merged[-1]
            merged[-1] = analyze_section({"start": prev["start"], "end": sec["end"],
                                          "label": prev["label"]}, table, grid, active_bars)
        else:
            merged.append(sec)
    return merged


# ─── 3. Plan ─────────────────────────────────────────────────────────────────

def _pattern(slots, n=16):
    return "".join("x" if i in slots else "." for i in range(n))


def _slots(pattern):
    return {i for i, c in enumerate(pattern) if c == "x"}


def _snap8(s):
    """16th slot -> nearest 8th slot in 8-slot space (ties go earlier)."""
    return min(7, (s + 1) // 2) if s % 2 else s // 2


def default_plan(sec, tempo):
    """Deterministic plan for one section from its groove statistics."""
    f = {k: np.array(v) for k, v in sec["freq"].items()}
    kick16 = {s for s in range(16) if f["kick"][s] >= 0.45}
    snare16 = {s for s in range(16) if f["snare"][s] >= 0.45}
    cym16 = {s for s in range(16) if f["cym"][s] >= 0.4 or
             (sec["cymbal"] == "crash" and f["crash"][s] >= 0.4)}
    # Backbeat the detector only caught part of the time
    if not snare16:
        snare16 = {s for s in (4, 12) if f["snare"][s] >= 0.25}
    if not kick16 and f["kick"][0] >= 0.25:
        kick16 = {0}

    # Medium: 8th-note grid, kicks on the strong 8ths, at most two snares
    def top8(fam, limit, prefer):
        score = {}
        for s in range(16):
            if f[fam][s] > 0:
                e = _snap8(s)
                score[e] = max(score.get(e, 0), f[fam][s] + (0.3 if e in prefer else 0))
        keep = sorted((e for e in score if score[e] >= 0.45), key=lambda e: -score[e])[:limit]
        return set(keep)

    snare8 = top8("snare", 2, prefer={2, 6})
    if not snare8 and snare16:
        snare8 = {_snap8(s) for s in snare16} & {2, 6} or {_snap8(min(snare16))}
    # Four on the floor is easy (one kick per beat), so it may keep all four
    four_floor = all(f["kick"][s] >= 0.45 for s in (0, 4, 8, 12))
    kick8 = {0, 2, 4, 6} if four_floor else top8("kick", 3, prefer={0, 4})
    if not kick8 and kick16:
        kick8 = {0}

    on = f["cym"][[0, 4, 8, 12]].mean()
    off = f["cym"][[2, 6, 10, 14]].mean()
    if off >= 0.3 and off > 1.5 * on:
        pulse = "offbeat"  # dance-style "&" hi-hat between the kicks
    elif tempo / 60 * 2 > MEDIUM_MAX_PULSE_RATE or off < 0.3 * max(1e-9, on):
        pulse = "quarter"
    else:
        pulse = "8th"

    return {
        "cymbal": sec["cymbal"],
        "medium": {"pulse": pulse, "kick": _pattern(kick8, 8), "snare": _pattern(snare8, 8),
                   "fill": "toms" if sec.get("tom_fills") else "snare"},
        "hard": {"kick": _pattern(kick16), "snare": _pattern(snare16), "cymbal": _pattern(cym16),
                 "variations": True},
    }


def _freq_line(v):
    return "".join(str(min(9, int(round(x * 9)))) for x in v)


def ai_revise(plans, sections, tempo, provider, api_key, song):
    """Let an LLM revise the plan as a drum teacher. Returns revised plans, or
    None to keep the deterministic ones."""
    from llm import ask_json

    lines = []
    for i, (sec, plan) in enumerate(zip(sections, plans)):
        if plan is None:
            lines.append(f"Section {i} ({sec['label']}, bars {sec['start']}-{sec['end']-1}): no drums")
            continue
        fr = sec["freq"]
        lines.append(
            f"Section {i} ({sec['label']}, bars {sec['start']}-{sec['end']-1}, "
            f"{len(sec['active'])} drummed bars, {len(sec['fills'])} fill bars, "
            f"cymbal={sec['cymbal']}, opens_with_crash={sec['opens_with_crash']}, tom_fills={sec['tom_fills']})\n"
            f"  how often each 16th is hit (0-9), slots 1 e & a 2 e & a 3 e & a 4 e & a:\n"
            f"    kick  {_freq_line(fr['kick'])}\n"
            f"    snare {_freq_line(fr['snare'])}\n"
            f"    cymbal{_freq_line(fr['cym'])}\n"
            f"    crash {_freq_line(fr['crash'])}\n"
            f"    toms  {_freq_line(fr['tom'])}\n"
            f"  proposal: {json.dumps(plan)}")
    prompt = f"""You are a drum teacher arranging a Clone Hero drum chart for "{song}".
Tempo {tempo:.1f} BPM, 4/4. The data below was measured from the isolated drum
track: for each section, how often each drum is hit on each 16th note across
the section's bars (9 = every bar). Use only this evidence, not memory of the song.

Write two reduced parts per section:

MEDIUM is for drum LEARNERS. They need: a steady cymbal pulse they can lock
onto (no gaps while the band plays), the backbeat snare, kick on strong beats,
ONE hand at a time, and the SAME groove every bar. Keep it clearly easier than
the real part. 8th-note grid (8 slots: 1 & 2 & 3 & 4 &).
  pulse: "8th", "quarter" or "offbeat" (cymbal on every "&", typical of dance
  music with a kick on every beat). Use quarter or offbeat if 8ths would be
  faster than about {MEDIUM_MAX_PULSE_RATE:.1f} hits/s; 8ths here are {tempo/60*2:.1f}/s.
  kick: 8 chars, at most 3 hits (4 only for a kick on every beat).
  snare: 8 chars, at most 2 hits.
  fill: "snare", "toms" or "none" (a two-hit fill on beats 3-4 of the bar
  before a section change)

HARD is for players who already play a kit and are TRAINING to get better:
the song's real groove at 16th resolution (16 slots: 1 e & a 2 e & a ...),
both hands, real fills and variations kept.
  kick, snare, cymbal: 16 chars each.  variations: true/false (keep the
  drummer's real bar-to-bar variations on top of the groove)

For both: "x" = hit, "." = rest. Only place kick/snare where the evidence
shows that drum is played in this section (value >= 2). cymbal: "hihat",
"ride" or "crash" — one per section, from the evidence. Sections that are
musically the same (same label, similar evidence) must get identical parts;
say so with "same_as": <earlier section index>.

SECTIONS:
{chr(10).join(lines)}

Return ONLY JSON:
{{"sections": {{"<index>": {{"same_as": null, "cymbal": "...", "medium": {{"pulse": "...", "kick": "........", "snare": "........", "fill": "..."}}, "hard": {{"kick": "................", "snare": "................", "cymbal": "................", "variations": true}}}}}}}}
Include every section that has drums."""

    def validate(data):
        secs = data.get("sections")
        if not isinstance(secs, dict):
            return None
        return data

    data = ask_json(provider, prompt, api_key, validate, max_tokens=8192)
    if data is None:
        return None
    revised = list(plans)
    for i, plan in enumerate(plans):
        if plan is None:
            continue
        got = data["sections"].get(str(i))
        if not isinstance(got, dict):
            continue
        same = got.get("same_as")
        if isinstance(same, int) and 0 <= same < i and revised[same] is not None:
            revised[i] = json.loads(json.dumps(revised[same]))
            continue
        revised[i] = merge_plan(plan, got, sections[i])
    return revised


def merge_plan(plan, got, sec):
    """Take the LLM's fields where they pass the rules; keep ours otherwise."""
    out = json.loads(json.dumps(plan))
    f = sec["freq"]
    evidence16 = lambda fam, s: f[fam][s] >= 0.2
    evidence8 = lambda fam, e: max(f[fam][2 * e], f[fam][2 * e + 1] if 2 * e + 1 < 16 else 0,
                                   f[fam][2 * e - 1] if e else 0) >= 0.2

    if got.get("cymbal") in CYMBALS:
        out["cymbal"] = got["cymbal"]
    m, mo = got.get("medium") or {}, out["medium"]
    if m.get("pulse") in ("8th", "quarter", "offbeat"):
        mo["pulse"] = m["pulse"]
    for fam, limit in (("kick", 3), ("snare", 2)):
        p = m.get(fam)
        if isinstance(p, str) and len(p) == 8 and set(p) <= {"x", "."}:
            slots = {e for e in _slots(p) if evidence8(fam, e)}
            if len(slots) <= limit or (fam == "kick" and slots == {0, 2, 4, 6}):
                mo[fam] = _pattern(slots, 8)
    if m.get("fill") in ("snare", "toms", "none"):
        mo["fill"] = m["fill"]
    h, ho = got.get("hard") or {}, out["hard"]
    for fam, src in (("kick", "kick"), ("snare", "snare"), ("cymbal", "cym")):
        p = h.get(fam)
        if isinstance(p, str) and len(p) == 16 and set(p) <= {"x", "."}:
            slots = _slots(p) if fam == "cymbal" else {s for s in _slots(p) if evidence16(src, s)}
            ho[fam] = _pattern(slots)
    if isinstance(h.get("variations"), bool):
        ho["variations"] = h["variations"]
    return out


def fit_for_learners(plan, tempo):
    """Keep Medium within MEDIUM_MAX_ONSET_RATE whatever the plan asked for:
    first slow the cymbal pulse to quarters, then thin the kick to beats 1
    and 3. Runs after the LLM revision, so it is a hard limit."""
    m = plan["medium"]
    bar_s = 4 * 60.0 / tempo

    def rate():
        pulse = {"8th": set(range(8)), "quarter": {0, 2, 4, 6}, "offbeat": {1, 3, 5, 7}}[m["pulse"]]
        onsets = pulse | _slots(m["kick"]) | _slots(m["snare"])
        return len(onsets) / bar_s

    if rate() > MEDIUM_MAX_ONSET_RATE and m["pulse"] != "quarter":
        m["pulse"] = "quarter"
    if rate() > MEDIUM_MAX_ONSET_RATE:
        m["kick"] = _pattern(_slots(m["kick"]) & {0, 4} or {0}, 8)
    return plan


# ─── 4. Render ───────────────────────────────────────────────────────────────

class Track:
    def __init__(self):
        self.slots = {}  # global slot -> {lane: is_cymbal}

    def add(self, g, lane, cymbal=False):
        self.slots.setdefault(g, {})[lane] = cymbal

    def pads(self, g):
        return [l for l in self.slots.get(g, {}) if l != KICK]

    def notes(self, grid):
        out = []
        for g in sorted(self.slots):
            tick = grid.tick(g)
            for lane, cym in sorted(self.slots[g].items()):
                out.append({"tick": tick, "lane": lane, "length": 0})
                if cym:
                    out.append({"tick": tick, "lane": CYMBAL_FLAG[lane], "length": 0})
        return out


def _beat_on(active_beats, grid, g):
    b = g // 4
    return 0 <= b < len(active_beats) and active_beats[b]


def render_medium(sections, plans, table, grid, active_beats):
    tr = Track()
    for i, (sec, plan) in enumerate(zip(sections, plans)):
        if plan is None:
            continue
        m = plan["medium"]
        pulse = {"8th": set(range(0, 16, 2)), "quarter": {0, 4, 8, 12},
                 "offbeat": {2, 6, 10, 14}}[m["pulse"]]
        cym_lane = CYM_LANE[plan["cymbal"]]
        kick = {2 * e for e in _slots(m["kick"])}
        snare = {2 * e for e in _slots(m["snare"])}
        has_next = i + 1 < len(sections)
        for b in sec["active"]:
            g0 = grid.bar_start(b)
            # A fill leads into a new groove, or closes an 8-bar phrase where
            # the drummer really played one
            phrase_end = (b - sec["start"]) % 8 == 7
            fill_bar = m["fill"] != "none" and (
                (b == sec["end"] - 1 and has_next and sec["end"] - sec["start"] >= 4) or
                (phrase_end and b in sec["fills"]))
            for s in range(16):
                g = g0 + s
                if not _beat_on(active_beats, grid, g):
                    continue
                if fill_bar and s >= 8:
                    if s in (8, 12):  # two quarter notes into the next section
                        lane = RED if (m["fill"] == "snare" or s == 8) else BLUE
                        tr.add(g, lane)
                    if s == 8 and s in kick:
                        tr.add(g, KICK)
                    continue
                if s in kick:
                    tr.add(g, KICK)
                if s in snare:
                    tr.add(g, RED)  # one hand: the snare replaces the cymbal
                elif s in pulse:
                    tr.add(g, cym_lane, cymbal=True)
            if b == sec["active"][0] and sec["opens_with_crash"]:
                _crash_downbeat(tr, g0, keep_kick=True)
    return tr


def _crash_downbeat(tr, g, keep_kick):
    hits = tr.slots.get(g, {})
    kick = KICK in hits
    tr.slots[g] = {GREEN: True}
    if kick or keep_kick:
        tr.slots[g][KICK] = False


def render_hard(sections, plans, table, grid, active_beats):
    tr = Track()
    # A variation hit must be at least as strong as the song's typical hit
    strong = {k: np.median([kinds[k] for kinds in table.values() if k in kinds] or [0])
              for k in ("kick", "snare")}
    for sec, plan in zip(sections, plans):
        if plan is None:
            continue
        h = plan["hard"]
        cym_lane = CYM_LANE[plan["cymbal"]]
        kick, snare, cym = _slots(h["kick"]), _slots(h["snare"]), _slots(h["cymbal"])
        if len(cym) < 4:
            cym |= {0, 4, 8, 12}  # never leave a trained player without a pulse
        for b in sec["active"]:
            g0 = grid.bar_start(b)
            pat = bar_pattern(table, grid, b)
            fill_from = 16
            if b in sec["fills"]:
                late = [s for s, kinds in pat.items() if s >= 8 and
                        ("snare" in kinds or any(k in TOMS for k in kinds))]
                fill_from = min(late) if late else 16
            for s in range(16):
                g = g0 + s
                if not _beat_on(active_beats, grid, g):
                    continue
                kinds = pat.get(s, {})
                if s >= fill_from:
                    # The real fill, one hand per 16th, plus the kick
                    pad = next((k for k in ("snare", "tom_hi", "tom_mid", "tom_lo", "crash") if k in kinds), None)
                    if pad == "snare":
                        tr.add(g, RED)
                    elif pad in TOM_LANE:
                        tr.add(g, TOM_LANE[pad])
                    elif pad == "crash":
                        tr.add(g, GREEN, cymbal=True)
                    if "kick" in kinds:
                        tr.add(g, KICK)
                    continue
                if s in kick or (h["variations"] and kinds.get("kick", 0) >= strong["kick"] and s % 2 == 0):
                    tr.add(g, KICK)
                if s in snare or (h["variations"] and kinds.get("snare", 0) >= strong["snare"] and s % 2 == 0):
                    tr.add(g, RED)
                if "crash" in kinds and s % 4 == 0 and h["variations"]:
                    tr.add(g, GREEN, cymbal=True)  # the crash takes the pulse hand
                elif s in cym:
                    tr.add(g, cym_lane, cymbal=True)
            if b == sec["active"][0] and sec["opens_with_crash"]:
                hits = tr.slots.setdefault(g0, {})
                hits.pop(cym_lane, None)
                hits[GREEN] = True
    return tr


def render_easy(sections, plans, table, grid, active_beats):
    """One drum at a time on quarter notes: kick on 1 (and 3), snare on the
    backbeat, the cymbal on the other beats."""
    tr = Track()
    for sec, plan in zip(sections, plans):
        if plan is None:
            continue
        m = plan["medium"]
        cym_lane = CYM_LANE[plan["cymbal"]]
        kick = {2 * e for e in _slots(m["kick"]) if (2 * e) % 4 == 0}
        snare = {2 * e for e in _slots(m["snare"]) if (2 * e) % 4 == 0}
        step = 4 if grid.tempo / 60 <= EASY_MAX_PULSE_RATE else 8
        for b in sec["active"]:
            g0 = grid.bar_start(b)
            for s in range(0, 16, 4):
                g = g0 + s
                if not _beat_on(active_beats, grid, g):
                    continue
                if s in snare:
                    tr.add(g, RED)
                elif s in kick:
                    tr.add(g, KICK)
                elif s % step == 0:
                    tr.add(g, cym_lane, cymbal=True)
            if b == sec["active"][0] and sec["opens_with_crash"]:
                tr.slots[g0] = {GREEN: True}
    return tr


# ─── Entry point ─────────────────────────────────────────────────────────────

def plan_and_render(hits, y, sr, tempo, beat_times, sections, duration,
                    ai_provider=None, api_key="", song=""):
    grid = Grid(tempo, beat_times, duration)
    table = slot_table([h for h in hits if h["time"] <= duration], grid)
    if not table:
        return {}, {}
    grid.downbeat = find_downbeat(table)
    n_beats = int(np.ceil(grid.beat_pos(duration))) + 1
    active_beats = beat_activity(y, sr, grid, n_beats)
    n_bars = grid.bar_of(max(table)) + 2
    active_bars = {b: sum(active_beats[bb] for bb in range(b * 4 + grid.downbeat, b * 4 + grid.downbeat + 4)
                          if 0 <= bb < n_beats) >= 2
                   for b in range(n_bars)}

    secs = section_bars(sections or [], grid, n_bars)
    for sec in secs:
        analyze_section(sec, table, grid, active_bars)
    secs = merge_same_grooves(secs, table, grid, active_bars)
    plans = [default_plan(sec, tempo) if sec.get("active") and "freq" in sec else None for sec in secs]

    planner = "rules"
    plans = [fit_for_learners(p, tempo) if p else None for p in plans]
    if ai_provider:
        print(f"  Planning drum parts with {ai_provider}...", file=sys.stderr)
        revised = ai_revise(plans, secs, tempo, ai_provider, api_key, song)
        if revised is not None:
            plans = [fit_for_learners(p, tempo) if p else None for p in revised]
            planner = ai_provider
        else:
            print("  AI drum plan unavailable, using the rule-based plan", file=sys.stderr)

    diffs = {
        "HardDrums": render_hard(secs, plans, table, grid, active_beats).notes(grid),
        "MediumDrums": render_medium(secs, plans, table, grid, active_beats).notes(grid),
        "EasyDrums": render_easy(secs, plans, table, grid, active_beats).notes(grid),
    }
    info = {"planner": planner, "downbeat": grid.downbeat, "sections": len(secs),
            "plans": [{"label": s["label"], "bars": [s["start"], s["end"]], **p}
                      for s, p in zip(secs, plans) if p is not None]}
    return diffs, info
