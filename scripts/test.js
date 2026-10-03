#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const { loadEnv, resolvePython } = require("../lib/config");

const root = path.resolve(__dirname, "..");
loadEnv();
const python = resolvePython();
const commands = [
  [
    process.execPath,
    [
      "--test",
      ...fs
        .readdirSync(path.join(root, "test"))
        .filter((f) => f.endsWith(".test.js"))
        .sort()
        .map((f) => path.join(root, "test", f)),
    ],
  ],
  [python, ["-m", "unittest", "discover", "-s", "test/python", "-v"]],
];
let failed = false;
for (const [command, args] of commands) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: "inherit",
    env: {
      ...process.env,
      SONGHERO_PYTHON: python,
      PYTHONDONTWRITEBYTECODE: "1",
    },
  });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) failed = true;
}
process.exitCode = failed ? 1 : 0;
