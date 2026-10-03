# SongHero

Generate Clone Hero practice charts from Spotify track links and public playlists. Local audio analysis creates four guitar difficulties. Gemini enhancement, background video, and browser cookies are optional and explicitly enabled.

These are automatically generated charts, not hand-authored guitar transcriptions. Use only recordings, video, and lyrics you have permission to download and use.

## Install

Supported: macOS, Linux, and Windows through WSL. Requires Node.js 18+, Python 3.9+, yt-dlp, ffmpeg, and ffprobe.

On macOS, install [Homebrew](https://brew.sh) first. The installer checks dependencies, creates an isolated Python environment, installs the CLI, and verifies it before reporting success:

```bash
curl -fsSL https://raw.githubusercontent.com/jackwallner/any-song-clone-hero-cli/main/landing/install.sh | bash -s -- --add-to-path
```

`--add-to-path` opts into updating your shell startup file, with a backup. Omit it to leave your shell configuration unchanged and use `~/.songhero/bin/songhero` directly. Reopen your terminal after installing with PATH setup.

### From source

```bash
# macOS system tools
brew install node python yt-dlp ffmpeg

# Clone and install
git clone https://github.com/jackwallner/any-song-clone-hero-cli.git
cd any-song-clone-hero-cli
npm ci
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
node index.js -- doctor
```

On Debian/Ubuntu, install `nodejs npm python3 python3-venv ffmpeg`, ensure Node is at least 18, and install a current [yt-dlp](https://github.com/yt-dlp/yt-dlp#installation). Do not install Python analysis packages into a system Python marked externally managed.

SongHero prefers `.venv`, then the installer's `~/.songhero/venv`, then a compatible Python on PATH. `SONGHERO_PYTHON=/path/to/python` overrides interpreter selection. `npm test` uses the same selection.

### Windows

Use WSL rather than double-clicking `index.js`, which invokes Windows Script Host, not Node. Install WSL with `wsl --install` in an administrator PowerShell, open Ubuntu, and run the installer there. Clone inside WSL to avoid Windows checkout issues. Reach Linux output from Explorer through `\\wsl$`.

## Usage

From source, replace `songhero` with `node index.js`.

```bash
# Track, local analysis, optional lyrics, no video
songhero https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b

# Audio-only, without lyric lookups
songhero spotify:track:0VjIjW4GlUZAMYd2vXMi3b --no-lyrics

# Require a compatible background video
songhero https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b --video

# Public playlist, custom output and delay
songhero -- playlist https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M --output ~/Documents/Charts --rate-limit 5000

# Interactive commands
songhero -

# Dependency diagnostics
songhero -- doctor
```

Track and playlist URLs are auto-detected. Spotify URIs, localized Spotify URLs, and command mode (`songhero -- generate <url>`) use the same options. Playlist scraping can only see tracks exposed by Spotify's public embed, not necessarily every track in a large playlist. Duplicate or unsupported embed entries are filtered.

### Options

| Option | Behavior |
| --- | --- |
| `--gemini`, `--no-gemini` | Optional AI enhancement. Off by default. |
| `--lyrics`, `--no-lyrics` | Optional lyric lookup. On by default. |
| `--video` | Require a compatible video; fail this track if unavailable. |
| `--no-video` | No video download. Default. |
| `--auto-video` | Try a suitable video, continue audio-only on failure. |
| `--output <dir>` | Output root, defaults to `~/Desktop/Clone Hero`. |
| `--rate-limit <ms>` | Nonnegative playlist delay, defaults to 5000, or 30000 with configured Gemini. |
| `--skip-existing`, `--no-skip-existing` | Skip valid packages, or regenerate SongHero-owned packages. |
| `--rewrite` | Replace this track's SongHero-owned package only after a new package validates. |
| `--keep-temp` | Retain intermediate files on success or failure. Their location is printed. |
| `--cookies-from-browser <browser>` | Explicitly permit yt-dlp to use that browser's session. No profile scanning by default. |
| `--allow-duration-mismatch` | Accept a recording that differs significantly from Spotify's duration. |

Conflicting flags, unknown flags, malformed URLs, missing option values, and invalid delays are errors. A playlist reports successes, skips, and failures; any failed track produces exit status 1. Success and skip use 0. Cancellation uses 130 and cleans temporary work unless `--keep-temp` was selected.

### Gemini keys

Gemini is never called by default, even if a key exists. Enable it with `--gemini` or interactive `gemini on`. API use may incur charges under your Google account. A missing key or unavailable model falls back to local analysis with a warning.

```bash
songhero -- keys                  # Status only, never prints the key
songhero -- keys set gemini       # Hidden input, no key in shell history
```

Automation can supply `GEMINI_API_KEY` through its secret environment, or use `songhero -- keys set gemini --stdin` with a secret manager's output. Do not put a literal key in command arguments. Saved `.env` files have owner-only permissions. `GEMINI_MODEL` optionally overrides the configured model fallback list.

### Interactive mode

`generate`, `gen`, and `playlist` accept normal flags. Session settings include `gemini on|off`, `lyrics on|off`, `video on|off|auto`, `skip-existing on|off`, `rate-limit <ms>`, and `output "path with spaces"`. Use `options`, `keys`, `doctor`, `help`, and `exit`. Jobs are serialized, including pasted command sequences.

## Output

New packages include the Spotify track ID in the folder name to distinguish recordings with identical titles:

```text
~/Desktop/Clone Hero/
└── Artist - Song (SongHero 0VjIjW4GlUZAMYd2vXMi3b)/
    ├── notes.chart       # Easy, Medium, Hard, Expert and available lyrics
    ├── song.ini          # Metadata and duration
    ├── song.opus         # Verified Ogg Opus audio
    ├── .songhero.json    # Ownership, source URLs, configuration and file hashes
    ├── album.jpg         # Optional download thumbnail
    ├── lyrics.json       # Optional source lyrics, when included in the chart
    └── video.mp4         # Optional verified H.264 background video
```

Rescan songs in Clone Hero. Video is recognized by its filename; `song.ini` does not need a `video =` setting.

Packages are built in staging directories before publication. A failed rewrite preserves the previous package. SongHero never recursively replaces foreign folders or symlink destinations. Modified or incomplete owned packages require explicit regeneration. Older artist/title folders remain untouched; regenerated tracks use the new ID-based layout, so remove old duplicates yourself after checking them.

All chart events share one consistent timing model. Audio-derived beats retain their measured positions instead of being interpreted under a different, noisy BPM map. Easy and Medium contain single notes with no orange fret; Hard and Expert add density and chords. Notes are deterministic for the same analyzed audio, configuration, and AI response. Live downloads and model responses can change.

Lyrics come from LRCLIB, with plain-text lyrics.ovh as a fallback. Plain-text placement is estimated, not true karaoke synchronization. Lyrics with a conflicting reference duration are omitted with a warning. Gemini does not generate or repair lyric text.

### Repair older background videos

```bash
node scripts/fix-videos.js --dry-run "/path/to/Clone Hero"
node scripts/fix-videos.js "/path/to/Clone Hero"
```

The repair script validates H.264 MP4 output before replacing a source video. Unreadable or failed videos produce a nonzero exit status. Review the dry run before changing a library.

## Verification and troubleshooting

```bash
npm test
node index.js -- doctor
node index.js --help
```

The offline suite covers input validation, subprocess errors and cancellation, media integrity, atomic packaging and rewrites, playlists, lyrics, AI response validation, deterministic notes, timeline alignment, and real audio analysis/chart generation using synthetic recordings. It never uses live Gemini or browser cookies.

- **Missing Node/npm:** rerun the installer or install Node 18+ in the environment you use to run SongHero.
- **Missing Python packages:** install `requirements.txt` into the selected venv, then rerun `doctor`.
- **Windows `node\r` shebang error:** clone inside WSL or rerun the installer to repair LF line endings.
- **Download failure:** update yt-dlp. Use explicit browser-cookie access only if public access fails and you authorize using that session.
- **Duration mismatch:** check that the selected YouTube recording is the intended version before accepting the override.
- **Stale song lock:** after confirming no SongHero process is active, remove only the lock file named in the error.

## License

MIT
