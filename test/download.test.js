'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { after, before, test } = require('node:test');

const { downloadSong } = require('../lib/download');
const { probeMedia } = require('../lib/media');
const { runProcess } = require('../lib/process');

let fixtures;
before(async () => {
  fixtures = fs.mkdtempSync(path.join(os.tmpdir(), 'songhero-download-fixtures-'));
  await runProcess('ffmpeg', ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
    '-c:a', 'libopus', path.join(fixtures, 'audio.webm')]);
  await runProcess('ffmpeg', ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10:d=2',
    '-c:v', 'mpeg4', path.join(fixtures, 'video.mp4')]);
});
after(() => { if (fixtures) fs.rmSync(fixtures, { recursive: true, force: true }); });

function setup(t, { failVideo = false, sourceUrl = 'https://www.youtube.com/watch?v=fixture0000' } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'songhero-download-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const calls = [], logs = [];
  const run = async (command, args, options) => {
    if (command !== 'yt-dlp') return runProcess(command, args, options);
    calls.push(args);
    if (args.includes('-x')) {
      fs.copyFileSync(path.join(fixtures, 'audio.webm'), path.join(directory, 'song.opus'));
      return { stdout: JSON.stringify({ title: 'Fixture official music video', webpage_url: sourceUrl }), stderr: '' };
    }
    if (failVideo) throw new Error('Fixture video download failed');
    fs.copyFileSync(path.join(fixtures, 'video.mp4'), path.join(directory, 'video.mp4'));
    return { stdout: '', stderr: '' };
  };
  return { directory, calls, logs, options: { run, retries: 0, log: message => logs.push(message) } };
}

test('downloader ignores user config and remuxes mislabeled WebM Opus without using cookies', async t => {
  const { directory, calls, options } = setup(t);
  const result = await downloadSong('Artist $(never)', 'Song', directory, options);
  assert.equal(result.hasVideo, false);
  assert.equal(calls.length, 1);
  assert.ok(calls[0].includes('--ignore-config'));
  assert.ok(calls[0].includes('ytsearch1:Artist $(never) - Song'));
  assert.ok(!calls[0].includes('--cookies-from-browser'));
  const audio = await probeMedia(path.join(directory, 'song.opus'));
  assert.equal(audio.format.format_name, 'ogg');
  assert.equal(audio.streams[0].codec_name, 'opus');
});

test('explicit video and cookie access produce validated H.264 MP4', async t => {
  const { directory, calls, options } = setup(t);
  const result = await downloadSong('Artist', 'Song', directory, { ...options, videoMode: 'on', cookiesBrowser: 'firefox' });
  assert.equal(result.hasVideo, true);
  assert.equal(calls.length, 2);
  for (const args of calls) assert.equal(args[args.indexOf('--cookies-from-browser') + 1], 'firefox');
  const video = await probeMedia(path.join(directory, 'video.mp4'));
  assert.equal(video.streams[0].codec_name, 'h264');
  assert.equal(video.streams[0].pix_fmt, 'yuv420p');
});

test('automatic video failures continue audio-only and required video failures are errors', async t => {
  const automatic = setup(t, { failVideo: true });
  assert.equal((await downloadSong('Artist', 'Song', automatic.directory, { ...automatic.options, videoMode: 'auto' })).hasVideo, false);
  assert.ok(automatic.logs.some(line => /continuing with audio only/.test(line)));
  const required = setup(t, { failVideo: true });
  await assert.rejects(downloadSong('Artist', 'Song', required.directory, { ...required.options, videoMode: 'on' }), { code: 'VIDEO_REQUIRED' });
});

test('unexpected sources and duration mismatches fail unless the duration override is explicit', async t => {
  const untrusted = setup(t, { sourceUrl: 'https://evil.test/recording' });
  await assert.rejects(downloadSong('Artist', 'Song', untrusted.directory, untrusted.options), /unexpected source URL/);
  const mismatch = setup(t);
  await assert.rejects(downloadSong('Artist', 'Song', mismatch.directory, { ...mismatch.options, expectedDurationMs: 120000 }), { code: 'DURATION_MISMATCH' });
  const override = setup(t);
  assert.equal((await downloadSong('Artist', 'Song', override.directory, { ...override.options, expectedDurationMs: 120000, allowDurationMismatch: true })).hasVideo, false);
  assert.ok(override.logs.some(line => /different version/.test(line)));
});
