const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

// Clone Hero's background video player only decodes H.264 reliably. YouTube's
// "best" 1080p mp4 stream is usually AV1 now, which loads as a black screen.
const VIDEO_MAX_HEIGHT = 720;

function getCookieArgs() {
  const platform = process.platform;
  let browsers;
  if (platform === 'darwin') {
    // Chrome first — Safari keychain blocks yt-dlp cookie extraction on macOS
    browsers = ['chrome', 'firefox', 'chromium', 'edge', 'brave'];
  } else if (platform === 'linux') {
    browsers = ['chrome', 'firefox', 'chromium', 'brave'];
  } else {
    browsers = ['chrome', 'firefox', 'edge', 'brave', 'chromium'];
  }
  
  const home = require('os').homedir();
  const browserPaths = {
    chrome: [path.join(home, 'Library/Application Support/Google/Chrome'), path.join(home, '.config/google-chrome')],
    safari: [path.join(home, 'Library/Containers/com.apple.Safari')],
    firefox: [path.join(home, 'Library/Application Support/Firefox'), path.join(home, '.mozilla/firefox')],
    chromium: [path.join(home, 'Library/Application Support/Chromium'), path.join(home, '.config/chromium')],
    edge: [path.join(home, 'Library/Application Support/Microsoft Edge')],
    brave: [path.join(home, 'Library/Application Support/BraveSoftware/Brave-Browser')],
  };

  for (const browser of browsers) {
    const paths = browserPaths[browser] || [];
    for (const p of paths) {
      if (fs.existsSync(p)) {
        return ['--cookies-from-browser', browser];
      }
    }
  }
  return [];
}

const NON_VIDEO_PATTERNS = [
  /lyric\s*video/i,
  /official\s*lyric/i,
  /\blyrics\b/i,
  /official\s*audio/i,
  /audio\s*only/i,
  /\bvisuali[sz]er\b/i,
  /\bvisuali[sz]ation\b/i,
  /static\s*image/i,
  /album\s*art/i,
  /\btopic\b/i,
  /-\s*$/,
];

function isMusicVideo(title) {
  for (const pattern of NON_VIDEO_PATTERNS) {
    if (pattern.test(title)) {
      return false;
    }
  }
  return true;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function probeVideoCodec(filePath) {
  const res = spawnSync('ffprobe', [
    '-v', 'error',
    '-select_streams', 'v:0',
    '-show_entries', 'stream=codec_name',
    '-of', 'default=nw=1:nk=1',
    filePath,
  ], { encoding: 'utf-8', timeout: 60000 });
  if (res.status !== 0 || !res.stdout) return null;
  return res.stdout.trim() || null;
}

// Returns true if the file is a Clone Hero playable H.264 mp4 (transcoding if needed).
function ensureCloneHeroVideo(videoPath) {
  const codec = probeVideoCodec(videoPath);
  if (!codec) return false;
  if (codec === 'h264') return true;

  console.log(`  ↻ Re-encoding video (${codec} → H.264) for Clone Hero...`);
  const tmpPath = videoPath.replace(/\.mp4$/, '.h264.mp4');
  const res = spawnSync('ffmpeg', [
    '-y',
    '-i', videoPath,
    '-c:v', 'libx264',
    '-preset', 'veryfast',
    '-crf', '23',
    '-profile:v', 'high',
    '-pix_fmt', 'yuv420p',
    '-vf', `scale=-2:min(${VIDEO_MAX_HEIGHT}\\,ih)`,
    '-an',
    '-movflags', '+faststart',
    tmpPath,
  ], { stdio: 'ignore', timeout: 900000 });

  if (res.status !== 0 || !fs.existsSync(tmpPath)) {
    try { fs.unlinkSync(tmpPath); } catch {}
    return false;
  }
  fs.renameSync(tmpPath, videoPath);
  return true;
}

// videoMode: 'auto' (default, skip lyric videos/visualizers), 'on', or 'off'
async function downloadSong(artist, title, outputDir, videoMode = 'auto') {
  fs.mkdirSync(outputDir, { recursive: true });
  const searchQuery = `${artist} - ${title}`;
  console.log(`  Searching YouTube for: ${searchQuery}`);

  const maxRetries = 3;
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await attemptDownload(artist, title, outputDir, searchQuery, videoMode);
    } catch (e) {
      if (attempt === maxRetries - 1) throw e;
      console.error(`  ⚠ Download attempt ${attempt + 1} failed: ${e.message}`);
      const delay = Math.pow(2, attempt) * 5000;
      console.log(`  Retry ${attempt + 2}/${maxRetries} in ${delay / 1000}s...`);
      await sleep(delay);
      // Clean leftover files from failed attempt
      try {
        for (const f of fs.readdirSync(outputDir)) {
          try { fs.unlinkSync(path.join(outputDir, f)); } catch {}
        }
      } catch {}
    }
  }
}

