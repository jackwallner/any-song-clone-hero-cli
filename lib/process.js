'use strict';

const { spawn } = require('node:child_process');

function abortError() {
  const error = new Error('Cancelled.');
  error.name = 'AbortError';
  return error;
}

function runProcess(command, args, options = {}) {
  const { timeout = 30000, maxBuffer = 4 * 1024 * 1024, signal, env = process.env, cwd } = options;
  if (signal?.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd, env, shell: false, windowsHide: true,
      detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '', stderr = '', size = 0, failure = null, killTimer;
    const kill = (kind) => {
      try {
        if (process.platform === 'win32') child.kill(kind);
        else process.kill(-child.pid, kind);
      } catch (error) { if (error.code !== 'ESRCH') child.kill(kind); }
    };
    const stop = (error) => {
      if (failure) return;
      failure = error;
      kill('SIGTERM');
      killTimer = setTimeout(() => kill('SIGKILL'), 1000);
      killTimer.unref();
    };
    const timer = setTimeout(() => stop(new Error(`${command} timed out after ${Math.round(timeout / 1000)}s.`)), timeout);
    const onAbort = () => stop(abortError());
    signal?.addEventListener('abort', onAbort, { once: true });
    if (signal?.aborted) onAbort();
    const collect = (stream, data) => {
      size += Buffer.byteLength(data);
      if (size > maxBuffer) {
        stop(new Error(`${command} exceeded the output size limit.`));
        return;
      }
      if (stream === 'stdout') stdout += data.toString();
      else stderr += data.toString();
    };
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', data => collect('stdout', data));
    child.stderr.on('data', data => collect('stderr', data));
    child.on('error', error => { failure = error; });
    child.on('close', (code, exitSignal) => {
      clearTimeout(timer);
      clearTimeout(killTimer);
      signal?.removeEventListener('abort', onAbort);
      if (failure) return reject(failure);
      if (code !== 0) {
        const detail = stderr.trim().slice(-1500) || stdout.trim().slice(-1500);
        const error = new Error(`${command} failed (${exitSignal || code})${detail ? `: ${detail}` : '.'}`);
        error.code = code;
        error.stdout = stdout;
        error.stderr = stderr;
        return reject(error);
      }
      resolve({ stdout, stderr, code });
    });
  });
}

function sleep(ms, signal) {
  if (signal?.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const onAbort = () => { clearTimeout(timer); reject(abortError()); };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
    if (signal?.aborted) onAbort();
  });
}

module.exports = { runProcess, sleep, abortError };
