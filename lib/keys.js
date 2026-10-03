"use strict";

const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const { Writable } = require("node:stream");

const { ENV_FILE } = require("./config");
const { abortError } = require("./process");

function keyStatus() {
  return Boolean(process.env.GEMINI_API_KEY?.trim());
}

function setEnvKey(value, filename = ENV_FILE) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{10,256}$/.test(value)) {
    throw new Error(
      "The key must be 10 to 256 letters, digits, underscores or hyphens, without whitespace.",
    );
  }
  if (fs.existsSync(filename) && fs.lstatSync(filename).isSymbolicLink())
    throw new Error("Refusing to overwrite a symlinked key file.");
  let lines = fs.existsSync(filename)
    ? fs.readFileSync(filename, "utf8").split(/\r?\n/)
    : [];
  lines = lines.filter(
    (line) => !/^\s*(?:export\s+)?GEMINI_API_KEY\s*=/.test(line),
  );
  while (lines.length && !lines[lines.length - 1]) lines.pop();
  lines.push(`GEMINI_API_KEY=${value}`, "");
  const temp = path.join(
    path.dirname(filename),
    `.songhero-key-${process.pid}-${Date.now()}`,
  );
  try {
    fs.writeFileSync(temp, lines.join("\n"), { mode: 0o600, flag: "wx" });
    fs.renameSync(temp, filename);
    process.env.GEMINI_API_KEY = value;
  } finally {
    fs.rmSync(temp, { force: true });
  }
}

function readAnswer(reader, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      reader.removeListener("close", onClose);
      reader.removeListener("SIGINT", onAbort);
      signal?.removeEventListener("abort", onAbort);
    };
    const fail = (error) => {
      cleanup();
      reject(error);
    };
    const onClose = () => fail(new Error("Key input closed without a key."));
    const onAbort = () => fail(abortError());
    reader.once("close", onClose);
    reader.once("SIGINT", onAbort);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) {
      onAbort();
      return;
    }
    reader.question("", (answer) => {
      cleanup();
      resolve(answer.trim());
    });
  });
}

async function readSecret({
  input = process.stdin,
  output = process.stdout,
  rl,
  signal,
} = {}) {
  if (signal?.aborted) throw abortError();
  if (rl) {
    if (!input.isTTY)
      throw new Error(
        "Set a key using songhero -- keys set gemini --stdin outside interactive mode.",
      );
    output.write("Gemini key (hidden): ");
    const write = rl._writeToOutput;
    rl._writeToOutput = () => {};
    try {
      return await readAnswer(rl, signal);
    } finally {
      rl._writeToOutput = write;
      output.write("\n");
    }
  }
  if (!input.isTTY) {
    let value = "";
    const onAbort = () => input.destroy(abortError());
    signal?.addEventListener("abort", onAbort, { once: true });
    try {
      for await (const chunk of input) {
        value += chunk.toString();
        if (value.length > 512) throw new Error("Key input is too long.");
      }
      if (signal?.aborted) throw abortError();
      return value.trim();
    } finally {
      signal?.removeEventListener("abort", onAbort);
    }
  }
  output.write("Gemini key (hidden): ");
  const muted = new Writable({
    write(_chunk, _encoding, done) {
      done();
    },
  });
  const reader = readline.createInterface({
    input,
    output: muted,
    terminal: true,
  });
  try {
    return await readAnswer(reader, signal);
  } finally {
    reader.close();
    output.write("\n");
  }
}

module.exports = { keyStatus, setEnvKey, readSecret };
