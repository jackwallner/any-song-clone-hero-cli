'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { after, before, test } = require('node:test');

const { resolvePython } = require('../lib/config');
const { createPipeline } = require('../lib/pipeline');
const { packageStatus } = require('../lib/paths');
const { runProcess, sleep } = require('../lib/process');

const ID = '0VjIjW4GlUZAMYd2vXMi3b';
const URL = `https://open.spotify.com/track/${ID}`;
let fixtures, python;

before(async () => {
  fixtures = fs.mkdtempSync(path.join(os.tmpdir(), 'songhero-media-fixture-'));
  python = resolvePython();
  await runProcess(python, ['-c', [
    'import numpy as np, soundfile as sf, sys',
    'sr=22050; t=np.arange(sr*8)/sr',
    'y=0.1*np.sin(2*np.pi*440*t)+0.6*np.sin(2*np.pi*1000*t)*(np.mod(t,0.5)<0.012)',
    'sf.write(sys.argv[1], y, sr)',
  ].join(';'), path.join(fixtures, 'source.wav')]);
  await runProcess('ffmpeg', ['-y', '-v', 'error', '-i', path.join(fixtures, 'source.wav'), '-c:a', 'libopus', path.join(fixtures, 'song.opus')]);
  await runProcess('ffmpeg', ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10:d=8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path.join(fixtures, 'video.mp4')]);
});
after(() => { if (fixtures) fs.rmSync(fixtures, { recursive: true, force: true }); });

function setup(t, overrides = {}) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'songhero-pipeline-test-'));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const logs = [], downloads = [];
  const dependencies = {
    python, tempRoot: base, log: message => logs.push(message), checkDependencies: async () => {},
    resolveTrack: async url => ({ id: url.split('/').at(-1), name: '../../Song "quoted" $(never)', artist: 'Artist / Band', duration_ms: 8000, spotify_tempo: 120 }),
    resolvePlaylist: async () => ({ tracks: [] }),
    downloadSong: async (_artist, _name, directory, options) => {
      downloads.push(options);
      fs.copyFileSync(path.join(fixtures, 'song.opus'), path.join(directory, 'song.opus'));
      if (options.videoMode !== 'off') fs.copyFileSync(path.join(fixtures, 'video.mp4'), path.join(directory, 'video.mp4'));
      return { hasVideo: options.videoMode !== 'off', youtubeUrl: 'https://www.youtube.com/watch?v=fixture0000' };
    },
    analyzeAudio: async () => ({ tempo: 120000, duration_ms: 8000, sections: [{ start: 0, end: 8, label: 'verse' }],
      tempo_map: [{ tick: 0, bpm: 120000 }], section_events: [{ tick: 0, name: 'verse' }],
      difficulties: Object.fromEntries(['EasySingle', 'MediumSingle', 'HardSingle', 'ExpertSingle'].map(name => [name, [{ tick: 480, fret: 0, length: 0 }]])), lyrics: [] }),
    generateChart: async () => '﻿[Song]\r\n{\r\n}\r\n',
  };
  const pipeline = createPipeline({ ...dependencies, ...overrides });
  return { base, logs, downloads, pipeline, options: { outputBase: path.join(base, 'songs'), fetchLyrics: false } };
}

test('audio-only and video packages are complete, safe and independently owned', async t => {
  const { pipeline, options, downloads } = setup(t);
  const first = await pipeline.runPipeline(URL, options);
  assert.equal(first.status, 'success', first.error);
  assert.equal(packageStatus(first.outputPath, ID).valid, true);
  assert.equal(fs.existsSync(path.join(first.outputPath, 'video.mp4')), false);
  assert.equal(downloads[0].videoMode, 'off');
  assert.equal(downloads[0].cookiesBrowser, null);
  const second = await pipeline.runPipeline(URL, { ...options, videoMode: 'on', rewrite: true });
  assert.equal(second.status, 'success', second.error);
  assert.equal(fs.existsSync(path.join(second.outputPath, 'video.mp4')), true);
});

