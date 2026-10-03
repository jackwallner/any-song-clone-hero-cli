#!/usr/bin/env node
"use strict";

const readline = require("node:readline");

const { ENV_FILE, loadEnv } = require("./lib/config");
const { inspectDependencies } = require("./lib/doctor");
const { keyStatus, readSecret, setEnvKey } = require("./lib/keys");
const {
  DEFAULTS,
  expandPath,
  parseOptions,
  parseRateLimit,
  parseSpotifyInput,
  tokenize,
} = require("./lib/options");
const { createPipeline } = require("./lib/pipeline");
const { abortError } = require("./lib/process");

function showHelp() {
  console.log(`SongHero: Spotify links to Clone Hero song folders

Usage:
  songhero <track-or-playlist-url> [options]
  songhero -- generate <track-url> [options]
  songhero -- playlist <playlist-url> [options]
  songhero -                         Interactive commands

Defaults: local analysis, optional lyrics, no video, skip valid existing charts.
Gemini and browser cookies are never used unless explicitly enabled.

Options (shared by all generation modes):
  --gemini / --no-gemini             Optional Gemini enhancement (requires a key)
  --lyrics / --no-lyrics             Fetch or skip optional lyrics
  --video / --no-video / --auto-video Require, skip, or try a background video
  --output <dir>                    Output directory (default: ~/Desktop/Clone Hero)
  --rate-limit <ms>                  Playlist delay (default: 5000, 30000 with Gemini)
  --skip-existing / --no-skip-existing Skip valid charts or regenerate owned charts
  --rewrite                         Replace only this track's SongHero-owned package
  --keep-temp                       Keep intermediate files, including on failure
  --cookies-from-browser <browser>  Explicitly allow yt-dlp to use a browser session
  --allow-duration-mismatch         Accept a different-duration audio version

Commands (with or without the leading --):
  generate <url>, playlist <url>, doctor, keys, help
  keys set gemini                    Prompt for a key without echo or shell history
  keys set gemini --stdin            Read a key from standard input

Spotify HTTPS links and spotify:track:/spotify:playlist: URIs are accepted.
Only tracks exposed by Spotify's public playlist embed can be processed.
Keys are stored in ${ENV_FILE} with owner-only permissions.
Use only audio, video, and lyrics you have permission to download and use.`);
}

function showInteractiveHelp() {
  console.log(`generate <url> [options], gen <url> [options], playlist <url> [options]
gemini on|off, lyrics on|off, video on|off|auto, skip-existing on|off
output <dir>, rate-limit <ms>, options, doctor, keys, keys set gemini
help, exit, quit
Quoted paths are supported. Jobs run one at a time. Ctrl+C cancels and cleans up.`);
}

function showOptions(session) {
  console.log(
    `Output: ${session.outputBase}\nGemini: ${session.useGemini ? "on" : "off"}\nLyrics: ${session.fetchLyrics ? "on" : "off"}\nVideo: ${session.videoMode}\nSkip existing: ${session.skipExisting}\nRate limit: ${session.rateLimitMs ?? "automatic"}\nKeep temp: ${session.keepTemp}\nBrowser cookies: ${session.cookiesBrowser || "disabled"}`,
  );
}

function generationArguments(args, defaults, expectedType) {
  const index = args.findIndex(
    (value) => /^https?:\/\//.test(value) || value.startsWith("spotify:"),
  );
  if (index === -1) throw new Error("A Spotify URL or URI is required.");
  const spotify = parseSpotifyInput(args[index], expectedType);
  const options = parseOptions(
    args.filter((_value, i) => i !== index),
    defaults,
  );
  return { spotify, options };
}

async function keyCommand(args, rl, signal) {
  if (!args.length || (args.length === 1 && args[0] === "show")) {
    console.log(
      `Gemini key: ${keyStatus() ? "set" : "not set"}\nKey file: ${ENV_FILE}`,
    );
    return;
  }
  if (
    args[0] !== "set" ||
    args[1] !== "gemini" ||
    args.length > 3 ||
    (args[2] && args[2] !== "--stdin")
  ) {
    throw new Error(
      "Use keys set gemini (hidden prompt) or keys set gemini --stdin. Do not put secrets in command arguments.",
    );
  }
  if (!rl && !process.stdin.isTTY && args[2] !== "--stdin")
    throw new Error("Noninteractive key entry requires --stdin.");
  if (rl && args[2])
    throw new Error("Use keys set gemini without flags in interactive mode.");
  setEnvKey(await readSecret({ rl, signal }));
  console.log("Gemini key saved with owner-only permissions.");
}

async function doctor(signal) {
  const checks = await inspectDependencies({ signal });
  for (const check of checks)
    console.log(
      `${check.ok ? "OK" : "FAIL"} ${check.name}${check.detail ? `: ${check.detail}` : ""}`,
    );
  return checks.every((check) => check.ok) ? 0 : 1;
}

