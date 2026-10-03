"use strict";

const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");

const { main } = require("../index");
const { PassThrough } = require("node:stream");

const { readSecret, setEnvKey } = require("../lib/keys");
const { generateSongIni } = require("../lib/songini");
const ID = "0VjIjW4GlUZAMYd2vXMi3b";
const URL = `https://open.spotify.com/track/${ID}`;
const root = path.resolve(__dirname, "..");

test("legacy and command modes forward exactly the same generation options", async () => {
  const seen = [];
  const pipeline = {
    runPipeline: async (url, options) => {
      seen.push({ url, options });
      return { status: "success" };
    },
  };
  const options = [
    "--output",
    "/tmp/fixture charts",
    "--no-gemini",
    "--no-video",
    "--no-lyrics",
    "--rate-limit",
    "0",
  ];
  assert.equal(await main([URL, ...options], { pipeline }), 0);
  assert.equal(
    await main(["--", "generate", URL, ...options], { pipeline }),
    0,
  );
  assert.deepEqual(seen[0], seen[1]);
});

test("playlist failure reaches the process exit status", async () => {
  const pipeline = {
    processPlaylist: async () => ({ status: "failed", failed: 2 }),
  };
  assert.equal(await main([`spotify:playlist:${ID}`], { pipeline }), 1);
  assert.equal(
    await main(["--", "playlist", `spotify:playlist:${ID}`], { pipeline }),
    1,
  );
});

test("real CLI help is quiet and invalid options never start a pipeline", () => {
  const help = spawnSync(process.execPath, ["index.js", "--help"], {
    cwd: root,
    encoding: "utf8",
  });
  assert.equal(help.status, 0);
  assert.match(help.stdout, /Defaults: local analysis/);
  assert.doesNotMatch(help.stdout, /injected env|dotenv/);
  const invalid = spawnSync(process.execPath, ["index.js", URL, "--unknown"], {
    cwd: root,
    encoding: "utf8",
  });
  assert.equal(invalid.status, 1);
  assert.match(invalid.stderr, /Unknown option/);
});

test("interactive settings handle EOF and queued commands without process.exit", () => {
  const interactive = spawnSync(process.execPath, ["index.js", "-"], {
    cwd: root,
    input:
      'video on\nvideo off\noutput "my charts"\nrate-limit 0\noptions\nexit\n',
    encoding: "utf8",
  });
  assert.equal(interactive.status, 0, interactive.stderr);
  assert.match(interactive.stdout, /Output: my charts/);
  assert.match(interactive.stdout, /Video: off/);
  assert.match(interactive.stdout, /Rate limit: 0/);
});

test("key files are private, reject injection and preserve other settings", (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "songhero-keys-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const filename = path.join(directory, ".env");
  fs.writeFileSync(filename, "OTHER=preserved\nGEMINI_API_KEY=old\n", {
    mode: 0o644,
  });
  const original = process.env.GEMINI_API_KEY;
  t.after(() => {
    if (original === undefined) delete process.env.GEMINI_API_KEY;
    else process.env.GEMINI_API_KEY = original;
  });
  setEnvKey("fixture-key-not-real", filename);
  assert.equal(fs.statSync(filename).mode & 0o777, 0o600);
  assert.match(fs.readFileSync(filename, "utf8"), /OTHER=preserved/);
  assert.throws(() => setEnvKey("bad\nANOTHER=oops", filename));
});

test("song.ini cannot contain injected lines or nonexistent instrument metadata", () => {
  const ini = generateSongIni(
    { name: "Song\nvideo = bad", artist: "Artist" },
    { duration_ms: 8000, lyrics: [] },
    false,
  );
  assert.doesNotMatch(ini, /\nvideo =/);
  assert.match(ini, /diff_guitarghl = -1/);
  assert.doesNotMatch(ini, /video_start_time/);
});

test("cancelled key input rejects without waiting for EOF", async () => {
  const input = new PassThrough();
  const controller = new AbortController();
  const secret = readSecret({ input, signal: controller.signal });
  input.write("partial-secret");
  controller.abort();
  await assert.rejects(secret, { name: "AbortError" });
  assert.equal(input.destroyed, true);
});

test("hidden key input handles EOF and cancellation without echoing secrets", async () => {
  for (const cancel of [false, true]) {
    const input = new PassThrough();
    input.isTTY = true;
    const output = new PassThrough();
    let displayed = "";
    output.on("data", (chunk) => {
      displayed += chunk.toString();
    });
    const controller = new AbortController();
    const secret = readSecret({ input, output, signal: controller.signal });
    input.write("partial-secret");
    if (cancel) controller.abort();
    else input.end();
    await assert.rejects(
      secret,
      cancel ? { name: "AbortError" } : /closed without a key/,
    );
    assert.doesNotMatch(displayed, /partial-secret/);
    input.destroy();
  }
});

test(
  "actual CLI key input exits 130 on SIGINT without modifying the key file",
  { timeout: 5000 },
  async (t) => {
    const filename = path.join(root, ".env");
    const original = fs.existsSync(filename) ? fs.readFileSync(filename) : null;
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "songhero-key-cancel-"));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const observer = path.join(directory, "observe.cjs");
    fs.writeFileSync(observer, `
      const keys = require(${JSON.stringify(path.join(root, 'lib/keys.js'))});
      const readSecret = keys.readSecret;
      keys.readSecret = (...args) => {
        const pending = readSecret(...args);
        process.stdout.write('KEY_INPUT_READY\\n');
        return pending;
      };
    `);
    const child = spawn(
      process.execPath,
      ["--require", observer, "index.js", "--", "keys", "set", "gemini", "--stdin"],
      { cwd: root },
    );
    t.after(() => {
      child.kill("SIGKILL");
      child.stdin.destroy();
    });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    const finished = new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    await new Promise((resolve, reject) => {
      child.stdout.once('data', resolve);
      child.once('error', reject);
      child.once('close', () => reject(new Error('CLI exited before reading a key.')));
    });
    child.stdin.write("partial-secret");
    child.kill("SIGINT");
    assert.deepEqual(await finished, { code: 130, signal: null }, stderr);
    assert.match(stderr, /Cancelled/);
    assert.deepEqual(
      fs.existsSync(filename) ? fs.readFileSync(filename) : null,
      original,
    );
  },
);
