'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const ENV_FILE = path.join(ROOT, '.env');

function loadEnv() {
  try { require('dotenv').config({ path: ENV_FILE, quiet: true }); }
  catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
}

function resolvePython() {
  const bin = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'];
  const candidates = process.env.SONGHERO_PYTHON ? [process.env.SONGHERO_PYTHON] : [
    path.join(ROOT, '.venv', ...bin), path.join(os.homedir(), '.songhero', 'venv', ...bin),
    ...(process.platform === 'win32' ? ['python', 'python3', 'py'] : ['python3', 'python']),
  ];
  const usable = [];
  for (const command of candidates) {
    if (path.isAbsolute(command) && !fs.existsSync(command)) continue;
    const check = spawnSync(command, ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'], { timeout: 10000, stdio: 'ignore' });
    if (!check.error && check.status === 0) usable.push(command);
  }
  for (const command of usable) {
    const check = spawnSync(command, ['-c', 'import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ("librosa", "numpy", "scipy", "soundfile")) else 1)'], { timeout: 10000, stdio: 'ignore' });
    if (!check.error && check.status === 0) return command;
  }
  if (usable.length) return usable[0];
  throw new Error('Python 3.9+ is required. Install Python or set SONGHERO_PYTHON to its executable path.');
}

module.exports = { ROOT, ENV_FILE, loadEnv, resolvePython };
