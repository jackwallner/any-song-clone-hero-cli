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

### Prerequisites

```bash
# macOS
brew install yt-dlp ffmpeg

# Python dependencies
pip3 install librosa soundfile numpy scipy
```

### Install SongHero

```bash
git clone https://github.com/jackwallner/any-song-clone-hero-cli.git
cd any-song-clone-hero-cli
chmod +x index.js
```

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

## Tech Stack

- **CLI**: Node.js
- **Audio Analysis**: Python (librosa, numpy, scipy)
- **AI Enhancement**: Google Gemini 2.0 Flash
- **Downloads**: yt-dlp + ffmpeg
- **Chart Format**: Clone Hero `.chart` (MIDI-compatible)

## License

MIT
