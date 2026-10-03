'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { ROOT, resolvePython } = require('./config');
const { downloadSong } = require('./download');
const { checkDependencies } = require('./doctor');
const { probeMedia } = require('./media');
const { DEFAULTS, parseSpotifyInput } = require('./options');
const { acquireLock, digest, outputRoot, packageStatus, publishPackage, songPath } = require('./paths');
const { abortError, runProcess, sleep } = require('./process');
const { generateSongIni } = require('./songini');

function createPipeline(deps = {}) {
  const run = deps.runProcess || runProcess;
  const log = deps.log || console.log;
  let python;
  let preflight;
  const interpreter = () => (python ||= deps.python || resolvePython());
  const ready = async signal => {
    preflight ||= (deps.checkDependencies || checkDependencies)({ python: interpreter(), run, signal });
    try { await preflight; } catch (error) { preflight = null; throw error; }
  };
  const jsonScript = async (script, args, options = {}) => {
    let result;
    try {
      result = await run(interpreter(), [path.join(ROOT, 'python', script), ...args], { ...options, maxBuffer: 50 * 1024 * 1024 });
    } catch (error) {
      let failure;
      try { failure = JSON.parse(error.stdout || ''); } catch {}
      if (typeof failure?.error === 'string' && failure.error) {
        throw new Error(failure.error, { cause: error });
      }
      throw error;
    }
    const data = JSON.parse(result.stdout);
    if (data.error) throw new Error(data.error);
    if (script === 'analyze.py' && result.stderr.trim()) log(result.stderr.trim());
    return data;
  };

  async function runPipeline(input, settings = {}) {
    const options = { ...DEFAULTS, ...settings };
    const { signal, keepTemp, outputBase, rewrite, skipExisting } = options;
    let workDir, releaseLock;
    try {
      const spotify = parseSpotifyInput(input, 'track');
      if (signal?.aborted) throw abortError();
      await ready(signal);
      log('1/5 Resolving Spotify track...');
      const metadata = await (deps.resolveTrack || ((url) => jsonScript('spotify.py', [url], { signal })))(spotify.url);
      if (!metadata || typeof metadata.name !== 'string' || !metadata.name.trim()
          || typeof metadata.artist !== 'string' || !metadata.artist.trim() || (metadata.id && metadata.id !== spotify.id)) {
        throw new Error('Spotify returned incomplete or inconsistent track metadata.');
      }
      metadata.id = spotify.id;
      const root = outputRoot(outputBase);
      const destination = songPath(root, metadata);
      releaseLock = acquireLock(root, spotify.id);
      const existing = packageStatus(destination, spotify.id);
      if (existing.exists && !existing.owned) throw new Error(`Destination is not owned by this SongHero track: ${destination}`);
      if (existing.valid && skipExisting && !rewrite) {
        log(`Skipped existing chart: ${destination}`);
        return { status: 'skipped', outputPath: destination };
      }
      if (existing.exists && !existing.valid && skipExisting && !rewrite) {
        throw new Error('An incomplete or modified chart already exists. Use --rewrite to replace this SongHero-owned chart.');
      }
      workDir = fs.mkdtempSync(path.join(deps.tempRoot || os.tmpdir(), 'songhero-'));
      log(`2/5 Downloading ${metadata.artist.replace(/[\x00-\x1f\x7f]/g, ' ')} - ${metadata.name.replace(/[\x00-\x1f\x7f]/g, ' ')}...`);
      const downloaded = await (deps.downloadSong || downloadSong)(metadata.artist, metadata.name, workDir, {
        videoMode: options.videoMode, cookiesBrowser: options.cookiesBrowser,
        expectedDurationMs: Number(metadata.duration_ms) || 0, allowDurationMismatch: options.allowDurationMismatch,
        signal, run, log,
      });
      const audioPath = path.join(workDir, 'song.opus');
      const audio = await (deps.probeMedia || probeMedia)(audioPath, { run, signal });
      if (audio.format.format_name !== 'ogg' || !audio.streams.some(stream => stream.codec_name === 'opus' && stream.codec_type === 'audio')) {
        throw new Error('The downloaded audio is not valid Ogg Opus.');
      }
      if (options.videoMode === 'on' && !downloaded.hasVideo) throw new Error('Required video is unavailable.');
      if (downloaded.hasVideo && options.videoMode !== 'off') {
        const video = await (deps.probeMedia || probeMedia)(path.join(workDir, 'video.mp4'), { run, signal });
        if (!video.format.format_name.split(',').includes('mp4') || !video.streams.some(stream => stream.codec_name === 'h264' && stream.codec_type === 'video')) {
          throw new Error('The downloaded video is not valid H.264 MP4.');
        }
      }
      let lyrics = null;
      if (options.fetchLyrics) {
        log('Fetching optional lyrics...');
        try {
          lyrics = await (deps.fetchLyrics || ((track) => jsonScript('lyrics.py', [track.name, track.artist], { signal, timeout: 40000 })))(metadata);
          if (lyrics?.error) throw new Error(lyrics.error);
          if (lyrics) fs.writeFileSync(path.join(workDir, 'lyrics.json'), JSON.stringify(lyrics));
        } catch (error) {
          if (error.name === 'AbortError') throw error;
          lyrics = null;
          log(`Warning: lyrics unavailable (${error.message}).`);
        }
      }
      const key = options.geminiKey ?? process.env.GEMINI_API_KEY ?? '';
      if (options.useGemini && !key) log('Warning: --gemini has no configured key, using local analysis.');
      log('3/5 Analyzing audio...');
      const analysisArgs = [audioPath];
      if (options.useGemini && key) analysisArgs.push('--gemini');
      if (lyrics) analysisArgs.push('--lyrics-file', path.join(workDir, 'lyrics.json'));
      const env = {
        ...process.env, GEMINI_API_KEY: options.useGemini ? key : '',
        SONG_NAME: metadata.name, SONG_ARTIST: metadata.artist,
        SPOTIFY_TEMPO: String(metadata.spotify_tempo || ''), SPOTIFY_KEY: String(metadata.spotify_key ?? ''),
      };
      const analysis = await (deps.analyzeAudio || ((args) => jsonScript('analyze.py', args, { env, signal, timeout: 300000 })))(analysisArgs, { env, signal });
      if (!analysis || !Number.isFinite(analysis.duration_ms) || analysis.duration_ms <= 0 || !analysis.difficulties) throw new Error('Analysis returned an invalid song duration or notes.');
      log(`Tempo: ${Math.round(analysis.tempo / 1000)} BPM. Sections: ${analysis.sections.length}.`);
      const fullMetadata = { ...metadata, genre: metadata.genre || 'rock', duration_ms: analysis.duration_ms };
      const analysisPath = path.join(workDir, 'analysis.json');
      const metadataPath = path.join(workDir, 'metadata.json');
      fs.writeFileSync(analysisPath, JSON.stringify(analysis));
      fs.writeFileSync(metadataPath, JSON.stringify(fullMetadata));
      log('4/5 Writing and validating all four difficulties...');
      const chart = await (deps.generateChart || (async () => {
        const result = await run(interpreter(), [path.join(ROOT, 'python', 'generate_chart.py'), analysisPath, metadataPath], { signal, timeout: 30000, maxBuffer: 50 * 1024 * 1024 });
        return result.stdout;
      }))(analysis, fullMetadata);
      if (!chart.startsWith('﻿[Song]\r\n') || !chart.endsWith('\r\n')) throw new Error('Chart serializer returned invalid BOM or line endings.');
      fs.writeFileSync(path.join(workDir, 'notes.chart'), chart);
      const hasVideo = options.videoMode !== 'off' && downloaded.hasVideo;
      fs.writeFileSync(path.join(workDir, 'song.ini'), generateSongIni(fullMetadata, analysis, hasVideo));
      const files = ['notes.chart', 'song.ini', 'song.opus'];
      if (hasVideo) files.push('video.mp4');
      if (fs.existsSync(path.join(workDir, 'album.jpg'))) files.push('album.jpg');
      if (lyrics && analysis.lyrics?.length) files.push('lyrics.json');
      if (signal?.aborted) throw abortError();
      log('5/5 Publishing validated song package...');
      publishPackage(root, destination, workDir, files, {
        track_id: spotify.id, spotify_url: spotify.url, youtube_url: downloaded.youtubeUrl,
        version: require('../package.json').version, audio_sha256: digest(audioPath),
        analysis_sha256: digest(analysisPath), metadata: fullMetadata,
        options: { useGemini: options.useGemini && Boolean(key), videoMode: options.videoMode, fetchLyrics: options.fetchLyrics },
      }, rewrite || !skipExisting);
      log(`Ready: ${destination}\nRescan songs in Clone Hero.`);
      return { status: 'success', outputPath: destination, ...(keepTemp ? { workDir } : {}) };
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      log(`Failed: ${error.message}`);
      return { status: 'failed', error: error.message, ...(keepTemp && workDir ? { workDir } : {}) };
    } finally {
      if (workDir && !keepTemp) fs.rmSync(workDir, { recursive: true, force: true });
      if (workDir && keepTemp) log(`Temporary files kept: ${workDir}`);
      releaseLock?.();
    }
  }

  async function processPlaylist(input, settings = {}) {
    const options = { ...DEFAULTS, ...settings };
    const result = { status: 'failed', succeeded: 0, skipped: 0, failed: 0, results: [] };
    try {
      const spotify = parseSpotifyInput(input, 'playlist');
      await ready(options.signal);
      const playlist = await (deps.resolvePlaylist || ((url) => jsonScript('playlist.py', [url], { signal: options.signal })))(spotify.url);
      if (!Array.isArray(playlist?.tracks) || !playlist.tracks.length) throw new Error('No public, playable tracks found. The playlist may be private or empty.');
      log(`Playlist: ${playlist.playlist_name || 'Playlist'} (${playlist.tracks.length} tracks exposed by Spotify's public embed).`);
      const seen = new Set();
      const delay = options.rateLimitMs ?? (options.useGemini && (options.geminiKey || process.env.GEMINI_API_KEY) ? 30000 : 5000);
      for (let i = 0; i < playlist.tracks.length; i++) {
        if (options.signal?.aborted) throw abortError();
        const url = playlist.tracks[i].url || playlist.tracks[i].spotify_url;
        let track;
        try {
          const id = parseSpotifyInput(url, 'track').id;
          if (seen.has(id)) { result.skipped++; continue; }
          seen.add(id);
          log(`Track ${i + 1}/${playlist.tracks.length}`);
          track = await runPipeline(url, options);
        } catch (error) {
          if (error.name === 'AbortError') throw error;
          track = { status: 'failed', error: error.message };
          log(`Failed: ${error.message}`);
        }
        result.results.push(track);
        if (track.status === 'success') result.succeeded++;
        else if (track.status === 'skipped') result.skipped++;
        else result.failed++;
        if (i < playlist.tracks.length - 1 && track.status !== 'skipped' && delay > 0) await (deps.sleep || sleep)(delay, options.signal);
      }
      result.status = result.failed ? 'failed' : 'success';
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      result.failed++;
      log(`Playlist failed: ${error.message}`);
    }
    log(`Playlist result: ${result.succeeded} succeeded, ${result.skipped} skipped, ${result.failed} failed.`);
    return result;
  }

  return { runPipeline, processPlaylist };
}

module.exports = { createPipeline };