function attemptDownload(artist, title, outputDir, searchQuery, videoMode) {
  return new Promise((resolve, reject) => {
    const cookieArgs = getCookieArgs();
    
    const audioArgs = [
      ...cookieArgs,
      'ytsearch1:' + searchQuery,
      '-f', 'bestaudio[ext=webm]/bestaudio',
      '-x',
      '--audio-format', 'opus',
      '--audio-quality', '0',
      '-o', path.join(outputDir, 'song.%(ext)s'),
      '--write-thumbnail',
      '--convert-thumbnails', 'jpg',
      '--no-playlist',
      '--no-warnings',
      '--no-simulate',
      '--print', 'after_move:%(title)s|||%(duration)s|||%(webpage_url)s',
      '--sleep-requests', '1.5',
      '--sleep-interval', '3',
      '--max-sleep-interval', '15',
      '--retries', '5',
    ];

    const audio = spawn('yt-dlp', audioArgs, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';

    audio.stdout.on('data', d => stdout += d.toString());
    audio.stderr.on('data', d => stderr += d.toString());

    audio.on('close', (code) => {
      if (code !== 0) {
        console.error('  yt-dlp error:', stderr.slice(-300));
        reject(new Error(`yt-dlp exited with code ${code}`));
        return;
      }

      const lines = stdout.trim().split('\n');
      const lastLine = lines[lines.length - 1] || '';
      const parts = lastLine.split('|||');
      const videoTitle = parts[0] || `${artist} - ${title}`;
      const duration = parseFloat(parts[1]) || 0;
      const youtubeUrl = parts[2] || '';

      // Rename thumbnail to album.jpg
      const allFiles = fs.readdirSync(outputDir);
      for (const f of allFiles) {
        if (f.startsWith('song.') && (f.endsWith('.jpg') || f.endsWith('.webp') || f.endsWith('.png'))) {
          const dest = path.join(outputDir, 'album.jpg');
          if (!fs.existsSync(dest)) {
            fs.renameSync(path.join(outputDir, f), dest);
          } else {
            fs.unlinkSync(path.join(outputDir, f));
          }
        }
      }

      const hasAudio = allFiles.some(f => f.startsWith('song.') && f.endsWith('.opus'));
      if (!hasAudio) {
        console.error('  All files:', allFiles.join(', '));
        reject(new Error('Audio download failed - no song.opus found'));
        return;
      }

      // Convert WebM Opus to proper Ogg Opus container for Clone Hero
      const audioFile = allFiles.find(f => f.startsWith('song.') && f.endsWith('.opus'));
      if (audioFile) {
        const audioPath = path.join(outputDir, audioFile);
        const tmpPath = path.join(outputDir, 'song_convert.opus');
        try {
          const { execSync } = require('child_process');
          execSync(`ffmpeg -i "${audioPath}" -c:a copy "${tmpPath}" -y 2>/dev/null`, { timeout: 30000 });
          fs.renameSync(tmpPath, audioPath);
        } catch (e) {
          // If ffmpeg fails, keep the original — it might already be Ogg
        }
      }

      const finish = (hasVideo) => resolve({
        artist,
        title: videoTitle.includes(' - ') ? videoTitle.split(' - ').slice(1).join(' - ') : videoTitle,
        durationMs: Math.round(duration * 1000),
        youtubeUrl,
        hasVideo,
      });

      if (videoMode === 'off') {
        finish(false);
        return;
      }

      // Check if the video title indicates it's actually a music video
      if (videoMode !== 'on' && !isMusicVideo(videoTitle)) {
        console.log(`  ⚠ Skipping video: "${videoTitle}" is not a music video`);
        finish(false);
        return;
      }

      // Download video — prefer an H.264 (avc1) stream, the only codec Clone
      // Hero decodes reliably. Anything else gets transcoded below.
      const videoArgs = [
        ...cookieArgs,
        'ytsearch1:' + searchQuery,
        '-f', [
          `bestvideo[height<=${VIDEO_MAX_HEIGHT}][vcodec^=avc1][ext=mp4]`,
          `best[height<=${VIDEO_MAX_HEIGHT}][vcodec^=avc1][ext=mp4]`,
          `bestvideo[height<=${VIDEO_MAX_HEIGHT}]`,
          `best[height<=${VIDEO_MAX_HEIGHT}]`,
        ].join('/'),
        '--remux-video', 'mp4',
        '--max-filesize', '100M',
        '-o', path.join(outputDir, 'video.%(ext)s'),
        '--no-playlist',
        '--no-warnings',
        '--sleep-requests', '1.5',
        '--sleep-interval', '3',
        '--max-sleep-interval', '15',
        '--retries', '3',
      ];

      const videoPath = path.join(outputDir, 'video.mp4');
      const dropVideo = (reason) => {
        console.log(`  ⚠ ${reason}, packaging audio only`);
        for (const f of fs.readdirSync(outputDir)) {
          if (f.startsWith('video.')) {
            try { fs.unlinkSync(path.join(outputDir, f)); } catch {}
          }
        }
        finish(false);
      };

      const video = spawn('yt-dlp', videoArgs, { stdio: 'ignore' });
      video.on('error', (e) => dropVideo(`Video download failed (${e.message})`));
      video.on('close', (videoCode) => {
        if (videoCode !== 0) {
          dropVideo('Video download failed');
          return;
        }

        // Remux to mp4 rather than blindly renaming: a .webm renamed to .mp4
        // is unplayable in Clone Hero.
        const vfiles = fs.readdirSync(outputDir).filter(f => f.startsWith('video.') && f !== 'video.mp4');
        for (const vf of vfiles) {
          const vpath = path.join(outputDir, vf);
          if (fs.existsSync(videoPath)) {
            try { fs.unlinkSync(vpath); } catch {}
            continue;
          }
          const remux = spawnSync('ffmpeg', ['-y', '-i', vpath, '-c', 'copy', videoPath], {
            stdio: 'ignore',
            timeout: 300000,
          });
          if (remux.status !== 0 && fs.existsSync(vpath)) {
            // Copy-remux can fail on codecs mp4 cannot hold; let ffmpeg
            // transcode from the original instead.
            try { fs.unlinkSync(videoPath); } catch {}
            fs.renameSync(vpath, videoPath);
          }
          try { fs.unlinkSync(vpath); } catch {}
        }

        if (!fs.existsSync(videoPath)) {
          dropVideo('No video stream downloaded');
          return;
        }
        if (!ensureCloneHeroVideo(videoPath)) {
          dropVideo('Video could not be converted to H.264');
          return;
        }
        finish(true);
      });
    });
  });
}

module.exports = { downloadSong };
