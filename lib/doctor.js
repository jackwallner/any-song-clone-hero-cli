"use strict";

const { resolvePython, ROOT } = require("./config");
const { runProcess } = require("./process");

async function inspectDependencies({ run = runProcess, python, signal } = {}) {
  const checks = [];
  checks.push({
    name: "Node.js 18+",
    ok: Number(process.versions.node.split(".")[0]) >= 18,
    detail: process.version,
  });
  try {
    python ||= resolvePython();
  } catch (error) {
    checks.push({ name: "Python 3.9+", ok: false, detail: error.message });
  }
  if (python) {
    try {
      await run(python, ["-c", "import librosa, numpy, scipy, soundfile"], {
        timeout: 60000,
        signal,
      });
      checks.push({
        name: "Python analysis dependencies",
        ok: true,
        detail: python,
      });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      checks.push({
        name: "Python analysis dependencies",
        ok: false,
        detail: `Install with: ${python} -m pip install -r "${ROOT}/requirements.txt"`,
      });
    }
  }
  for (const command of ["yt-dlp", "ffmpeg", "ffprobe"]) {
    try {
      await run(
        command,
        command === "yt-dlp" ? ["--ignore-config", "--version"] : ["-version"],
        { timeout: 30000, signal },
      );
      checks.push({ name: command, ok: true, detail: "" });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      checks.push({
        name: command,
        ok: false,
        detail: `${error.message} Install ${command === "ffprobe" ? "ffmpeg (includes ffprobe)" : command} and ensure it is on PATH.`,
      });
    }
  }
  return checks;
}

async function checkDependencies(options) {
  const checks = await inspectDependencies(options);
  const failures = checks.filter((check) => !check.ok);
  if (failures.length)
    throw new Error(
      failures.map((check) => `${check.name}: ${check.detail}`).join("\n"),
    );
}

module.exports = { inspectDependencies, checkDependencies };
