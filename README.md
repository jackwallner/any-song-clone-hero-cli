# 🎸 Any Song Clone Hero CLI

Generate Clone Hero charts from **any Spotify link** — with AI-powered note generation, automatic difficulty scaling, and music video support.

```
songhero https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b --gemini --video
```

## How It Works

1. **Resolve** — Extracts song metadata from Spotify (no API key needed)
2. **Download** — Fetches audio + music video from YouTube via yt-dlp
3. **Analyze** — AI-powered audio analysis using librosa + Gemini
   - Beat & onset detection
   - Pitch-to-fret mapping
   - Section detection (verse, chorus, bridge)
4. **Generate** — Creates `.chart` files with 4 difficulty levels
5. **Package** — Outputs a complete Clone Hero song folder

## Difficulty Levels

| Difficulty | Note Density | Orange Fret | Chords | Description |
|-----------|-------------|-------------|--------|-------------|
| **Easy** | 25% | No | No | Simple patterns on strong beats |
| **Medium** | 50% | No | No | Faster, more notes, no orange |
| **Hard** | 70% | Yes | Yes | Orange notes, some chords |
| **Expert** | 90% | Yes | Yes | Dense, all notes, complex patterns |

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

## Usage

```bash
# Basic usage
./index.js <spotify_url>

# With AI enhancement + music video
./index.js <spotify_url> --gemini --video

# Full options
./index.js <spotify_url> [options]

Options:
  --gemini          Use Gemini AI for enhanced note generation
  --video           Force download music video
  --no-video        Skip music video download
  --output <dir>    Output directory (default: ~/Desktop/Clone Hero)
  --keep-temp       Keep temporary files
```

### Examples

```bash
# Quick chart (no AI, no video)
./index.js https://open.spotify.com/track/3DrNvXNKo4cr8YAjxvjgnp

# Full experience
./index.js spotify:track:0VjIjW4GlUZAMYd2vXMi3b --gemini --video

# Custom output
./index.js "https://open.spotify.com/track/..." --output ~/Documents/Charts
```

## Output

Each song is saved as a Clone Hero-ready folder:

```
~/Desktop/Clone Hero/
└── Artist - Song Name (SongHero AI)/
    ├── notes.chart    # All 4 difficulties
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

## Tech Stack

- **CLI**: Node.js
- **Audio Analysis**: Python (librosa, numpy, scipy)
- **AI Enhancement**: Google Gemini 2.0 Flash
- **Downloads**: yt-dlp + ffmpeg
- **Chart Format**: Clone Hero `.chart` (MIDI-compatible)

## License

MIT
