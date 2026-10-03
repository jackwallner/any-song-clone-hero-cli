'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { runProcess } = require('./process');

const VIDEO_MAX_HEIGHT = 720;

async function probeMedia(filename, { run = runProcess, signal } = {}) {
  if (!fs.lstatSync(filename).isFile() || !fs.statSync(filename).size) throw new Error(`Missing or empty media: ${filename}`);
  const result = await run('ffprobe', ['-v', 'error', '-show_format', '-show_streams', '-of', 'json', filename], { timeout: 60000, signal });
  const data = JSON.parse(result.stdout);
  const duration = Number(data.format?.duration);
  if (!Number.isFinite(duration) || duration <= 0 || !Array.isArray(data.streams) || !data.streams.length) {
    throw new Error('Media has no usable streams or duration.');
  }
  return { ...data, duration };
}

async function ensureOpus(filename, options = {}) {
  const { run = runProcess, signal } = options;
  let media = await probeMedia(filename, options);
  if (media.streams.some(stream => stream.codec_type === 'audio' && stream.codec_name === 'opus')
      && media.format.format_name.split(',').includes('ogg')) return media;
  const temp = path.join(path.dirname(filename), 'song.remux.opus');
  try {
    await run('ffmpeg', ['-y', '-i', filename, '-map', '0:a:0', '-vn', '-c:a', 'copy', '-f', 'ogg', temp], { timeout: 60000, signal });
    media = await probeMedia(temp, options);
    if (!media.streams.some(stream => stream.codec_type === 'audio' && stream.codec_name === 'opus') || media.format.format_name !== 'ogg') {
      throw new Error('Audio could not be converted to Ogg Opus.');
    }
    fs.renameSync(temp, filename);
    return media;
  } finally { fs.rmSync(temp, { force: true }); }
}

async function ensureCloneHeroVideo(filename, options = {}) {
  const { run = runProcess, signal } = options;
  const media = await probeMedia(filename, options);
  const stream = media.streams.find(item => item.codec_type === 'video');
  if (!stream) throw new Error('No video stream was downloaded.');
  const target = path.join(path.dirname(filename), 'video.mp4');
  if (filename === target && stream.codec_name === 'h264' && stream.pix_fmt === 'yuv420p'
      && media.format.format_name.split(',').includes('mp4')) return target;
  if (target !== filename && fs.existsSync(target)) throw new Error('video.mp4 already exists. Refusing to overwrite a different video.');
  const work = fs.mkdtempSync(path.join(path.dirname(filename), '.songhero-video-'));
  const temp = path.join(work, 'video.mp4');
  try {
    await run('ffmpeg', [
      '-y', '-i', filename, '-map', '0:v:0', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
      '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-vf', `scale=-2:trunc(min(${VIDEO_MAX_HEIGHT}\\,ih)/2)*2`,
      '-an', '-movflags', '+faststart', temp,
    ], { timeout: 900000, signal });
    const converted = await probeMedia(temp, options);
    if (!converted.streams.some(item => item.codec_type === 'video' && item.codec_name === 'h264')
        || !converted.format.format_name.split(',').includes('mp4')) throw new Error('H.264 video validation failed.');
    fs.renameSync(temp, target);
    if (filename !== target) fs.unlinkSync(filename);
    return target;
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
}

module.exports = { probeMedia, ensureOpus, ensureCloneHeroVideo, VIDEO_MAX_HEIGHT };
