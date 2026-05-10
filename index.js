#!/usr/bin/env node

const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { downloadSong } = require('./lib/download');
const { generateSongIni } = require('./lib/songini');

const CLONE_HERO_DIR = path.join(require('os').homedir(), 'Desktop', 'Clone Hero');
const OUTPUT_DIR = path.join(__dirname, 'output');
const GEMINI_KEY = process.env.GEMINI_API_KEY || '';

async function main() {
  const args = process.argv.slice(2);
  
  if (args.length < 1 || args.includes('--help') || args.includes('-h')) {
    console.log(`
SongHero CLI - Generate Clone Hero charts from Spotify links

Usage: songhero <spotify_url> [options]

Options:
  --gemini          Use Gemini AI for enhanced note generation
  --video           Force download music video
  --no-video        Skip music video download
  --output <dir>    Output directory (default: Desktop/Clone Hero)
  --keep-temp       Keep temporary files

Examples:
  songhero https://open.spotify.com/track/3DrNvXNKo4cr8YAjxvjgnp
  songhero spotify:track:3DrNvXNKo4cr8YAjxvjgnp --gemini
`);
    process.exit(0);
  }

  const spotifyUrl = args[0];
  const useGemini = args.includes('--gemini');
  const forceVideo = args.includes('--video');
  const noVideo = args.includes('--no-video');
  const keepTemp = args.includes('--keep-temp');
  
  const outputIdx = args.indexOf('--output');
  const outputBase = outputIdx !== -1 ? args[outputIdx + 1] : CLONE_HERO_DIR;

  console.log('🎸 SongHero - AI-Powered Clone Hero Chart Generator');
  console.log('═══════════════════════════════════════════════════\n');

  // Step 1: Resolve Spotify link
  console.log('Step 1/5: Resolving Spotify link...');
  let metadata;
  try {
    const result = execSync(`python3 "${path.join(__dirname, 'python', 'spotify.py')}" "${spotifyUrl}"`, {
      encoding: 'utf-8',
      timeout: 30000
    });
    metadata = JSON.parse(result);
    if (metadata.error) {
      console.error(`  ✗ ${metadata.error}`);
      process.exit(1);
    }
  } catch (e) {
    console.error('  ✗ Failed to resolve Spotify link:', e.message);
    process.exit(1);
  }
  console.log(`  ✓ ${metadata.artist} - ${metadata.name}`);

  // Step 2: Download from YouTube
  console.log('\nStep 2/5: Downloading audio...');
  const workDir = path.join(OUTPUT_DIR, sanitize(`${metadata.artist} - ${metadata.name}`));
  
  let downloadInfo;
  try {
    downloadInfo = await downloadSong(metadata.artist, metadata.name, workDir);
    console.log(`  ✓ Audio downloaded`);
    if (downloadInfo.hasVideo) {
      console.log(`  ✓ Music video downloaded`);
    } else {
      console.log(`  ⚠ No music video found`);
    }
  } catch (e) {
    console.error('  ✗ Download failed:', e.message);
    process.exit(1);
  }

  // Step 3: Analyze audio
  console.log('\nStep 3/5: Analyzing audio with AI...');
  const audioFile = fs.readdirSync(workDir).find(f => f.startsWith('song.'));
  if (!audioFile) {
    console.error('  ✗ No audio file found in download');
    process.exit(1);
  }
  
  const audioPath = path.join(workDir, audioFile);
  let analysis;
  try {
    const geminiFlag = useGemini ? '--gemini' : '';
    const result = execSync(`python3 "${path.join(__dirname, 'python', 'analyze.py')}" "${audioPath}" ${geminiFlag}`, {
      encoding: 'utf-8',
      timeout: 120000,
      maxBuffer: 50 * 1024 * 1024,
      env: { ...process.env, GEMINI_API_KEY: GEMINI_KEY }
    });
    analysis = JSON.parse(result);
    if (analysis.error) {
      console.error(`  ✗ ${analysis.error}`);
      process.exit(1);
    }
  } catch (e) {
    console.error('  ✗ Analysis failed:', e.message);
    process.exit(1);
  }
  
  console.log(`  ✓ Tempo: ${Math.round(analysis.tempo / 10)} BPM`);
  console.log(`  ✓ Key: ${analysis.key}`);
  console.log(`  ✓ Sections detected: ${analysis.sections.length}`);
  console.log(`  ✓ Notes generated: ${Object.values(analysis.difficulties).reduce((s, n) => s + n.length, 0)}`);
  if (analysis.ai_enhanced) {
    console.log(`  ✓ Gemini AI enhancement applied`);
  }

  // Step 4: Generate chart
  console.log('\nStep 4/5: Generating chart file...');
  
  // Write analysis to temp JSON
  const analysisJson = path.join(workDir, 'analysis.json');
  fs.writeFileSync(analysisJson, JSON.stringify(analysis));
  
  const metadataJson = path.join(workDir, 'metadata.json');
  const fullMetadata = {
    name: metadata.name,
    artist: metadata.artist,
    album: metadata.album || '',
    genre: 'rock',
    year: metadata.year || '',
    duration_ms: analysis.duration_ms || metadata.duration_ms || 0
  };
  fs.writeFileSync(metadataJson, JSON.stringify(fullMetadata));
  
  try {
    const chart = execSync(`python3 "${path.join(__dirname, 'python', 'generate_chart.py')}" "${analysisJson}" "${metadataJson}"`, {
      encoding: 'utf-8',
      timeout: 10000
    });
    fs.writeFileSync(path.join(workDir, 'notes.chart'), chart);
    console.log(`  ✓ notes.chart generated`);
  } catch (e) {
    console.error('  ✗ Chart generation failed:', e.message);
    process.exit(1);
  }

  // Step 5: Package for Clone Hero
  console.log('\nStep 5/5: Packaging for Clone Hero...');
  
  // Generate song.ini
  const hasVideo = downloadInfo.hasVideo && !noVideo;
  const songIni = generateSongIni(fullMetadata, analysis, hasVideo);
  fs.writeFileSync(path.join(workDir, 'song.ini'), songIni);
  
  // Determine output folder name
  const folderName = `${metadata.artist} - ${metadata.name} (SongHero AI)`;
  const outputPath = path.join(outputBase, folderName);
  
  // Copy to Clone Hero directory
  fs.mkdirSync(outputPath, { recursive: true });
  
  const filesToCopy = ['notes.chart', 'song.ini', audioFile];
  if (hasVideo) {
    const videoFile = fs.readdirSync(workDir).find(f => f.startsWith('video.'));
    if (videoFile) {
      filesToCopy.push(videoFile);
      // Copy as video.mp4 for Clone Hero (it may need specific naming)
      const videoExt = path.extname(videoFile);
      fs.copyFileSync(path.join(workDir, videoFile), path.join(workDir, `video${videoExt}`));
    }
  }
  if (fs.existsSync(path.join(workDir, 'album.jpg'))) {
    filesToCopy.push('album.jpg');
  }
  
  for (const file of filesToCopy) {
    const src = path.join(workDir, file);
    const dest = path.join(outputPath, file);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, dest);
    }
  }
  
  console.log(`  ✓ Packaged to: ${outputPath}`);
  
  // Cleanup temp files
  if (!keepTemp) {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
  
  console.log('\n═══════════════════════════════════════════════════');
  console.log('✨ Done! Your song is ready in Clone Hero.');
  console.log(`   Location: ${outputPath}`);
  console.log('   Restart Clone Hero or rescan songs to see it.\n');
}

function sanitize(name) {
  return name.replace(/[<>:"/\\|?*]/g, '').replace(/\s+/g, ' ').trim();
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
