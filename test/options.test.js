'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { parseOptions, parseSpotifyInput, tokenize } = require('../lib/options');

const ID = '0VjIjW4GlUZAMYd2vXMi3b';

test('Spotify links and URIs normalize to the same trusted URL', () => {
  for (const input of [
    `spotify:track:${ID}`,
    `https://open.spotify.com/track/${ID}?si=abc`,
    `https://open.spotify.com/intl-de/track/${ID}`,
    `https://play.spotify.com/track/${ID}`,
  ]) {
    assert.deepEqual(parseSpotifyInput(input), {
      type: 'track', id: ID, url: `https://open.spotify.com/track/${ID}`,
    });
  }
  assert.equal(parseSpotifyInput(`spotify:playlist:${ID}`).type, 'playlist');
});

test('untrusted hosts, malformed IDs and wrong input types fail before network access', () => {
  for (const input of [
    `https://evil.test/open.spotify.com/track/${ID}`,
    `https://open.spotify.com.evil.test/track/${ID}`,
    `https://user@open.spotify.com/track/${ID}`,
    `https://open.spotify.com/track/${ID}/extra`,
    'spotify:track:short', '$(touch nope)', '--doctor',
  ]) assert.throws(() => parseSpotifyInput(input));
  assert.throws(() => parseSpotifyInput(`spotify:playlist:${ID}`, 'track'));
});

test('options have safe defaults and accept all documented toggles', () => {
  const defaults = parseOptions([]);
  assert.equal(defaults.useGemini, false);
  assert.equal(defaults.videoMode, 'off');
  assert.equal(defaults.fetchLyrics, true);
  assert.equal(defaults.skipExisting, true);
  const opts = parseOptions(['--output', '/tmp/my charts', '--rate-limit=0', '--video', '--gemini', '--no-lyrics', '--rewrite', '--keep-temp', '--cookies-from-browser', 'firefox']);
  assert.equal(opts.outputBase, '/tmp/my charts');
  assert.equal(opts.rateLimitMs, 0);
  assert.equal(opts.videoMode, 'on');
  assert.equal(opts.useGemini, true);
  assert.equal(opts.fetchLyrics, false);
  assert.equal(opts.rewrite, true);
  assert.equal(opts.keepTemp, true);
  assert.equal(opts.cookiesBrowser, 'firefox');
});

test('bad and conflicting options are errors, not silently ignored', () => {
  for (const args of [
    ['--output'], ['--output', '--video'], ['--rate-limit', 'NaN'],
    ['--rate-limit', '12ms'], ['--rate-limit', '-1'], ['--rate-limit', '1.5'],
    ['--video', '--no-video'], ['--gemini', '--no-gemini'],
    ['--unknown'], ['--cookies-from-browser', 'anything'],
  ]) assert.throws(() => parseOptions(args));
});

test('interactive tokenization handles quoted paths without evaluating shell syntax', () => {
  assert.deepEqual(tokenize('output "my charts"'), ['output', 'my charts']);
  assert.deepEqual(tokenize("output 'my charts'"), ['output', 'my charts']);
  assert.deepEqual(tokenize('output "$(do-not-run)"'), ['output', '$(do-not-run)']);
  assert.throws(() => tokenize('output "unfinished'));
});
