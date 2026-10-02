const { spawnSync } = require('child_process');

const YOUTUBE_HOST = /^(?:https?:\/\/)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)\//i;

function isYouTubeUrl(url) {
  return typeof url === 'string' && YOUTUBE_HOST.test(url);
}

// Video titles carry noise like "(Official Music Video)" or "[HD]" that breaks
// the lyrics lookup, so strip the common tags.
const TITLE_NOISE = /\s*[([](?:official|lyric|lyrics|audio|video|music video|hd|hq|4k|remaster(?:ed)?|visuali[sz]er|live)[^)\]]*[)\]]/gi;

function cleanTitle(title) {
  return title.replace(TITLE_NOISE, '').replace(/\s+/g, ' ').trim();
}

function cleanChannel(name) {
  return (name || '').replace(/\s*-\s*Topic$/i, '').replace(/VEVO$/i, '').trim();
}

// Resolves a YouTube video into the same metadata shape spotify.py returns.
// YouTube Music uploads carry proper artist/track fields; for everything else
// the "Artist - Title" convention in the video title is the best guess, with
// the channel name as the last resort.
function resolveYouTube(url) {
  const res = spawnSync('yt-dlp', [
    '--dump-single-json', '--skip-download', '--no-playlist', '--no-warnings', url,
  ], { encoding: 'utf-8', timeout: 60000, maxBuffer: 20 * 1024 * 1024 });
  if (res.error) return { error: `yt-dlp failed: ${res.error.message}` };
  if (res.status !== 0) {
    return { error: `yt-dlp could not read the video: ${(res.stderr || '').trim().slice(-300)}` };
  }

  let info;
  try {
    info = JSON.parse(res.stdout);
  } catch (e) {
    return { error: 'yt-dlp returned invalid JSON' };
  }

  const rawTitle = info.title || '';
  let artist = (info.artist || info.creator || '').split(',')[0].trim();
  let name = info.track || '';
  if (!name) {
    // "Artist - Title", also with en/em dashes
    const m = rawTitle.match(/^(.+?)\s+[-\u2013\u2014]\s+(.+)$/);
    if (m) {
      if (!artist) artist = m[1].trim();
      name = m[2];
    } else {
      name = rawTitle;
    }
  }
  if (!artist) artist = cleanChannel(info.channel || info.uploader);

  // upload_date is when the video went up, not when the song came out
  const year = info.release_year ? String(info.release_year) : '';

  return {
    id: info.id,
    name: cleanTitle(name) || rawTitle,
    artist: artist || 'Unknown Artist',
    artists: [artist],
    album: info.album || '',
    album_art: info.thumbnail || '',
    year,
    duration_ms: Math.round((info.duration || 0) * 1000),
    youtube_url: info.webpage_url || url,
    youtube_title: rawTitle,
  };
}

module.exports = { isYouTubeUrl, resolveYouTube, cleanTitle };
