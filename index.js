#!/usr/bin/env node

// dotenv is optional: a missing node_modules should not stop the CLI, it just
// means .env is not read.
try {
  require('dotenv').config({ path: require('path').join(__dirname, '.env'), quiet: true });
} catch (err) {
  if (err.code !== 'MODULE_NOT_FOUND') throw err;
}

const { spawn, spawnSync, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');
const { downloadSong } = require('./lib/download');
const { generateSongIni } = require('./lib/songini');
const { isYouTubeUrl, resolveYouTube } = require('./lib/youtube');

// SONGHERO_OUTPUT (e.g. in .env) points at the Clone Hero Songs folder; ~ is expanded
const CLONE_HERO_DIR = process.env.SONGHERO_OUTPUT
  ? process.env.SONGHERO_OUTPUT.replace(/^~(?=$|\/)/, require('os').homedir())
  : path.join(require('os').homedir(), 'Desktop', 'Clone Hero');
const OUTPUT_DIR = path.join(__dirname, 'output');
const GEMINI_KEY = process.env.GEMINI_API_KEY || '';
// gemini uses GEMINI_API_KEY; claude and codex shell out to the locally logged-in
// Claude Code / Codex CLI, so they run on the user's subscription with no API key.
const AI_PROVIDERS = ['gemini', 'claude', 'codex'];
const DEFAULT_AI = (process.env.SONGHERO_AI || 'gemini').toLowerCase();
// Drum charting needs Demucs (PyTorch) in the analysis venv; SONGHERO_DRUMS=0 turns it off
const DEFAULT_DRUMS = !['0', 'false', 'off', 'no'].includes((process.env.SONGHERO_DRUMS || '').toLowerCase());

// Windows Python installs expose `python`, not `python3`, so probe for a real
// Python 3 instead of hardcoding one name. The installer puts the analysis
// dependencies in a venv (Debian and Ubuntu refuse a plain pip install into the
// system Python), so prefer that interpreter when it exists.
function pythonCandidates() {
  const win = process.platform === 'win32';
  const candidates = [];
  if (process.env.SONGHERO_PYTHON) {
    candidates.push(process.env.SONGHERO_PYTHON);
  }
  const venv = path.join(
    require('os').homedir(),
    '.songhero',
    'venv',
    win ? 'Scripts' : 'bin',
    win ? 'python.exe' : 'python'
  );
  if (fs.existsSync(venv)) {
    candidates.push(venv);
  }
  candidates.push(...(win ? ['python', 'python3', 'py'] : ['python3', 'python']));
  return candidates;
}

function resolvePython() {
  const candidates = pythonCandidates();
  const usable = [];
  for (const candidate of candidates) {
    const res = spawnSync(candidate, ['-c', 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)'], {
      stdio: 'ignore',
    });
    if (!res.error && res.status === 0) {
      usable.push(candidate);
    }
  }
  // find_spec instead of a real import: librosa takes seconds to load.
  for (const candidate of usable) {
    const res = spawnSync(
      candidate,
      ['-c', 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("librosa") else 1)'],
      { stdio: 'ignore' }
    );
    if (!res.error && res.status === 0) {
      return candidate;
    }
  }
  return usable[0] || candidates[candidates.length - 1];
}

const PYTHON = resolvePython();

function showBanner() {
  console.log('🎸 SongHero - AI-Powered Clone Hero Chart Generator');
  console.log('═══════════════════════════════════════════════════\n');
}

