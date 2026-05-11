#!/usr/bin/env python3
"""Compare pipeline analysis output against a community .chart file.
Usage: python3 test_compare.py <analysis.json> <notes.chart> [--verbose]
"""
import json, sys, re
from statistics import mean, stdev
from collections import Counter

RES = 480

def parse_chart(chart_path):
    """Parse a .chart file and extract BPM map, sections, note counts, lyrics."""
    with open(chart_path, 'r', encoding='utf-8-sig') as f:
        text = f.read()
    
    # Extract BPM map from SyncTrack
    bpm_map = []
    sync_match = re.search(r'\[SyncTrack\]\s*\{([^}]+)\}', text)
    if sync_match:
        for line in sync_match.group(1).strip().split('\n'):
            m = re.match(r'\s*(\d+)\s*=\s*B\s+(\d+)', line)
            if m:
                tick = int(m.group(1))
                bpm = int(m.group(2)) / 1000.0
                bpm_map.append((tick, bpm))
    
    # Extract section events
    sections = []
    events_match = re.search(r'\[Events\]\s*\{([^}]+)\}', text)
    if events_match:
        for line in events_match.group(1).strip().split('\n'):
            m = re.match(r'\s*(\d+)\s*=\s*E\s*"section\s+(.+)"', line)
            if m:
                tick = int(m.group(1))
                name = m.group(2)
                sections.append((tick, name))
    
    # Count notes per difficulty
    note_counts = {}
    for diff in ['ExpertSingle', 'HardSingle', 'MediumSingle', 'EasySingle']:
        diff_match = re.search(rf'\[{diff}\]\s*\{{([^}}]+)\}}', text)
        if diff_match:
            note_lines = [l for l in diff_match.group(1).strip().split('\n') 
                         if re.match(r'\s*\d+\s*=\s*N', l)]
            note_counts[diff] = len(note_lines)
    
    # Extract lyrics (for count comparison)
    vocals_match = re.search(r'\[PART VOCALS\]\s*\{([^}]+)\}', text)
    lyric_count = 0
    if vocals_match:
        lyric_count = len([l for l in vocals_match.group(1).strip().split('\n') 
                          if re.match(r'\s*\d+\s*=\s*E\s*"', l)])
    
    return {
        'bpm_map': bpm_map,
        'sections': sections,
        'note_counts': note_counts,
        'lyric_count': lyric_count,
    }

def tick_to_time(tick, bpm_map):
    """Convert tick to seconds using BPM map."""
    time = 0.0
    last_tick = 0
    last_bpm = 120.0
    for t, bpm in bpm_map:
        if t >= tick:
            break
        time += (t - last_tick) * 60.0 / (last_bpm * RES)
        last_tick = t
        last_bpm = bpm
    time += (tick - last_tick) * 60.0 / (last_bpm * RES)
    return time

