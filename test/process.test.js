'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runProcess, sleep } = require('../lib/process');

test('subprocess arguments remain literal and stdout survives Unicode', async () => {
  const text = '$(do-not-run) "quotes" café 🎵';
  const result = await runProcess(process.execPath, ['-e', 'process.stdout.write(process.argv[1])', text]);
  assert.equal(result.stdout, text);
});

test('missing commands and unsuccessful exits reject with actionable errors', async () => {
  await assert.rejects(runProcess('songhero-command-that-does-not-exist', []), { code: 'ENOENT' });
  await assert.rejects(runProcess(process.execPath, ['-e', 'console.error("fixture failure"); process.exit(7)']), /fixture failure/);
});

test('hung and noisy children are terminated', async () => {
  await assert.rejects(runProcess(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { timeout: 200 }), /timed out/);
  await assert.rejects(runProcess(process.execPath, ['-e', 'process.stdout.write("x".repeat(10000))'], { maxBuffer: 100 }), /size limit/);
});

test('cancellation aborts children and playlist delays', async () => {
  const controller = new AbortController();
  const job = runProcess(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { signal: controller.signal });
  controller.abort();
  await assert.rejects(job, { name: 'AbortError' });
  await assert.rejects(sleep(1000, controller.signal), { name: 'AbortError' });
});