function showHelp() {
  console.log(`
SongHero CLI - Generate Clone Hero charts from Spotify links

Usage:
  songhero <url>                  Paste a Spotify link (track or playlist) or a YouTube video link
  songhero -                      Interactive REPL
  songhero -- <command> [args]    Shell-friendly command mode

Auto-detects Spotify track vs playlist URLs. Defaults to Gemini AI + karaoke lyrics enabled.

Options (to disable defaults):
  --ai <provider>   AI for note mapping: gemini (API key), claude or codex
                    (uses your logged-in Claude Code / Codex CLI, no API key).
                    Default: gemini, or $SONGHERO_AI
  --no-ai           Disable AI enhancement (alias: --no-gemini)
  --no-lyrics       Disable karaoke lyrics
  --no-drums        Skip drum charting (needs Demucs; default on, or $SONGHERO_DRUMS=0)
  --youtube <url>   With a Spotify link: use this YouTube video for audio/video
                    instead of searching YouTube
  --artist <name>   Override the artist name (useful for YouTube links)
  --title <name>    Override the song title
  --no-video        Skip video download
  --video           Force video download
  --no-skip-existing   Process songs even if already charted
  --rewrite         Overwrite existing charts
  --rate-limit <ms> Milliseconds between tracks (default: 5000, 30000 with Gemini)
  --output <dir>    Chart output directory (default: ~/Desktop/Clone Hero, or $SONGHERO_OUTPUT)
  --keep-temp       Keep temp files

Commands (via songhero --):
  generate <url>     Chart a single Spotify track or YouTube video
  playlist <url>     Chart an entire Spotify playlist
  keys               Show API key status
  keys set <service> <value>  Set an API key (gemini)
  help               Show this help

Interactive mode (songhero -) supports: generate, playlist, ai <provider>|off, gemini on|off,
drums on|off, lyrics on|off, video on|off|auto, output <dir>, options, help, exit

Examples:
  songhero https://open.spotify.com/track/xxx
  songhero https://open.spotify.com/playlist/xxx
  songhero spotify:track:xxx --no-gemini
  songhero https://open.spotify.com/track/xxx --ai claude
  songhero https://www.youtube.com/watch?v=xxx
  songhero https://open.spotify.com/track/xxx --youtube https://youtu.be/xxx
  songhero -
`);
}

const ENV_FILE = path.join(__dirname, '.env');

function readEnvKeys() {
  const keys = {};
  if (fs.existsSync(ENV_FILE)) {
    const content = fs.readFileSync(ENV_FILE, 'utf-8');
    for (const line of content.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const eqIdx = trimmed.indexOf('=');
      if (eqIdx > 0) keys[trimmed.slice(0, eqIdx)] = trimmed.slice(eqIdx + 1);
    }
  }
  return keys;
}

function setEnvKey(key, value) {
  let lines = [];
  if (fs.existsSync(ENV_FILE)) {
    lines = fs.readFileSync(ENV_FILE, 'utf-8').split('\n');
  }
  let found = false;
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (trimmed && !trimmed.startsWith('#') && trimmed.startsWith(key + '=')) {
      lines[i] = `${key}=${value}`;
      found = true;
      break;
    }
  }
  if (!found) lines.push(`${key}=${value}`);
  process.env[key] = value;
  fs.writeFileSync(ENV_FILE, lines.join('\n'));
}

function getKeyStatus() {
  const keys = readEnvKeys();
  return {
    gemini: keys.GEMINI_API_KEY ? '✓ Set' : '✗ Not set',
  };
}

function showInteractiveHelp() {
  console.log(`
🎸 SongHero Interactive Commands
─────────────────────────────────
  generate <url>    Run the full pipeline on a Spotify or YouTube URL
  gen <url>         Alias for generate
  playlist <url>    Process all tracks in a Spotify playlist
  lyrics on|off     Toggle lyrics fetching (default: on)
  drums on|off      Toggle drum charting (default: on)
  ai gemini|claude|codex|off  Choose AI provider, or turn AI off
  gemini on|off     Toggle Gemini AI enhancement (default: on)
  video on|off|auto Video download mode (default: auto)
  skip-existing on|off  Skip already charted songs (default: on)
  rate-limit <ms>   Set delay between tracks in playlists
  output <dir>      Set output directory
  keys              Show API key status
  keys set gemini <key>
  options           Show current session settings
  help, ?           Show this help
  exit, quit        Exit SongHero
`);
}

function showSessionOptions(session) {
  const ks = getKeyStatus();
  console.log('\n📋 Current Settings');
  console.log('──────────────────');
  console.log(`  Output:       ${session.outputBase}`);
  console.log(`  AI:           ${session.useAI ? `ON (${session.aiProvider})` : 'OFF'}`);
  console.log(`  Video:        ${session.noVideo ? 'OFF' : (session.forceVideo ? 'FORCED' : 'AUTO')}`);
  console.log(`  Lyrics:       ${session.fetchLyrics ? 'ON' : 'OFF'}`);
  console.log(`  Drums:        ${session.chartDrums ? 'ON' : 'OFF'}`);
  console.log(`  Skip Existing: ${session.skipExisting ? 'ON' : 'OFF'}`);
  console.log(`  Rate Limit:   ${session.rateLimitMs != null ? session.rateLimitMs + 'ms' : 'auto'}`);
  console.log(`  Keep Temp:    ${session.keepTemp ? 'ON' : 'OFF'}`);
  console.log(`  Gemini Key:   ${ks.gemini}\n`);
}

