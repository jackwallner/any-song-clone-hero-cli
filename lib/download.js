const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

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

async function downloadSong(artist, title, outputDir) {
  fs.mkdirSync(outputDir, { recursive: true });
  const searchQuery = `${artist} - ${title}`;
  console.log(`  Searching YouTube for: ${searchQuery}`);

  const maxRetries = 3;
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await attemptDownload(artist, title, outputDir, searchQuery);
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

function attemptDownload(artist, title, outputDir, searchQuery) {
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

      // Check if the video title indicates it's actually a music video
      const videoOk = isMusicVideo(videoTitle);
      if (!videoOk) {
        console.log(`  ⚠ Skipping video: "${videoTitle}" is not a music video`);
        resolve({
          artist,
          title: videoTitle.includes(' - ') ? videoTitle.split(' - ').slice(1).join(' - ') : videoTitle,
          durationMs: Math.round(duration * 1000),
          youtubeUrl,
          hasVideo: false,
        });
        return;
      }

      // Download video
      const videoArgs = [
        ...cookieArgs,
        'ytsearch1:' + searchQuery,
        '-f', 'bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]/best[height<=1080]',
        '--max-filesize', '100M',
        '-o', path.join(outputDir, 'video.%(ext)s'),
        '--no-playlist',
        '--no-warnings',
        '--sleep-requests', '1.5',
        '--sleep-interval', '3',
        '--max-sleep-interval', '15',
        '--retries', '3',
      ];

      const video = spawn('yt-dlp', videoArgs, { stdio: 'ignore' });
      video.on('close', () => {
        const vfiles = fs.readdirSync(outputDir).filter(f => f.startsWith('video.') && f !== 'video.mp4');
        for (const vf of vfiles) {
          try {
            const vpath = path.join(outputDir, vf);
            const dest = path.join(outputDir, 'video.mp4');
            if (fs.existsSync(dest)) fs.unlinkSync(dest);
            fs.renameSync(vpath, dest);
          } catch {}
        }

        const hasVideo = fs.existsSync(path.join(outputDir, 'video.mp4'));

        resolve({
          artist,
          title: videoTitle.includes(' - ') ? videoTitle.split(' - ').slice(1).join(' - ') : videoTitle,
          durationMs: Math.round(duration * 1000),
          youtubeUrl,
          hasVideo,
        });
      });
    });
  });
}

module.exports = { downloadSong };
