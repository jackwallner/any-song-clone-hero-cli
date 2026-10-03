'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

const { acquireLock, outputRoot, packageStatus, publishPackage, sanitizeName, songPath } = require('../lib/paths');
const ID = '0VjIjW4GlUZAMYd2vXMi3b';

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'songhero-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const root = outputRoot(path.join(directory, 'songs'));
  const source = path.join(directory, 'work');
  fs.mkdirSync(source);
  for (const file of ['notes.chart', 'song.ini', 'song.opus']) fs.writeFileSync(path.join(source, file), 'fixture');
  return { root, source };
}

test('metadata cannot traverse, collide across IDs or exceed filesystem name limits', t => {
  const { root } = fixture(t);
  const metadata = { id: ID, artist: '../../artist', name: '../../../$(never)/name' };
  const destination = songPath(root, metadata);
  assert.equal(path.dirname(destination), root);
  assert.notEqual(destination, songPath(root, { ...metadata, id: '3DrNvXNKo4cr8YAjxvjgnp' }));
  assert.ok(Buffer.byteLength(path.basename(songPath(root, { ...metadata, name: '🎵'.repeat(500) }))) < 255);
  assert.equal(sanitizeName('...'), 'Unknown song');
});

test('publishing records ownership and detects incomplete or changed packages', t => {
  const { root, source } = fixture(t);
  const destination = songPath(root, { id: ID, artist: 'Artist', name: 'Song' });
  publishPackage(root, destination, source, ['notes.chart', 'song.ini', 'song.opus'], { track_id: ID }, false);
  assert.deepEqual(packageStatus(destination, ID), { exists: true, owned: true, valid: true });
  fs.writeFileSync(path.join(destination, 'notes.chart'), 'modified');
  assert.deepEqual(packageStatus(destination, ID), { exists: true, owned: true, valid: false });
});

test('failed replacement keeps previous output, foreign folders are never removed', t => {
  const { root, source } = fixture(t);
  const destination = songPath(root, { id: ID, artist: 'Artist', name: 'Song' });
  publishPackage(root, destination, source, ['notes.chart', 'song.ini', 'song.opus'], { track_id: ID }, false);
  assert.throws(() => publishPackage(root, destination, source, ['notes.chart', 'missing'], { track_id: ID }, true));
  assert.equal(fs.readFileSync(path.join(destination, 'notes.chart'), 'utf8'), 'fixture');
  const foreign = path.join(root, 'foreign');
  fs.mkdirSync(foreign);
  fs.writeFileSync(path.join(foreign, 'keep'), 'untouched');
  assert.throws(() => publishPackage(root, foreign, source, ['notes.chart', 'song.ini', 'song.opus'], { track_id: ID }, true));
  assert.equal(fs.readFileSync(path.join(foreign, 'keep'), 'utf8'), 'untouched');
  assert.equal(fs.readdirSync(root).some(file => file.startsWith('.songhero-stage-')), false);
});

test('symlink destinations and output roots are refused', t => {
  const { root, source } = fixture(t);
  const link = path.join(root, 'link');
  fs.symlinkSync(source, link, 'dir');
  assert.throws(() => outputRoot(link));
  assert.throws(() => publishPackage(root, link, source, ['notes.chart', 'song.ini', 'song.opus'], { track_id: ID }, true));
});

test('concurrent runs cannot publish the same track', t => {
  const { root } = fixture(t);
  const release = acquireLock(root, ID);
  assert.throws(() => acquireLock(root, ID), /Another run/);
  release();
  acquireLock(root, ID)();
});
