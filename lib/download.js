'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { VIDEO_MAX_HEIGHT, ensureCloneHeroVideo, ensureOpus, probeMedia } = require('./media');
const { runProcess, sleep } = require('./process');

const NON_VIDEO_PATTERNS = [
  /lyric\s*video/i, /official\s*lyric/i, /\blyrics\b/i, /official\s*audio/i,
  /audio\s*only/i, /\bvisuali[sz]er\b/i, /\bvisuali[sz]ation\b/i,
  /static\s*image/i, /album\s*art/i, /\btopic\b/i,
];

function isMusicVideo(title) {
  return !NON_VIDEO_PATTERNS.some(pattern => pattern.test(title));
}

function youtubeUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error('Downloader did not return a YouTube source URL.'); }
  if (url.protocol !== 'https:' || !['www.youtube.com', 'youtube.com', 'music.youtube.com', 'youtu.be'].includes(url.hostname)
      || url.username || url.password || url.port) throw new Error('Downloader returned an unexpected source URL.');
  return url.toString();
}

async function attemptDownload(artist, title, outputDir, options) {
  const { videoMode = 'off', cookiesBrowser, expectedDurationMs = 0, allowDurationMismatch = false, signal, run = runProcess, log = console.log } = options;
  const cookies = cookiesBrowser ? ['--cookies-from-browser', cookiesBrowser] : [];
  const common = ['--ignore-config', ...cookies, '--no-playlist', '--no-warnings', '--retries', '3', '--socket-timeout', '20'];
  const result = await run('yt-dlp', [
    ...common, 'ytsearch1:' + `${artist} - ${title}`, '-f', 'bestaudio[ext=webm]/bestaudio',
    '-x', '--audio-format', 'opus', '--audio-quality', '0', '-o', path.join(outputDir, 'song.%(ext)s'),
    '--write-thumbnail', '--convert-thumbnails', 'jpg', '--no-simulate',
    '--print', 'after_move:{"title":%(title)j,"duration":%(duration)j,"webpage_url":%(webpage_url)j}',
  ], { timeout: 600000, signal });
  const info = result.stdout.trim().split(/\r?\n/).reverse().map(line => {
    try { return JSON.parse(line); } catch { return null; }
  }).find(item => item && typeof item.webpage_url === 'string');
  if (!info) throw new Error('Downloader did not return source metadata.');
  const sourceUrl = youtubeUrl(info.webpage_url);
  const audioPath = path.join(outputDir, 'song.opus');
  const audio = await ensureOpus(audioPath, { run, signal });
  const durationMs = Math.round(audio.duration * 1000);
  if (expectedDurationMs > 0 && Math.abs(expectedDurationMs - durationMs) > Math.max(10000, expectedDurationMs * 0.08)) {
    const error = new Error(`Downloaded audio is ${Math.round(durationMs / 1000)}s, Spotify reports ${Math.round(expectedDurationMs / 1000)}s. It may be a different version. Use --allow-duration-mismatch only if this is intended.`);
    error.code = 'DURATION_MISMATCH';
    if (!allowDurationMismatch) throw error;
    log(`Warning: ${error.message}`);
  }
  if (fs.existsSync(path.join(outputDir, 'song.jpg'))) fs.renameSync(path.join(outputDir, 'song.jpg'), path.join(outputDir, 'album.jpg'));
  const output = { artist, title: info.title || title, durationMs, youtubeUrl: sourceUrl, hasVideo: false, audioFile: 'song.opus' };
  if (videoMode === 'off' || (videoMode === 'auto' && !isMusicVideo(info.title || ''))) return output;
  try {
    await run('yt-dlp', [
      ...common, sourceUrl, '-f', [
        `bestvideo[height<=${VIDEO_MAX_HEIGHT}][vcodec^=avc1][ext=mp4]`,
        `best[height<=${VIDEO_MAX_HEIGHT}][vcodec^=avc1][ext=mp4]`,
        `bestvideo[height<=${VIDEO_MAX_HEIGHT}]`, `best[height<=${VIDEO_MAX_HEIGHT}]`,
      ].join('/'), '--remux-video', 'mp4', '--max-filesize', '100M', '-o', path.join(outputDir, 'video.%(ext)s'),
    ], { timeout: 600000, signal });
    const file = fs.readdirSync(outputDir).find(name => /^video\.(mp4|webm|mkv|mov)$/.test(name));
    if (!file) throw new Error('No video stream was downloaded.');
    const target = await ensureCloneHeroVideo(path.join(outputDir, file), { run, signal });
    const video = await probeMedia(target, { run, signal });
    if (Math.abs(video.duration - audio.duration) > Math.max(2, audio.duration * 0.02)) throw new Error('Video duration does not match the audio.');
    output.hasVideo = true;
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    for (const file of fs.readdirSync(outputDir).filter(name => name.startsWith('video.'))) {
      fs.rmSync(path.join(outputDir, file), { force: true });
    }
    if (videoMode === 'on') {
      error.code = 'VIDEO_REQUIRED';
      throw error;
    }
    log(`Warning: video unavailable, continuing with audio only (${error.message}).`);
  }
  return output;
}

async function downloadSong(artist, title, outputDir, options = {}) {
  if (typeof options === 'string') options = { videoMode: options };
  if (!['off', 'auto', 'on'].includes(options.videoMode || 'off')) throw new Error('Invalid video mode.');
  fs.mkdirSync(outputDir, { recursive: true });
  const { retries = 2, signal, log = console.log } = options;
  for (let attempt = 0; ; attempt++) {
    try { return await attemptDownload(artist, title, outputDir, options); }
    catch (error) {
      if (error.name === 'AbortError' || ['ENOENT', 'DURATION_MISMATCH', 'VIDEO_REQUIRED'].includes(error.code) || attempt >= retries) throw error;
      log(`Download failed, retrying (${attempt + 1}/${retries}): ${error.message}`);
      await sleep(2000 * (attempt + 1), signal);
      for (const file of fs.readdirSync(outputDir)) fs.rmSync(path.join(outputDir, file), { recursive: true, force: true });
    }
  }
}

module.exports = { downloadSong, isMusicVideo };
