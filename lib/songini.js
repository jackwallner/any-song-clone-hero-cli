function generateSongIni(metadata, analysis, hasVideo) {
  const songLength = analysis.duration_ms || metadata.duration_ms || 0;
  const previewStart = Math.round(songLength * 0.15);
  const hasLyrics = analysis.lyrics && analysis.lyrics.length > 0;
  
  let ini = `[song]
name = ${metadata.name}
artist = ${metadata.artist}
album = ${metadata.album || ''}
genre = ${metadata.genre || 'rock'}
year = ${metadata.year || ''}
charter = SongHero AI
song_length = ${songLength}
diff_band = -1
diff_guitar = 3
diff_bass = 3
diff_drums = -1
diff_drums_real = -1
diff_keys = -1
diff_guitarghl = 3
diff_vocals = ${hasLyrics ? 3 : -1}
preview_start_time = ${previewStart}
`;

  if (hasVideo) {
    ini += `video_start_time = 0
`;
  }

  return ini;
}

module.exports = { generateSongIni };
