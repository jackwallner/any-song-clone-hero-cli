'use strict';

function iniText(value) {
  return String(value ?? '').replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim();
}

function generateSongIni(metadata, analysis, hasVideo) {
  const songLength = analysis.duration_ms || metadata.duration_ms || 0;
  if (!Number.isFinite(songLength) || songLength <= 0) throw new Error('Invalid song duration.');
  const hasLyrics = Boolean(analysis.lyrics?.length);
  const values = {
    name: iniText(metadata.name), artist: iniText(metadata.artist), album: iniText(metadata.album),
    genre: iniText(metadata.genre || 'rock'), year: iniText(metadata.year), charter: 'SongHero',
    song_length: Math.round(songLength), diff_band: -1, diff_guitar: 3, diff_bass: -1,
    diff_drums: -1, diff_drums_real: -1, diff_keys: -1, diff_guitarghl: -1,
    diff_vocals: hasLyrics ? 3 : -1, preview_start_time: Math.round(songLength * 0.15),
  };
  if (hasVideo) values.video_start_time = 0;
  return '[song]\n' + Object.entries(values).map(([key, value]) => `${key} = ${value}`).join('\n') + '\n';
}

module.exports = { generateSongIni, iniText };
