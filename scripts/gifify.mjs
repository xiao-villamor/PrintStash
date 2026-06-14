// Converts the recorded .webm flows (screenshots/.video-tmp) into optimized
// animated GIFs, using Playwright's bundled ffmpeg to extract frames and sharp
// to assemble them. The bundled ffmpeg is a minimal build (scale/crop/pad only,
// no gif/fps/palette filters), so we extract frames at a low rate and replay
// them faster, which both speeds up the clip and keeps frame counts sane.
import { mkdir, rm, readdir, stat } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";

const run = promisify(execFile);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(ROOT, "screenshots");
const TMP = path.join(OUT, ".video-tmp");

// Locate Playwright's bundled ffmpeg.
function findFfmpeg() {
  const base = path.join(os.homedir(), ".cache/ms-playwright");
  const dir = readdirSync(base).find((d) => d.startsWith("ffmpeg-"));
  if (!dir) throw new Error("bundled ffmpeg not found");
  return path.join(base, dir, "ffmpeg-linux");
}

// Resolve sharp from frontend's pnpm store.
function loadSharp() {
  const require = createRequire(path.join(ROOT, "frontend/package.json"));
  try {
    return require("sharp");
  } catch {
    const store = path.join(ROOT, "frontend/node_modules/.pnpm");
    const d = readdirSync(store).find((x) => x.startsWith("sharp@"));
    return require(path.join(store, d, "node_modules/sharp"));
  }
}

const FF = findFfmpeg();
const sharp = loadSharp();

// clip: { name, src, width, rate, delay, colours }
//  rate  = frames/sec sampled from the source
//  delay = ms between frames in the GIF (lower => faster playback)
const CLIPS = [
  { name: "00-demo.gif", src: "demo.webm", width: 860, rate: 6, delay: 60, colours: 128 },
  { name: "11-revision-compare.gif", src: "compare.webm", width: 900, rate: 5, delay: 115, colours: 96 },
  { name: "12-tag-filter.gif", src: "filter.webm", width: 900, rate: 6, delay: 100, colours: 110 },
];

async function buildClip(clip) {
  const src = path.join(TMP, clip.src);
  if (!existsSync(src)) {
    console.log("  skip (missing)", clip.src);
    return;
  }
  const frameDir = path.join(TMP, "frames-" + clip.name);
  await rm(frameDir, { recursive: true, force: true });
  await mkdir(frameDir, { recursive: true });

  // Extract scaled frames (even height required by some encoders → scale -2).
  await run(FF, [
    "-y", "-i", src,
    "-r", String(clip.rate),
    "-vf", `scale=${clip.width}:-2`,
    path.join(frameDir, "f%04d.png"),
    "-loglevel", "error",
  ]);

  const files = (await readdir(frameDir)).filter((f) => f.endsWith(".png")).sort();
  if (!files.length) {
    console.log("  no frames for", clip.name);
    return;
  }
  const meta = await sharp(path.join(frameDir, files[0])).metadata();
  // Join the frames into a true animated GIF (join.animated treats each input
  // image as a frame, rather than stacking them into one tall static image).
  const frames = files.map((f) => path.join(frameDir, f));
  const out = path.join(OUT, clip.name);
  await sharp(frames, { join: { animated: true } })
    .gif({ loop: 0, delay: files.map(() => clip.delay), colours: clip.colours })
    .toFile(out);

  await rm(frameDir, { recursive: true, force: true });
  const kb = Math.round((await stat(out)).size / 1024);
  const secs = ((files.length * clip.delay) / 1000).toFixed(1);
  console.log(`  ✓ ${clip.name}  ${meta.width}x${meta.height}  ${files.length}fr  ~${secs}s  ${kb}KB`);
}

for (const clip of CLIPS) {
  await buildClip(clip);
}
console.log("GIFs done →", OUT);
