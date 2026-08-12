#!/usr/bin/env node
// Re-encode background videos in an existing Clone Hero song library to H.264.
// Clone Hero shows a black screen (or nothing) for AV1/VP9 videos, which is
// what older SongHero builds packaged.
//
//   node scripts/fix-videos.js                       # default library, transcode
//   node scripts/fix-videos.js --dry-run             # report only
//   node scripts/fix-videos.js "/path/to/Clone Hero" # custom library path

const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const MAX_HEIGHT = 720;
const VIDEO_NAMES = ['video.mp4', 'video.webm', 'video.avi', 'video.ogv', 'video.mpeg', 'video.mpg'];

function probeCodec(filePath) {
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

function findVideos(dir, found = []) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return found;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      findVideos(full, found);
    } else if (VIDEO_NAMES.includes(entry.name.toLowerCase())) {
      found.push(full);
    }
  }
  return found;
}

function transcode(videoPath) {
  const tmpPath = path.join(path.dirname(videoPath), 'video.fix.mp4');
  const res = spawnSync('ffmpeg', [
    '-y',
    '-i', videoPath,
    '-c:v', 'libx264',
    '-preset', 'veryfast',
    '-crf', '23',
    '-profile:v', 'high',
    '-pix_fmt', 'yuv420p',
    '-vf', `scale=-2:min(${MAX_HEIGHT}\\,ih)`,
    '-an',
    '-movflags', '+faststart',
    tmpPath,
  ], { stdio: 'ignore', timeout: 900000 });

  if (res.status !== 0 || !fs.existsSync(tmpPath)) {
    try { fs.unlinkSync(tmpPath); } catch {}
    return false;
  }
  fs.unlinkSync(videoPath);
  fs.renameSync(tmpPath, path.join(path.dirname(videoPath), 'video.mp4'));
  return true;
}

function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const libraryArg = args.find(a => !a.startsWith('--'));
  const library = libraryArg || path.join(os.homedir(), 'Desktop', 'Clone Hero');

  if (!fs.existsSync(library)) {
    console.error(`Library not found: ${library}`);
    process.exit(1);
  }

  const videos = findVideos(library);
  console.log(`Scanning ${library}`);
  console.log(`Found ${videos.length} background video(s)\n`);

  let ok = 0, fixed = 0, failed = 0, unreadable = 0;
  for (const videoPath of videos) {
    const label = path.relative(library, videoPath);
    const codec = probeCodec(videoPath);
    if (!codec) {
      console.log(`  ? ${label} — unreadable, skipping`);
      unreadable++;
      continue;
    }
    if (codec === 'h264') {
      ok++;
      continue;
    }
    if (dryRun) {
      console.log(`  ! ${label} — ${codec} (would re-encode)`);
      fixed++;
      continue;
    }
    process.stdout.write(`  ↻ ${label} — ${codec} → h264... `);
    if (transcode(videoPath)) {
      console.log('done');
      fixed++;
    } else {
      console.log('FAILED');
      failed++;
    }
  }

  console.log(`\nAlready H.264: ${ok}   ${dryRun ? 'Needs re-encode' : 'Re-encoded'}: ${fixed}   Failed: ${failed}   Unreadable: ${unreadable}`);
}

main();