test('skip, modified packages, rewrites and failed rewrites have truthful results', async t => {
  let fail = false;
  const { pipeline, options, downloads } = setup(t, { generateChart: async () => {
    if (fail) throw new Error('fixture chart failure');
    return '﻿[Song]\r\n{\r\n}\r\n';
  } });
  const first = await pipeline.runPipeline(URL, options);
  assert.equal((await pipeline.runPipeline(URL, options)).status, 'skipped');
  assert.equal(downloads.length, 1);
  const original = fs.readFileSync(path.join(first.outputPath, '.songhero.json'));
  fail = true;
  const failed = await pipeline.runPipeline(URL, { ...options, rewrite: true });
  assert.equal(failed.status, 'failed');
  assert.deepEqual(fs.readFileSync(path.join(first.outputPath, '.songhero.json')), original);
  fs.writeFileSync(path.join(first.outputPath, 'notes.chart'), 'user changes');
  assert.equal((await pipeline.runPipeline(URL, options)).status, 'failed');
});

test('failures and cancellation clean temporary work and locks, keep-temp is honored', async t => {
  const { pipeline, options, base } = setup(t, { downloadSong: async () => { throw new Error('fixture download failure'); } });
  assert.equal((await pipeline.runPipeline(URL, options)).status, 'failed');
  assert.equal(fs.readdirSync(base).some(file => file.startsWith('songhero-')), false);
  assert.equal(fs.readdirSync(options.outputBase).some(file => file.startsWith('.songhero-')), false);
  const kept = await pipeline.runPipeline(URL, { ...options, keepTemp: true });
  assert.equal(kept.status, 'failed');
  assert.equal(fs.existsSync(kept.workDir), true);
  const controller = new AbortController();
  const cancelled = setup(t, { downloadSong: async (_artist, _name, _dir, opts) => { controller.abort(); await sleep(1000, opts.signal); } });
  await assert.rejects(cancelled.pipeline.runPipeline(URL, { ...cancelled.options, signal: controller.signal }), { name: 'AbortError' });
  assert.equal(fs.readdirSync(cancelled.base).some(file => file.startsWith('songhero-')), false);
});

test('mixed playlists aggregate failures and duplicate skips, empty playlists fail', async t => {
  const { pipeline, options } = setup(t, { resolvePlaylist: async () => ({ playlist_name: 'Fixture', tracks: [
    { spotify_url: URL }, { spotify_url: 'invalid' }, { spotify_url: URL },
  ] }) });
  const result = await pipeline.processPlaylist(`spotify:playlist:${ID}`, { ...options, rateLimitMs: 0 });
  assert.equal(result.succeeded, 1);
  assert.equal(result.failed, 1);
  assert.equal(result.skipped, 1);
  assert.equal(result.status, 'failed');
  const empty = setup(t);
  assert.equal((await empty.pipeline.processPlaylist(`spotify:playlist:${ID}`, empty.options)).status, 'failed');
});

test('the real analyzer and chart writer produce a playable-format offline package', { timeout: 180000 }, async t => {
  const { pipeline, options, logs } = setup(t, {
    analyzeAudio: undefined, generateChart: undefined,
    fetchLyrics: async () => ({ synced: true, source: 'fixture', lrc_duration: 8, events: [{ time: 1, word: 'Fixture lyrics' }] }),
  });
  const result = await pipeline.runPipeline(URL, { ...options, fetchLyrics: true });
  assert.equal(result.status, 'success', `${result.error}\n${logs.join('\n')}`);
  const bytes = fs.readFileSync(path.join(result.outputPath, 'notes.chart'));
  assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf]);
  const chart = bytes.toString('utf8');
  for (const name of ['EasySingle', 'MediumSingle', 'HardSingle', 'ExpertSingle']) assert.ok(chart.includes(`[${name}]`));
  assert.ok(chart.includes('lyric Fixture lyrics'));
  assert.ok(fs.existsSync(path.join(result.outputPath, 'lyrics.json')));
  assert.ok(chart.endsWith('\r\n'));
  assert.ok(logs.some(message => /Tempo: 120 BPM/.test(message)));
  assert.equal(packageStatus(result.outputPath, ID).valid, true);
});
