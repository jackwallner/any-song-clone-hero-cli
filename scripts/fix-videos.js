#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { ensureCloneHeroVideo, probeMedia } = require('../lib/media');

const VIDEO_NAMES = new Set(['video.mp4', 'video.webm', 'video.avi', 'video.ogv', 'video.mpeg', 'video.mpg']);

function findVideos(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...findVideos(filename));
    else if (entry.isFile() && VIDEO_NAMES.has(entry.name.toLowerCase())) files.push(filename);
  }
  return files;
}

async function main(args = process.argv.slice(2)) {
  if (args.some(arg => arg.startsWith('--') && arg !== '--dry-run') || args.filter(arg => !arg.startsWith('--')).length > 1) {
    throw new Error('Usage: node scripts/fix-videos.js [--dry-run] [library-directory]');
  }
  const dryRun = args.includes('--dry-run');
  const library = args.find(arg => !arg.startsWith('--')) || path.join(os.homedir(), 'Desktop', 'Clone Hero');
  if (!fs.existsSync(library) || !fs.lstatSync(library).isDirectory()) throw new Error(`Library not found or symlinked: ${library}`);
  const videos = findVideos(library);
  let compatible = 0, fixed = 0, failed = 0;
  console.log(`Found ${videos.length} background videos in ${library}`);
  for (const filename of videos) {
    try {
      const media = await probeMedia(filename);
      const video = media.streams.find(stream => stream.codec_type === 'video');
      const playable = filename.endsWith('video.mp4') && video?.codec_name === 'h264'
        && video.pix_fmt === 'yuv420p' && media.format.format_name.split(',').includes('mp4');
      if (playable) { compatible++; continue; }
      if (!video) throw new Error('No video stream');
      if (dryRun) console.log(`Would convert: ${path.relative(library, filename)} (${video.codec_name})`);
      else { await ensureCloneHeroVideo(filename); console.log(`Fixed: ${path.relative(library, filename)}`); }
      fixed++;
    } catch (error) {
      console.error(`Failed: ${path.relative(library, filename)} (${error.message})`);
      failed++;
    }
  }
  console.log(`Compatible: ${compatible}. ${dryRun ? 'Needs conversion' : 'Fixed'}: ${fixed}. Failed: ${failed}.`);
  return failed ? 1 : 0;
}

if (require.main === module) main().then(code => { process.exitCode = code; }).catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});

module.exports = { main, findVideos };
