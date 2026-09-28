# 🎸 Any Song Clone Hero CLI

Generate Clone Hero charts from **any Spotify link or YouTube video** — guitar and
pro drums, AI-assisted note generation, automatic difficulty scaling, synced
lyrics and music video support.

```
songhero https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b
songhero "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --ai claude
```

## How It Works

1. **Resolve** — Reads song metadata from the Spotify page or the YouTube video
   (no API key needed)
2. **Download** — Fetches audio + music video with yt-dlp: the exact video for a
   YouTube link, otherwise a YouTube search for "artist - title"
3. **Analyze** — Audio analysis with librosa, refined by an LLM (Gemini, Claude or Codex)
   - Beat & onset detection
   - Pitch-to-fret mapping
   - Section detection (verse, chorus, bridge)
4. **Drums** (optional) — Separates the drum stem with Demucs and charts kick,
   snare, hi-hat, ride, crash and toms as pro drums
5. **Generate** — Writes one `.chart` with guitar and drums, 4 difficulties each
6. **Package** — Outputs a complete Clone Hero song folder

All notes are placed with one constant tempo that the chart also declares, so
note timing matches the audio for the whole song.

## Difficulty Levels

Guitar:

| Difficulty | Note Density | Orange Fret | Chords | Description |
|-----------|-------------|-------------|--------|-------------|
| **Easy** | 25% | No | No | Simple patterns on strong beats |
| **Medium** | 50% | No | No | Faster, more notes, no orange |
| **Hard** | 70% | Yes | Yes | Orange notes, some chords |
| **Expert** | 90% | Yes | Yes | Dense, all notes, complex patterns |

Drums follow how human charters reduce a part: Expert has every detected hit
(at most two pads plus kick at once), Hard drops ghost notes, Medium keeps the
snare but thins kicks and cymbals to 8th notes, Easy plays one drum at a time
on the beat plus 8th-note snares.

## Installation