async function interactiveMode(pipeline, signal) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: Boolean(process.stdin.isTTY),
    prompt: "songhero> ",
  });
  const session = { ...DEFAULTS };
  let exitCode = 0;
  const close = () => rl.close();
  signal?.addEventListener("abort", close, { once: true });
  console.log("SongHero interactive mode. Type help for commands.");
  if (process.stdin.isTTY) rl.prompt();
  try {
    for await (const line of rl) {
      if (signal?.aborted) break;
      try {
        const [rawCommand, ...args] = tokenize(line);
        if (!rawCommand) continue;
        const command = rawCommand.toLowerCase();
        if (command === "exit" || command === "quit") break;
        if (command === "help" || command === "?") showInteractiveHelp();
        else if (command === "options") showOptions(session);
        else if (command === "keys") await keyCommand(args, rl, signal);
        else if (command === "doctor") {
          if (await doctor(signal)) exitCode = 1;
        } else if (command === "output") {
          if (!args.length) console.log(session.outputBase);
          else if (args.length === 1) session.outputBase = expandPath(args[0]);
          else
            throw new Error(
              'Quote paths containing spaces: output "my charts".',
            );
        } else if (command === "rate-limit") {
          if (!args.length) console.log(session.rateLimitMs ?? "automatic");
          else if (args.length === 1)
            session.rateLimitMs = parseRateLimit(args[0]);
          else throw new Error("Usage: rate-limit <milliseconds>");
        } else if (
          ["gemini", "lyrics", "video", "skip-existing"].includes(command)
        ) {
          const allowed =
            command === "video"
              ? ["on", "off", "auto", "force"]
              : ["on", "off"];
          if (args.length !== 1 || !allowed.includes(args[0]))
            throw new Error(`Usage: ${command} ${allowed.join("|")}`);
          const flag =
            command === "video" && args[0] === "auto"
              ? "--auto-video"
              : command === "video" && args[0] === "force"
                ? "--video"
                : `--${args[0] === "off" ? "no-" : ""}${command}`;
          Object.assign(session, parseOptions([flag], session));
        } else if (["generate", "gen", "playlist"].includes(command)) {
          const { spotify, options } = generationArguments(
            args,
            session,
            command === "playlist" ? "playlist" : "track",
          );
          const result = await pipeline[
            spotify.type === "playlist" ? "processPlaylist" : "runPipeline"
          ](spotify.url, { ...options, signal });
          if (result.status === "failed") exitCode = 1;
        } else throw new Error(`Unknown command: ${command}. Type help.`);
      } catch (error) {
        if (error.name === "AbortError") throw error;
        console.error(error.message);
        exitCode = 1;
      }
      if (process.stdin.isTTY && !signal?.aborted) rl.prompt();
    }
  } finally {
    signal?.removeEventListener("abort", close);
    rl.close();
  }
  return signal?.aborted ? 130 : exitCode;
}

async function main(argv = process.argv.slice(2), { pipeline, signal } = {}) {
  if (signal?.aborted) throw abortError();
  loadEnv();
  pipeline ||= createPipeline();
  if (!argv.length || argv.includes("--help") || argv.includes("-h")) {
    showHelp();
    return 0;
  }
  if (argv.length === 1 && argv[0] === "-")
    return interactiveMode(pipeline, signal);
  const args = argv[0] === "--" ? argv.slice(1) : [...argv];
  if (!args.length || args[0] === "help") {
    showHelp();
    return 0;
  }
  if (args[0] === "doctor" || args[0] === "--doctor") {
    if (args.length !== 1) throw new Error("Usage: songhero -- doctor");
    return doctor(signal);
  }
  if (args[0] === "keys") {
    await keyCommand(args.slice(1), undefined, signal);
    return 0;
  }
  const command = ["generate", "gen", "playlist"].includes(args[0])
    ? args.shift()
    : null;
  const { spotify, options } = generationArguments(
    args,
    {},
    command ? (command === "playlist" ? "playlist" : "track") : undefined,
  );
  const result = await pipeline[
    spotify.type === "playlist" ? "processPlaylist" : "runPipeline"
  ](spotify.url, { ...options, signal });
  return result.status === "failed" ? 1 : 0;
}

if (require.main === module) {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  process.on("SIGINT", cancel);
  process.on("SIGTERM", cancel);
  main(undefined, { signal: controller.signal })
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error) => {
      console.error(error.name === "AbortError" ? "Cancelled." : error.message);
      process.exitCode = error.name === "AbortError" ? 130 : 1;
    })
    .finally(() => {
      process.removeListener("SIGINT", cancel);
      process.removeListener("SIGTERM", cancel);
    });
}

module.exports = { main, generationArguments, interactiveMode };
