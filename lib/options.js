'use strict';

const os = require('node:os');
const path = require('node:path');

const SPOTIFY_ID = /^[A-Za-z0-9]{22}$/;
const BROWSERS = new Set(['chrome', 'chromium', 'firefox', 'edge', 'brave', 'safari']);
const DEFAULTS = Object.freeze({
  useGemini: false,
  videoMode: 'off',
  fetchLyrics: true,
  skipExisting: true,
  rewrite: false,
  keepTemp: false,
  rateLimitMs: null,
  outputBase: path.join(os.homedir(), 'Desktop', 'Clone Hero'),
  cookiesBrowser: null,
  allowDurationMismatch: false,
});

function parseSpotifyInput(input, expectedType) {
  if (typeof input !== 'string') throw new Error('A Spotify track or playlist link is required.');
  let type, id;
  const uri = /^spotify:(track|playlist):([A-Za-z0-9]{22})$/.exec(input);
  if (uri) [, type, id] = uri;
  else {
    let url;
    try { url = new URL(input); } catch { throw new Error('Use a complete Spotify track or playlist URL or URI.'); }
    const match = /^\/(?:intl-[a-z-]+\/)?(?:embed\/)?(track|playlist)\/([A-Za-z0-9]{22})\/?$/.exec(url.pathname);
    if (url.protocol !== 'https:' || !['open.spotify.com', 'play.spotify.com'].includes(url.hostname)
        || url.username || url.password || url.port || !match) {
      throw new Error('Use an HTTPS open.spotify.com track or playlist link with a valid 22-character ID.');
    }
    [, type, id] = match;
  }
  if (!SPOTIFY_ID.test(id) || (expectedType && type !== expectedType)) {
    throw new Error(`Expected a Spotify ${expectedType || 'track or playlist'} link.`);
  }
  return { type, id, url: `https://open.spotify.com/${type}/${id}` };
}

function parseRateLimit(value) {
  if (!/^\d+$/.test(String(value)) || !Number.isSafeInteger(Number(value)) || Number(value) > 2147483647) {
    throw new Error('--rate-limit must be a nonnegative integer in milliseconds (maximum 2147483647).');
  }
  return Number(value);
}

function expandPath(value) {
  if (!value || /[\x00-\x1f]/.test(value)) throw new Error('--output requires a directory path.');
  if (value === '~') return os.homedir();
  if (value.startsWith('~/')) return path.join(os.homedir(), value.slice(2));
  return value;
}

function parseOptions(args, defaults = {}) {
  const options = { ...DEFAULTS, ...defaults };
  const seen = new Map();
  const toggles = {
    '--gemini': ['useGemini', true], '--no-gemini': ['useGemini', false],
    '--lyrics': ['fetchLyrics', true], '--no-lyrics': ['fetchLyrics', false],
    '--video': ['videoMode', 'on'], '--no-video': ['videoMode', 'off'], '--auto-video': ['videoMode', 'auto'],
    '--skip-existing': ['skipExisting', true], '--no-skip-existing': ['skipExisting', false],
    '--rewrite': ['rewrite', true], '--keep-temp': ['keepTemp', true],
    '--allow-duration-mismatch': ['allowDurationMismatch', true],
  };
  for (let i = 0; i < args.length; i++) {
    const [flag, ...inline] = args[i].split('=');
    const toggle = toggles[flag];
    if (toggle && !inline.length) {
      const [key, value] = toggle;
      if (seen.has(key) && seen.get(key) !== value) throw new Error(`Conflicting options for ${key}.`);
      seen.set(key, value);
      options[key] = value;
      continue;
    }
    if (!['--output', '--rate-limit', '--cookies-from-browser'].includes(flag)) {
      throw new Error(`Unknown option: ${args[i]}. Run songhero --help.`);
    }
    const value = inline.length ? inline.join('=') : args[++i];
    if (!value || value.startsWith('--')) throw new Error(`${flag} requires a value.`);
    if (flag === '--output') options.outputBase = expandPath(value);
    if (flag === '--rate-limit') options.rateLimitMs = parseRateLimit(value);
    if (flag === '--cookies-from-browser') {
      if (!BROWSERS.has(value)) throw new Error(`Unsupported browser: ${value}. Use ${[...BROWSERS].join(', ')}.`);
      options.cookiesBrowser = value;
    }
  }
  return options;
}

function tokenize(line) {
  const words = [];
  let word = '', quote = null, active = false;
  for (const char of line.trim()) {
    if (quote) {
      if (char === quote) quote = null;
      else word += char;
      active = true;
    } else if (char === '"' || char === "'") {
      quote = char;
      active = true;
    } else if (/\s/.test(char)) {
      if (active) words.push(word);
      word = ''; active = false;
    } else {
      word += char; active = true;
    }
  }
  if (quote) throw new Error('Unclosed quote.');
  if (active) words.push(word);
  return words;
}

module.exports = { DEFAULTS, parseOptions, parseSpotifyInput, parseRateLimit, expandPath, tokenize };