Supported platforms: **macOS** and **Linux**. On Windows, run SongHero inside
WSL (see [Windows](#windows) below). Node.js 18+ and Python 3 are required.

### Prerequisites

The installer below sets all of this up for you. To do it by hand:

```bash
# macOS
brew install node python yt-dlp ffmpeg

# Debian / Ubuntu (including WSL)
sudo apt install -y nodejs npm python3 python3-venv ffmpeg
node -v   # must be 18 or newer; if not, install Node 20 from https://deb.nodesource.com
curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o ~/.local/bin/yt-dlp
chmod +x ~/.local/bin/yt-dlp

# Python dependencies (Debian and Ubuntu refuse a plain pip install into the
# system Python, so use a venv)
python3 -m venv ~/.songhero/venv
~/.songhero/venv/bin/pip install librosa soundfile numpy scipy
```

### Install SongHero

The one-line installer works on macOS, Linux, and WSL. It installs Node, Python,
ffmpeg, and yt-dlp, then verifies every dependency before it reports success:

```bash
curl -sSL https://jackwallner.com/songhero/install.sh | bash
```

To install from source instead:

```bash
git clone https://github.com/jackwallner/any-song-clone-hero-cli.git
cd any-song-clone-hero-cli
npm install
chmod +x index.js
```

### Windows

SongHero is not a Windows-native program, and double-clicking `index.js` in
Explorer will not work: Windows hands `.js` files to Windows Script Host, which
chokes on the shebang and reports `Invalid character` at line 1, char 1. Use WSL
instead, which gives you a real Linux environment inside Windows.

1. In PowerShell as Administrator, run `wsl --install`
2. Reboot, open the **Ubuntu** app, and set a username and password
3. In the Ubuntu shell, run the installer:

   ```bash
   curl -sSL https://jackwallner.com/songhero/install.sh | bash
   ```

4. Reopen Ubuntu, then run `songhero <spotify_url>`

A fresh WSL Ubuntu has no Node.js, so the installer adds it. If you see
`env: 'node': No such file or directory`, you are on a build from before the
installer did that: rerun the command above and it will repair the install.

Charts land in your Linux home directory. Reach them from Windows Explorer by
typing `\\wsl$` in the address bar.

### Optional: Gemini AI Enhancement

```bash
export GEMINI_API_KEY="your-key-here"
# Or create a .env file
cp .env.example .env
```

### Optional: Claude or Codex instead of Gemini (no API key)

If you have [Claude Code](https://claude.com/claude-code) or the
[Codex CLI](https://github.com/openai/codex) installed and logged in, SongHero can
send the section analysis through that CLI instead of the Gemini API. It then
runs on your Claude or ChatGPT subscription, and no API key is needed.

```bash
songhero <spotify_url> --ai claude
songhero <spotify_url> --ai codex

# Or make it the default
export SONGHERO_AI=claude
```

The call runs in an empty temp directory with tools disabled (Claude) or a
read-only sandbox (Codex). `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` are stripped
from its environment so the login is used rather than a key. Expect about
5–15 seconds per song instead of about 1 second with Gemini, and each song uses
some of your subscription quota.

| Variable | Effect |
|----------|--------|
| `SONGHERO_AI` | Default provider: `gemini`, `claude` or `codex` |
| `SONGHERO_OUTPUT` | Default output folder, e.g. `~/Clone Hero/Songs` |
| `SONGHERO_CLAUDE_MODEL` | Model passed to `claude --model` |
| `SONGHERO_CLAUDE_BIN` | Path to `claude` if it is not on `PATH` |
| `SONGHERO_CODEX_MODEL` | Model passed to `codex exec -m` (overrides `~/.codex/config.toml`) |
| `SONGHERO_CODEX_EFFORT` | Codex reasoning effort (default `low`) |
| `SONGHERO_CODEX_BIN` | Path to `codex`; otherwise found on `PATH`, in Codex.app or the VS Code extension |
| `SONGHERO_AI_TIMEOUT` | Seconds before a CLI call is abandoned (default 180) |
| `SONGHERO_DRUMS` | `0` turns drum charting off by default |

Put any of these in `.env` next to `index.js` (see `.env.example`) to make them
the default, so `songhero <url>` needs no flags.

### Drums (optional)

SongHero can also chart **pro drums** (4 lanes with cymbal markers, four
difficulties). It separates the drum stem with [Demucs](https://github.com/facebookresearch/demucs),
detects hits per frequency band (kick, snare, hi-hat, ride, crash, toms) and
snaps them to the beat grid. Install Demucs into the SongHero venv to turn it on:

```bash
~/.songhero/venv/bin/pip install demucs   # pulls in PyTorch (~2 GB)
```

Drums are then charted by default; skip them with `--no-drums` or
`SONGHERO_DRUMS=0`. Separation takes about 30 s per song on Apple Silicon (GPU)
and a few minutes on CPU. The model (80 MB) downloads on first use.

Accuracy against a human-charted live-drummer song: kick F1 0.78, snare 0.84,
cymbals/toms 0.73. Hi-hat vs ride vs crash is a heuristic and toms are charted
conservatively, so expect to see fewer toms than a human charter would add.

### YouTube links

You can chart a YouTube video directly. Title and artist come from the video
(YouTube Music fields when present, otherwise the "Artist - Title" in the video
title), and audio and music video are taken from that exact video instead of a
YouTube search:

```bash
songhero https://www.youtube.com/watch?v=dQw4w9WgXcQ --ai claude

# Fix the name if the video title is messy (used for lyrics lookup and the folder name)
songhero https://youtu.be/xxxx --artist "Queen" --title "Bohemian Rhapsody"
```

To keep Spotify metadata but pick the exact video yourself (for example when
the search lands on a live version), pass it with `--youtube`:

```bash
songhero https://open.spotify.com/track/xxxx --youtube https://youtu.be/xxxx
```

A video chosen by URL is always downloaded as the background video unless you
pass `--no-video`. YouTube playlists are not supported yet.

## Usage

```bash
# Basic usage: Spotify track/playlist or YouTube video
./index.js <spotify_or_youtube_url>

# Full options
./index.js <url> [options]

Options:
  --ai <provider>   AI for note mapping: gemini (default), claude or codex
  --no-drums        Skip drum charting
  --youtube <url>   With a Spotify link: use this YouTube video for audio/video
  --artist <name>   Override the artist name
  --title <name>    Override the song title
  --no-ai           Skip AI enhancement (alias: --no-gemini)
  --video           Force download music video
  --no-video        Skip music video download
  --no-lyrics       Skip karaoke lyrics
  --output <dir>    Output directory (default: ~/Desktop/Clone Hero, or $SONGHERO_OUTPUT)
  --rewrite         Overwrite a song that was already charted
  --keep-temp       Keep temporary files (including the separated drums.wav)
```

### Examples

```bash
# Quick chart (no AI, no drums, no video)
./index.js https://open.spotify.com/track/3DrNvXNKo4cr8YAjxvjgnp --no-ai --no-drums --no-video

# Full experience with Claude instead of a Gemini key
./index.js spotify:track:0VjIjW4GlUZAMYd2vXMi3b --ai claude --video

# Straight from YouTube
./index.js "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Custom output
./index.js "https://open.spotify.com/track/..." --output ~/Documents/Charts
```

## Output

Each song is saved as a Clone Hero-ready folder:

```
~/Desktop/Clone Hero/          (or --output / $SONGHERO_OUTPUT)
└── Artist - Song Name (SongHero AI)/
    ├── notes.chart    # Guitar + drums, 4 difficulties each
    ├── song.ini       # Song metadata
    ├── song.opus      # High-quality audio
    ├── album.jpg      # Album artwork
    └── video.mp4      # Music video (if available)
```

Clone Hero picks the background video up automatically from the `video.mp4`
filename. There is no `video = ` key in `song.ini`; the only video setting is
the optional `video_start_time`.

### Background video codec

Clone Hero only decodes **H.264** video. YouTube now serves most 1080p mp4
streams as AV1, which Clone Hero loads as a black screen. SongHero prefers an
H.264 stream and re-encodes with ffmpeg when only AV1/VP9 is available.

Charts made with an older SongHero build can be repaired in place:

```bash
node scripts/fix-videos.js --dry-run     # list videos that need re-encoding
node scripts/fix-videos.js               # re-encode them to H.264
node scripts/fix-videos.js "/path/to/Clone Hero"
```

## Troubleshooting

### `env: 'node': No such file or directory`

Two different problems print this, and the second line of the error tells them
apart.

**If that is the only line**, Node.js is not installed in the environment you
are running from. A fresh WSL Ubuntu has no Node. Rerun the installer:

```bash
curl -sSL https://jackwallner.com/songhero/install.sh | bash
```

**If it is followed by** `env: use -[v]S to pass options in shebang lines`,
Node is beside the point: your copy of `index.js` has Windows (CRLF) line
endings. The shebang then reads as `node\r` rather than `node`, and no
installed Node will ever match it. This happens when the repository is cloned
by Windows git, which rewrites line endings on checkout, and then run from WSL.

Confirm it:

```bash
head -1 index.js | od -c | head -2      # a trailing \r means CRLF
```

Fix your existing copy:

```bash
sed -i 's/\r$//' index.js lib/*.js python/*.py
```

Or reinstall, which repairs line endings automatically:

```bash
curl -sSL https://jackwallner.com/songhero/install.sh | bash
```

Clone inside WSL rather than in Windows to avoid it entirely. Charts land in
your Linux home directory; reach them from Explorer by typing `\\wsl$` in the
address bar.

### `Cannot find module 'dotenv'`

The Node dependencies were never installed. Run `npm install` in the install
directory (`~/.songhero` for installer-based installs).

## Tech Stack

- **CLI**: Node.js
- **Audio Analysis**: Python (librosa, numpy, scipy)
- **Drum separation**: Demucs (PyTorch), optional
- **AI Enhancement**: Google Gemini API, or the Claude Code / Codex CLI on your subscription
- **Downloads**: yt-dlp + ffmpeg
- **Chart Format**: Clone Hero `.chart` (MIDI-compatible)

## License

MIT
