"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");

const { inspectDependencies } = require("../lib/doctor");

test("doctor checks yt-dlp without loading user configuration", async () => {
  const calls = [];
  const checks = await inspectDependencies({
    python: "fixture-python",
    run: async (command, args) => {
      calls.push({ command, args });
    },
  });
  assert.ok(checks.every((check) => check.ok));
  assert.deepEqual(calls.find((call) => call.command === "yt-dlp").args, [
    "--ignore-config",
    "--version",
  ]);
});

test("doctor preserves dependency failure details and propagates cancellation", async () => {
  const checks = await inspectDependencies({
    python: "fixture-python",
    run: async (command) => {
      if (command === "yt-dlp") throw new Error("yt-dlp timed out");
    },
  });
  const failed = checks.find((check) => check.name === "yt-dlp");
  assert.equal(failed.ok, false);
  assert.match(failed.detail, /timed out/);
  assert.match(failed.detail, /Install yt-dlp/);
  await assert.rejects(
    inspectDependencies({
      python: "fixture-python",
      run: async () => {
        const error = new Error("Cancelled");
        error.name = "AbortError";
        throw error;
      },
    }),
    { name: "AbortError" },
  );
});
