'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const ALLOWED_FILES = new Set(['notes.chart', 'song.ini', 'song.opus', 'album.jpg', 'video.mp4', 'lyrics.json']);

function sanitizeName(value, limit = 150) {
  let text = String(value || '').normalize('NFKC').replace(/[\x00-\x1f\x7f<>:"/\\|?*]/g, ' ')
    .replace(/\s+/g, ' ').replace(/^[. ]+|[. ]+$/g, '');
  while (Buffer.byteLength(text) > limit) text = Array.from(text).slice(0, -1).join('');
  return text.trim() || 'Unknown song';
}

function outputRoot(directory) {
  const resolved = path.resolve(directory);
  if (fs.existsSync(resolved) && fs.lstatSync(resolved).isSymbolicLink()) throw new Error('The output directory must not be a symlink.');
  fs.mkdirSync(resolved, { recursive: true });
  if (!fs.statSync(resolved).isDirectory()) throw new Error('The output path must be a directory.');
  return fs.realpathSync(resolved);
}

function songPath(root, metadata) {
  if (!/^[A-Za-z0-9]{22}$/.test(metadata.id || '')) throw new Error('Missing or invalid Spotify track ID.');
  const name = `${sanitizeName(`${metadata.artist} - ${metadata.name}`)} (SongHero ${metadata.id})`;
  const destination = path.resolve(root, name);
  if (path.dirname(destination) !== root) throw new Error('Song destination escaped the output directory.');
  return destination;
}

function regularFile(filename) {
  try { return fs.lstatSync(filename).isFile(); } catch { return false; }
}

function digest(filename) {
  return crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
}

function packageStatus(destination, trackId) {
  if (!fs.existsSync(destination)) {
    try { fs.lstatSync(destination); } catch { return { exists: false, owned: false, valid: false }; }
  }
  const base = { exists: true, owned: false, valid: false };
  if (!fs.lstatSync(destination).isDirectory()) return base;
  const marker = path.join(destination, '.songhero.json');
  if (!regularFile(marker) || fs.statSync(marker).size > 1024 * 1024) return base;
  try {
    const manifest = JSON.parse(fs.readFileSync(marker, 'utf8'));
    if (manifest.generator !== 'songhero' || manifest.track_id !== trackId) return base;
    base.owned = true;
    const files = manifest.files;
    if (!files || typeof files !== 'object' || Array.isArray(files)) return base;
    if (!['notes.chart', 'song.ini', 'song.opus'].every(file => Object.hasOwn(files, file))) return base;
    base.valid = Object.entries(files).every(([file, hash]) => ALLOWED_FILES.has(file)
      && regularFile(path.join(destination, file)) && fs.statSync(path.join(destination, file)).size > 0
      && /^[a-f0-9]{64}$/.test(hash) && digest(path.join(destination, file)) === hash);
    return base;
  } catch { return base; }
}

function acquireLock(root, trackId) {
  const filename = path.join(root, `.songhero-lock-${trackId}`);
  let fd;
  try { fd = fs.openSync(filename, 'wx', 0o600); }
  catch (error) {
    if (error.code === 'EEXIST') throw new Error(`Another run is using this song. If no run is active, remove the stale lock: ${filename}`);
    throw error;
  }
  fs.writeFileSync(fd, JSON.stringify({ pid: process.pid, created_at: new Date().toISOString() }));
  fs.closeSync(fd);
  return () => fs.rmSync(filename, { force: true });
}

function publishPackage(root, destination, source, files, metadata, replace) {
  const stage = fs.mkdtempSync(path.join(root, '.songhero-stage-'));
  let backup = null;
  try {
    const hashes = {};
    for (const file of files) {
      const src = path.join(source, file);
      if (!ALLOWED_FILES.has(file) || !regularFile(src) || !fs.statSync(src).size) throw new Error(`Missing or invalid package file: ${file}`);
      fs.copyFileSync(src, path.join(stage, file));
      hashes[file] = digest(path.join(stage, file));
    }
    fs.writeFileSync(path.join(stage, '.songhero.json'), JSON.stringify({
      generator: 'songhero', schema: 1, ...metadata, files: hashes,
    }, null, 2) + '\n');
    if (!packageStatus(stage, metadata.track_id).valid) throw new Error('Package validation failed.');
    const current = packageStatus(destination, metadata.track_id);
    if (current.exists) {
      if (!replace || !current.owned) throw new Error('Refusing to replace an existing directory that is not owned by this SongHero track.');
      backup = fs.mkdtempSync(path.join(root, '.songhero-backup-'));
      fs.rmdirSync(backup);
      fs.renameSync(destination, backup);
    }
    try { fs.renameSync(stage, destination); }
    catch (error) {
      if (backup) { fs.renameSync(backup, destination); backup = null; }
      throw error;
    }
    if (backup) { fs.rmSync(backup, { recursive: true }); backup = null; }
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
}

module.exports = { sanitizeName, outputRoot, songPath, packageStatus, acquireLock, publishPackage, digest };