function sanitize(name) {
  return name.replace(/[<>:"/\\|?*]/g, '').replace(/\s+/g, ' ').trim();
}

function parseAiProvider(args) {
  const idx = args.indexOf('--ai');
  if (idx === -1) return DEFAULT_AI;
  const provider = (args[idx + 1] || '').toLowerCase();
  if (!AI_PROVIDERS.includes(provider)) {
    console.error(`Unknown --ai provider: ${provider || '(missing)'}. Use: ${AI_PROVIDERS.join(', ')}`);
    process.exit(1);
  }
  return provider;
}

// Value of `--flag <value>`, or null when the flag is absent
function flagValue(args, flag) {
  const idx = args.indexOf(flag);
  return idx !== -1 && idx + 1 < args.length ? args[idx + 1] : null;
}

const VALUE_FLAGS = ['--ai', '--rate-limit', '--output', '--youtube', '--artist', '--title'];

// First argument that is a song/playlist link and not the value of a flag
function findSourceUrl(args) {
  return args.find((a, i) =>
    (a.startsWith('http') || a.startsWith('spotify:')) && !VALUE_FLAGS.includes(args[i - 1]));
}

function parseInlineOptions(args) {
  const rateLimitIdx = args.indexOf('--rate-limit');
  const youtubeUrl = flagValue(args, '--youtube');
  if (youtubeUrl && !isYouTubeUrl(youtubeUrl)) {
    console.error(`--youtube expects a YouTube link, got: ${youtubeUrl}`);
    process.exit(1);
  }
  const output = flagValue(args, '--output');
  return {
    ...(output ? { outputBase: output } : {}),
    youtubeUrl,
    artistOverride: flagValue(args, '--artist'),
    titleOverride: flagValue(args, '--title'),
    useAI: !args.includes('--no-gemini') && !args.includes('--no-ai'),
    chartDrums: args.includes('--drums') || (DEFAULT_DRUMS && !args.includes('--no-drums')),
    aiProvider: parseAiProvider(args),
    forceVideo: args.includes('--video'),
    noVideo: args.includes('--no-video'),
    keepTemp: args.includes('--keep-temp'),
    fetchLyrics: !args.includes('--no-lyrics'),
    rewrite: args.includes('--rewrite'),
    skipExisting: !args.includes('--no-skip-existing'),
    rateLimitMs: rateLimitIdx !== -1 ? parseInt(args[rateLimitIdx + 1], 10) : null,
  };
}

// sourceUrl is a Spotify track or a YouTube video. With a Spotify link,
// options.youtubeUrl picks the exact video instead of searching YouTube.
async function runPipeline(sourceUrl, options = {}) {
  const {
    youtubeUrl = null,
    artistOverride = null,
    titleOverride = null,
    useAI = false,
    aiProvider = DEFAULT_AI,
    chartDrums = false,
    forceVideo = false,
    noVideo = false,
    keepTemp = false,
    outputBase = CLONE_HERO_DIR,
    geminiKey = GEMINI_KEY,
    fetchLyrics = false,
    rewrite = false,
    skipExisting = true,
  } = options;

  const fromYouTube = isYouTubeUrl(sourceUrl);
  let metadata;
  if (fromYouTube) {
    console.log('Step 1/5: Resolving YouTube video...');
    metadata = resolveYouTube(sourceUrl);
    if (metadata.error) {
      console.error(`  ✗ ${metadata.error}`);
      return false;
    }
  } else {
    console.log('Step 1/5: Resolving Spotify link...');
    try {
      const result = execSync(`"${PYTHON}" "${path.join(__dirname, 'python', 'spotify.py')}" "${sourceUrl}"`, {
        encoding: 'utf-8',
        timeout: 30000
      });
      metadata = JSON.parse(result);
      if (metadata.error) {
        console.error(`  ✗ ${metadata.error}`);
        return false;
      }
    } catch (e) {
      console.error('  ✗ Failed to resolve Spotify link:', e.message);
      return false;
    }
  }
  if (artistOverride) metadata.artist = artistOverride;
  if (titleOverride) metadata.name = titleOverride;
  console.log(`  ✓ ${metadata.artist} - ${metadata.name}`);
  if (fromYouTube && !artistOverride && !titleOverride) {
    console.log(`    (from "${metadata.youtube_title}" — fix with --artist/--title if wrong)`);
  }
  const videoSource = fromYouTube ? metadata.youtube_url : youtubeUrl;

  // Skip if already ingested and skipExisting is enabled
  const folderName = sanitize(`${metadata.artist} - ${metadata.name} (SongHero AI)`);
  const outputPath = path.join(outputBase, folderName);
  if (skipExisting && fs.existsSync(outputPath) && !rewrite) {
    console.log(`  ⏭ Skipping: "${folderName}" already exists. Use --no-skip-existing or --rewrite to process.`);
    return 'skipped';
  }

  console.log('\nStep 2/5: Downloading audio...');
  const workDir = path.join(OUTPUT_DIR, sanitize(`${metadata.artist} - ${metadata.name}`));
  
  let downloadInfo;
  try {
    const videoMode = noVideo ? 'off' : (forceVideo ? 'on' : 'auto');
    downloadInfo = await downloadSong(metadata.artist, metadata.name, workDir, videoMode, videoSource);
    console.log(`  ✓ Audio downloaded`);
    if (downloadInfo.hasVideo) {
      console.log(`  ✓ Music video downloaded`);
    } else {
      console.log(`  ⚠ No music video found`);
    }
  } catch (e) {
    console.error('  ✗ Download failed:', e.message);
    return false;
  }

  let lyricsData = null;
  if (fetchLyrics) {
    console.log('\nStep 2.5/5: Fetching lyrics...');
    try {
      const lyricsResult = execSync(
        `"${PYTHON}" "${path.join(__dirname, 'python', 'lyrics.py')}" "${metadata.name}" "${metadata.artist}"`,
        { encoding: 'utf-8', timeout: 30000 }
      );
      lyricsData = JSON.parse(lyricsResult);
      if (lyricsData.error) {
        console.log(`  ⚠ ${lyricsData.error}`);
        lyricsData = null;
      } else {
        const syncedTag = lyricsData.synced ? 'karaoke-synced' : 'plain text';
        console.log(`  ✓ Lyrics fetched from ${lyricsData.source} (${syncedTag}, ${lyricsData.line_count || 0} lines)`);
        const lyricsPath = path.join(workDir, 'lyrics.json');
        fs.writeFileSync(lyricsPath, JSON.stringify(lyricsData));
      }
    } catch (e) {
      console.log(`  ⚠ Lyrics fetch failed: ${e.message}`);
      lyricsData = null;
    }
  }

  console.log('\nStep 3/5: Analyzing audio with AI...');
  const audioFile = fs.readdirSync(workDir).find(f => f.startsWith('song.') && !f.endsWith('.ini'));
  if (!audioFile) {
    console.error('  ✗ No audio file found in download');
    return false;
  }
  
  const audioPath = path.join(workDir, audioFile);
  let analysis;
  try {
    const aiFlag = useAI ? `--ai ${aiProvider}` : '';
    const lyricsFlag = (fetchLyrics && lyricsData) ? `--lyrics-file "${path.join(workDir, 'lyrics.json')}"` : '';
    const result = execSync(`"${PYTHON}" "${path.join(__dirname, 'python', 'analyze.py')}" "${audioPath}" ${aiFlag} ${lyricsFlag}`, {
      encoding: 'utf-8',
      // An agent CLI call takes far longer than one REST request
      timeout: useAI && aiProvider !== 'gemini' ? 360000 : 120000,
      maxBuffer: 50 * 1024 * 1024,
      env: { 
        ...process.env, 
        GEMINI_API_KEY: geminiKey,
        SONG_NAME: metadata.name,
        SONG_ARTIST: metadata.artist,
        SPOTIFY_TEMPO: metadata.spotify_tempo || '',
        SPOTIFY_KEY: metadata.spotify_key !== undefined ? String(metadata.spotify_key) : '',
      }
    });
    analysis = JSON.parse(result);
    if (analysis.error) {
      console.error(`  ✗ ${analysis.error}`);
      return false;
    }
  } catch (e) {
    console.error('  ✗ Analysis failed:', e.message);
    return false;
  }
  
  console.log(`  ✓ Tempo: ${Math.round(analysis.tempo / 1000)} BPM`);
  console.log(`  ✓ Key: ${analysis.key}`);
  console.log(`  ✓ Sections detected: ${analysis.sections.length}`);
  console.log(`  ✓ Notes generated: ${Object.values(analysis.difficulties).reduce((s, n) => s + n.length, 0)}`);
  if (analysis.ai_enhanced) {
    console.log(`  ✓ AI enhancement applied (${analysis.ai_provider || aiProvider})`);
  }
  if (analysis.lyrics && analysis.lyrics.length > 0) {
    console.log(`  ✓ Lyrics synced: ${analysis.lyrics.length} events`);
  } else if (analysis.lyrics_offset_seconds > 10) {
    console.log(`  ⚠ Lyrics dropped: audio ${analysis.lyrics_offset_seconds.toFixed(0)}s longer than LRCLIB reference`);
  }

  const analysisJson = path.join(workDir, 'analysis.json');
  fs.writeFileSync(analysisJson, JSON.stringify(analysis));

  if (chartDrums) {
    console.log('\nStep 3.5/5: Charting drums...');
    try {
      const drumsAi = useAI ? `--ai ${aiProvider}` : '';
      const out = execSync(
        `"${PYTHON}" "${path.join(__dirname, 'python', 'drums.py')}" "${audioPath}" "${analysisJson}" --stem "${path.join(workDir, 'drums.wav')}" ${drumsAi}`,
        {
          encoding: 'utf-8',
          timeout: 900000,
          maxBuffer: 50 * 1024 * 1024,
          env: { ...process.env, GEMINI_API_KEY: geminiKey, SONG_NAME: metadata.name, SONG_ARTIST: metadata.artist },
        }
      );
      const drums = JSON.parse(out);
      if (drums.error) {
        console.log(`  ⚠ ${drums.error} — skipping drums`);
      } else {
        Object.assign(analysis.difficulties, drums.difficulties);
        analysis.has_drums = true;
        fs.writeFileSync(analysisJson, JSON.stringify(analysis));
        const hits = drums.stats && drums.stats.hits ? drums.stats.hits : {};
        const summary = Object.entries(hits).map(([k, v]) => `${k} ${v}`).join(', ');
        console.log(`  ✓ Drums charted (${summary})`);
        console.log(`  ✓ Hard/Medium/Easy planned per section (${drums.stats.planner || 'rules'})`);
        fs.writeFileSync(path.join(workDir, 'drum_plan.json'), JSON.stringify(drums.plan, null, 2));
      }
    } catch (e) {
      console.log(`  ⚠ Drum charting failed (continuing without): ${e.message.split('\n')[0]}`);
    }
  }

  console.log('\nStep 4/5: Generating chart file...');
  
  const metadataJson = path.join(workDir, 'metadata.json');
  const fullMetadata = {
    name: metadata.name,
    artist: metadata.artist,
    album: metadata.album || '',
    genre: 'rock',
    year: metadata.year || '',
    duration_ms: analysis.duration_ms || metadata.duration_ms || 0
  };
  fs.writeFileSync(metadataJson, JSON.stringify(fullMetadata));
  
  try {
    const chart = execSync(`"${PYTHON}" "${path.join(__dirname, 'python', 'generate_chart.py')}" "${analysisJson}" "${metadataJson}"`, {
      encoding: 'utf-8',
      timeout: 10000
    });
    fs.writeFileSync(path.join(workDir, 'notes.chart'), chart);
    console.log(`  ✓ notes.chart generated`);
  } catch (e) {
    console.error('  ✗ Chart generation failed:', e.message);
    return false;
  }

  console.log('\nStep 5/5: Packaging for Clone Hero...');
  
  const hasVideo = downloadInfo.hasVideo && !noVideo;
  const songIni = generateSongIni(fullMetadata, analysis, hasVideo);
  fs.writeFileSync(path.join(workDir, 'song.ini'), songIni);
  
  if (rewrite && fs.existsSync(outputPath)) {
    fs.rmSync(outputPath, { recursive: true });
    console.log(`  ↳ Overwriting existing chart`);
  }
  fs.mkdirSync(outputPath, { recursive: true });
  
  const filesToCopy = ['notes.chart', 'song.ini', audioFile];
  if (hasVideo && fs.existsSync(path.join(workDir, 'video.mp4'))) {
    filesToCopy.push('video.mp4');
  }
  if (fs.existsSync(path.join(workDir, 'album.jpg'))) {
    filesToCopy.push('album.jpg');
  }
  
  for (const file of filesToCopy) {
    const src = path.join(workDir, file);
    const dest = path.join(outputPath, file);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, dest);
    }
  }
  
  console.log(`  ✓ Packaged to: ${outputPath}`);
  
  if (!keepTemp) {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
  
  console.log('\n═══════════════════════════════════════════════════');
  console.log('✨ Done! Your song is ready in Clone Hero.');
  console.log(`   Location: ${outputPath}`);
  console.log('   Restart Clone Hero or rescan songs to see it.\n');
  
  return true;
}

async function processPlaylist(playlistUrl, options = {}) {
  console.log('🎸 SongHero - Playlist Mode');
  console.log('═══════════════════════════════════════════════════\n');
  console.log('Fetching playlist tracks...');

  let playlistData;
  try {
    const result = execSync(
      `"${PYTHON}" "${path.join(__dirname, 'python', 'playlist.py')}" "${playlistUrl}"`,
      { encoding: 'utf-8', timeout: 30000 }
    );
    playlistData = JSON.parse(result);
    if (playlistData.error) {
      console.error(`  ✗ ${playlistData.error}`);
      return;
    }
  } catch (e) {
    console.error('  ✗ Failed to fetch playlist:', e.message);
    return;
  }

  const tracks = playlistData.tracks || [];
  if (tracks.length === 0) {
    console.log('  No tracks found in playlist.');
    return;
  }

  console.log(`  ✓ ${playlistData.playlist_name || 'Playlist'}: ${tracks.length} tracks\n`);
  
  const RATE_LIMIT_MS = options.rateLimitMs != null 
    ? options.rateLimitMs 
    : (options.useAI && options.aiProvider === 'gemini' ? 30000 : 5000);

  for (let i = 0; i < tracks.length; i++) {
    const track = tracks[i];
    console.log(`\n🎵 [${i + 1}/${tracks.length}] ${track.artist} - ${track.name}`);
    console.log('───────────────────────────────────────────────');

    const result = await runPipeline(track.url || track.spotify_url, options);
    
    if (i < tracks.length - 1 && result !== 'skipped') {
      const waitSec = Math.round(RATE_LIMIT_MS / 1000);
      console.log(`\n⏳ Waiting ${waitSec}s before next song (rate limit)...`);
      await new Promise(resolve => setTimeout(resolve, RATE_LIMIT_MS));
    }
  }

  console.log('\n═══════════════════════════════════════════════════');
  console.log(`✨ Playlist complete! ${tracks.length} songs processed.`);
  console.log(`   Output: ${options.outputBase || CLONE_HERO_DIR}\n`);
}

function buildSession(defaults = {}) {
  return {
    useAI: true,
    aiProvider: DEFAULT_AI,
    chartDrums: DEFAULT_DRUMS,
    forceVideo: false,
    noVideo: false,
    keepTemp: false,
    outputBase: CLONE_HERO_DIR,
    geminiKey: GEMINI_KEY,
    fetchLyrics: true,
    skipExisting: true,
    rateLimitMs: null,
    ...defaults,
  };
}

function interactiveMode() {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: 'songhero> '
  });

  const session = buildSession();

  console.log('🎸 SongHero Interactive CLI');
  console.log('Type "help" for commands, "exit" to quit.\n');
  rl.prompt();

  rl.on('line', async (line) => {
    const input = line.trim();
    if (!input) {
      rl.prompt();
      return;
    }

    const parts = input.match(/(?:[^\s"]+|"[^"]*")+/g) || [];
    const cmd = parts[0].toLowerCase();
    const cmdArgs = parts.slice(1).map(a => a.replace(/^"|"$/g, ''));

    switch (cmd) {
      case 'help':
      case '?':
        showInteractiveHelp();
        break;

      case 'exit':
      case 'quit':
        rl.close();
        return;

      case 'generate':
      case 'gen': {
        if (cmdArgs.length < 1) {
          console.log('Usage: generate <spotify_url>');
          break;
        }
        const url = cmdArgs[0];
        showBanner();
        const ok = await runPipeline(url, session);
        console.log(ok ? 'Type another command.\n' : 'Pipeline failed. Check errors above.\n');
        break;
      }

      case 'playlist': {
        if (cmdArgs.length < 1) {
          console.log('Usage: playlist <spotify_playlist_url>');
          break;
        }
        await processPlaylist(cmdArgs[0], session);
        break;
      }

      case 'gemini':
        if (cmdArgs[0] === 'on') { session.useAI = true; session.aiProvider = 'gemini'; }
        else if (cmdArgs[0] === 'off') session.useAI = false;
        else { console.log('Usage: gemini on|off'); break; }
        console.log(`  AI: ${session.useAI ? `ON (${session.aiProvider})` : 'OFF'}`);
        break;

      case 'ai': {
        const choice = (cmdArgs[0] || '').toLowerCase();
        if (choice === 'off') session.useAI = false;
        else if (AI_PROVIDERS.includes(choice)) { session.useAI = true; session.aiProvider = choice; }
        else if (choice) { console.log(`Usage: ai ${AI_PROVIDERS.join('|')}|off`); break; }
        console.log(`  AI: ${session.useAI ? `ON (${session.aiProvider})` : 'OFF'}`);
        break;
      }

      case 'drums':
        if (cmdArgs[0] === 'on') session.chartDrums = true;
        else if (cmdArgs[0] === 'off') session.chartDrums = false;
        else { console.log('Usage: drums on|off'); break; }
        console.log(`  Drums: ${session.chartDrums ? 'ON' : 'OFF'}`);
        break;

      case 'lyrics':
        if (cmdArgs[0] === 'on') session.fetchLyrics = true;
        else if (cmdArgs[0] === 'off') session.fetchLyrics = false;
        else { console.log('Usage: lyrics on|off'); break; }
        console.log(`  Lyrics: ${session.fetchLyrics ? 'ON' : 'OFF'}`);
        break;

      case 'video':
        if (cmdArgs[0] === 'on' || cmdArgs[0] === 'force') {
          session.forceVideo = true; session.noVideo = false;
        } else if (cmdArgs[0] === 'off') {
          session.noVideo = true; session.forceVideo = false;
        } else if (cmdArgs[0] === 'auto') {
          session.forceVideo = false; session.noVideo = false;
        } else { console.log('Usage: video on|off|auto'); break; }
        console.log(`  Video: ${session.noVideo ? 'OFF' : (session.forceVideo ? 'FORCED' : 'AUTO')}`);
        break;

      case 'skip-existing':
        if (cmdArgs[0] === 'on') session.skipExisting = true;
        else if (cmdArgs[0] === 'off') session.skipExisting = false;
        else { console.log('Usage: skip-existing on|off'); break; }
        console.log(`  Skip Existing: ${session.skipExisting ? 'ON' : 'OFF'}`);
        break;

      case 'rate-limit':
        if (cmdArgs.length < 1) {
          console.log(`  Current rate limit: ${session.rateLimitMs != null ? session.rateLimitMs + 'ms' : 'auto'}`);
        } else {
          const ms = parseInt(cmdArgs[0], 10);
          if (isNaN(ms) || ms < 0) {
            console.log('Usage: rate-limit <milliseconds>');
          } else {
            session.rateLimitMs = ms;
            console.log(`  Rate limit set to: ${ms}ms`);
          }
        }
        break;

      case 'output':
        if (cmdArgs.length < 1) {
          console.log(`  Current output: ${session.outputBase}`);
        } else {
          session.outputBase = cmdArgs.join(' ');
          console.log(`  Output set to: ${session.outputBase}`);
        }
        break;

      case 'keys': {
        if (cmdArgs.length === 0 || (cmdArgs.length === 1 && cmdArgs[0] === 'show')) {
          const ks = getKeyStatus();
          console.log('\n🔑 API Keys');
          console.log('─────────────');
          console.log(`  Gemini:         ${ks.gemini}`);
          console.log(`\n  File: ${ENV_FILE}\n`);
        } else if (cmdArgs[0] === 'set' && cmdArgs.length >= 3) {
          const service = cmdArgs[1];
          const value = cmdArgs.slice(2).join(' ');
          const keyMap = {
            'gemini': 'GEMINI_API_KEY',
          };
          const envKey = keyMap[service];
          if (!envKey) {
            console.log(`Unknown key: ${service}. Use: gemini`);
          } else {
            setEnvKey(envKey, value);
            session.geminiKey = process.env.GEMINI_API_KEY || '';
            console.log(`  ✓ ${service} key saved to .env`);
          }
        } else {
          console.log('Usage: keys [show] | keys set <gemini> <value>');
        }
        break;
      }

      case 'options':
        showSessionOptions(session);
        break;

      default:
        console.log(`Unknown command: ${cmd}. Type "help" for available commands.`);
    }

    rl.prompt();
  }).on('close', () => {
    console.log('\nGoodbye! 🎸');
    process.exit(0);
  });
}

async function execCommand(cmdArgs) {
  const cmd = cmdArgs[0]?.toLowerCase();
  const args = cmdArgs.slice(1);

  switch (cmd) {
    case 'generate':
    case 'gen': {
      if (args.length < 1) {
        console.error('Usage: songhero -- generate <spotify_or_youtube_url> [--youtube <url>] [--artist <name>] [--title <name>] [--ai gemini|claude|codex] [--no-ai] [--no-lyrics] [--video]');
        process.exit(1);
      }
      const inline = parseInlineOptions(args);
      const url = findSourceUrl(args);
      if (!url) {
        console.error('No Spotify or YouTube URL found in arguments');
        process.exit(1);
      }
      showBanner();
      const ok = await runPipeline(url, buildSession(inline));
      if (!ok) process.exit(1);
      break;
    }

    case 'playlist': {
      if (args.length < 1) {
        console.error('Usage: songhero -- playlist <spotify_playlist_url> [--ai gemini|claude|codex] [--no-ai] [--no-lyrics]');
        process.exit(1);
      }
      const inline = parseInlineOptions(args);
      const url = args.find(a => a.includes('playlist'));
      if (!url) {
        console.error('No playlist URL found in arguments');
        process.exit(1);
      }
      await processPlaylist(url, buildSession(inline));
      break;
    }

    case 'help':
      showBanner();
      showHelp();
      break;

    case 'keys': {
      if (args.length === 0 || args[0] === 'show') {
        const ks = getKeyStatus();
        console.log('🔑 API Keys');
        console.log('─────────────');
        console.log(`  Gemini:         ${ks.gemini}`);
        console.log(`\n  File: ${ENV_FILE}`);
      } else if (args[0] === 'set' && args.length >= 3) {
        const service = args[1];
        const value = args.slice(2).join(' ');
        const keyMap = {
          'gemini': 'GEMINI_API_KEY',
        };
        const envKey = keyMap[service];
        if (!envKey) {
          console.error(`Unknown key: ${service}. Use: gemini`);
          process.exit(1);
        }
        setEnvKey(envKey, value);
        console.log(`✓ ${service} key saved to .env`);
      } else {
        console.error('Usage: songhero -- keys [show] | songhero -- keys set <gemini> <value>');
        process.exit(1);
      }
      break;
    }

    default:
      console.error(`Unknown command: ${cmd}. Try: songhero -- help`);
      process.exit(1);
  }
}

async function main() {
  const args = process.argv.slice(2);

  // -- command mode: songhero -- <cmd> [args]
  if (args[0] === '--') {
    const cmdArgs = args.slice(1);
    if (cmdArgs.length === 0) {
      showBanner();
      showHelp();
      process.exit(0);
    }
    await execCommand(cmdArgs);
    return;
  }

  // Interactive mode: songhero -
  if (args.length === 1 && args[0] === '-') {
    interactiveMode();
    return;
  }
  
  // Legacy one-shot / auto-detect mode: songhero <url> [options]
  if (args.length < 1 || args.includes('--help') || args.includes('-h')) {
    showBanner();
    showHelp();
    process.exit(0);
  }

  const url = args[0];
  const options = buildSession(parseInlineOptions(args));

  showBanner();

  if (isYouTubeUrl(url) && /[?&]list=|\/playlist/.test(url) && !/[?&]v=|youtu\.be\//.test(url)) {
    console.error('YouTube playlists are not supported yet — pass a single video link.');
    process.exit(1);
  }

  // Auto-detect: playlist vs track (a YouTube watch link with &list= is one video)
  if (!isYouTubeUrl(url) && url.includes('playlist')) {
    await processPlaylist(url, options);
  } else {
    const ok = await runPipeline(url, options);
    if (!ok) process.exit(1);
  }
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
