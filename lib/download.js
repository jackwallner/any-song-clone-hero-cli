const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

async function downloadSong(artist, title, outputDir) {
  fs.mkdirSync(outputDir, { recursive: true });
  const searchQuery = `${artist} - ${title}`;

  console.log(`  Searching YouTube for: ${searchQuery}`);

  return new Promise((resolve, reject) => {
    // Single yt-dlp call: download audio as opus + get metadata
    const audioArgs = [
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

      // Verify audio exists
      const hasAudio = allFiles.some(f => f.startsWith('song.') && f.endsWith('.opus'));
      if (!hasAudio) {
        console.error('  All files:', allFiles.join(', '));
        reject(new Error('Audio download failed - no song.opus found'));
        return;
      }

      // Step 2: Download video (non-critical)
      const videoArgs = [
        'ytsearch1:' + searchQuery,
        '-f', 'bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]/best[height<=1080]',
        '--max-filesize', '100M',
        '-o', path.join(outputDir, 'video.%(ext)s'),
        '--no-playlist',
        '--no-warnings',
      ];

      const video = spawn('yt-dlp', videoArgs, { stdio: 'ignore' });
      video.on('close', () => {
        // Rename video to video.mp4
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