def compare(analysis, chart, verbose=False):
    """Compare analysis.json against community .chart. Returns score dict."""
    results = []
    issues = []
    grades = {}
    
    # === BPM COMPARISON ===
    pipe_bpm = analysis['tempo'] / 1000.0
    chart_bpms = [b for _, b in chart['bpm_map']]
    if chart_bpms:
        chart_avg_bpm = mean(chart_bpms)
        chart_bpm_range = max(chart_bpms) - min(chart_bpms)
        bpm_diff = abs(pipe_bpm - chart_avg_bpm)
        
        grades['bpm'] = {
            'pipeline': round(pipe_bpm, 1),
            'chart_avg': round(chart_avg_bpm, 1),
            'chart_range': round(chart_bpm_range, 1),
            'diff': round(bpm_diff, 1),
            'chart_tempo_changes': len(chart['bpm_map']),
        }
        
        if bpm_diff < 3:
            grades['bpm']['status'] = 'PASS'
        elif bpm_diff < 8:
            grades['bpm']['status'] = 'WARN'
            issues.append(f"BPM off by {bpm_diff:.1f} (pipe={pipe_bpm:.1f}, chart={chart_avg_bpm:.1f})")
        else:
            grades['bpm']['status'] = 'FAIL'
            issues.append(f"BPM way off: {bpm_diff:.1f} (pipe={pipe_bpm:.1f}, chart={chart_avg_bpm:.1f})")
        
        if chart['bpm_map'] and len(chart['bpm_map']) == 1:
            issues.append("Community chart has only one BPM (unusual)")
    else:
        grades['bpm'] = {'status': 'SKIP', 'reason': 'No BPM map in chart'}
    
    # === SECTION COMPARISON ===
    pipe_sections = analysis.get('section_events', [])
    chart_sections = chart['sections']
    
    pipe_labels = [s['name'] for s in pipe_sections]
    chart_labels = [s[1] for s in chart_sections]
    
    grades['sections'] = {
        'pipeline_count': len(pipe_sections),
        'chart_count': len(chart_sections),
        'pipeline_labels': pipe_labels,
        'chart_labels': chart_labels,
    }
    
    # Check section sanity
    if pipe_labels and pipe_labels[0] == 'chorus':
        issues.append("Pipeline starts with 'chorus' — should be intro/verse")
    if pipe_labels and pipe_labels[-1] != 'outro' and pipe_labels[-1] != 'ending':
        issues.append(f"Pipeline doesn't end with outro (last={pipe_labels[-1]})")
    
    # === NOTE COUNT COMPARISON ===
    pipe_notes = analysis.get('difficulties', {})
    chart_notes = chart['note_counts']
    
    grades['notes'] = {}
    for diff in ['ExpertSingle', 'HardSingle', 'MediumSingle', 'EasySingle']:
        p_count = len(pipe_notes.get(diff, []))
        c_count = chart_notes.get(diff, 0)
        
        if c_count > 0:
            ratio = p_count / c_count
            grades['notes'][diff] = {
                'pipeline': p_count,
                'chart': c_count,
                'ratio': round(ratio, 2),
            }
            
            if 0.6 <= ratio <= 1.8:
                grades['notes'][diff]['status'] = 'PASS'
            elif 0.3 <= ratio <= 3.0:
                grades['notes'][diff]['status'] = 'WARN'
                issues.append(f"{diff} note ratio: {ratio:.2f} (pipe={p_count}, chart={c_count})")
            else:
                grades['notes'][diff]['status'] = 'FAIL'
                issues.append(f"{diff} note ratio extreme: {ratio:.2f} (pipe={p_count}, chart={c_count})")
        else:
            grades['notes'][diff] = {'status': 'SKIP', 'reason': 'No chart notes'}
    
    # === LYRICS COMPARISON ===
    pipe_lyrics = len(analysis.get('lyrics', []))
    chart_lyrics = chart['lyric_count']
    grades['lyrics'] = {
        'pipeline': pipe_lyrics,
        'chart': chart_lyrics,
    }
    if chart_lyrics > 0:
        if abs(pipe_lyrics - chart_lyrics) <= chart_lyrics * 0.3:
            grades['lyrics']['status'] = 'PASS'
        else:
            grades['lyrics']['status'] = 'WARN'
            issues.append(f"Lyric count differs: pipe={pipe_lyrics}, chart={chart_lyrics}")
    else:
        grades['lyrics']['status'] = 'SKIP'
    
    # === BEAT TRACKING COVERAGE ===
    beat_times = analysis.get('beat_times', [])
    duration = analysis['duration_ms'] / 1000.0
    if beat_times:
        coverage = beat_times[-1] / duration
        grades['beat_coverage'] = {
            'tracked_until': round(beat_times[-1], 1),
            'duration': round(duration, 1),
            'coverage': round(coverage * 100, 1),
        }
        if coverage >= 0.90:
            grades['beat_coverage']['status'] = 'PASS'
        elif coverage >= 0.70:
            grades['beat_coverage']['status'] = 'WARN'
            issues.append(f"Beat tracking only covers {coverage*100:.0f}% of song")
        else:
            grades['beat_coverage']['status'] = 'FAIL'
            issues.append(f"CRITICAL: Beat tracking only covers {coverage*100:.0f}% of song ({duration-beat_times[-1]:.1f}s missing)")
    
    # Overall grade
    statuses = []
    for cat in ['bpm', 'sections', 'beat_coverage']:
        if cat in grades and 'status' in grades[cat]:
            statuses.append(grades[cat]['status'])
    for diff in grades.get('notes', {}):
        if 'status' in grades['notes'][diff]:
            statuses.append(grades['notes'][diff]['status'])
    if 'lyrics' in grades and 'status' in grades['lyrics']:
        statuses.append(grades['lyrics']['status'])
    
    fail_count = statuses.count('FAIL')
    warn_count = statuses.count('WARN')
    
    if fail_count == 0 and warn_count == 0:
        overall = 'PASS'
    elif fail_count == 0:
        overall = 'WARN'
    else:
        overall = 'FAIL'
    
    return {
        'overall': overall,
        'fail_count': fail_count,
        'warn_count': warn_count,
        'grades': grades,
        'issues': issues,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('analysis', help='analysis.json file')
    parser.add_argument('chart', help='community notes.chart file')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    
    with open(args.analysis) as f:
        analysis = json.load(f)
    
    chart = parse_chart(args.chart)
    
    result = compare(analysis, chart, verbose=args.verbose)
    
    print(json.dumps(result, indent=2))
    
    return 0 if result['overall'] == 'PASS' else 1

if __name__ == '__main__':
    sys.exit(main())
