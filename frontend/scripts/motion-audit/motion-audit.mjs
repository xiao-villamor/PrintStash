#!/usr/bin/env node
/**
 * Frame-by-frame motion audit for the Library.
 *
 *   node scripts/motion-audit/motion-audit.mjs --base http://localhost:3470 \
 *     --out ../.motion-audit [--scenario sidebar-close,sidebar-open,load,folder] [--path "/?c=..."]
 *     [--cache cold|warm] (cold by default: load/folder run with the HTTP cache off)
 *
 * Every scenario performs one real user gesture at 1920x1080 and records, on
 * every animation frame, the geometry and *effective* opacity (own × ancestors)
 * of each tracked element. The trace is analysed for the defects a screenshot
 * never shows: teleports (a jump between two still frames), pop-ins and
 * pop-outs (an element appearing or vanishing above 10% opacity), skeleton →
 * card geometry mismatches, dropped frames, long animation frames, and motion
 * that happens in two separate bursts (e.g. a reflow that waits for rest).
 *
 * Output per scenario: <out>/<scenario>/report.json, filmstrip.png (every
 * screencast frame with its timestamp) and frames/NNN.jpg. A summary of all
 * findings is printed and written to <out>/report.md. Exit code 1 when any
 * scenario has findings, so it can gate an iteration.
 *
 * Sign-in uses PRINTSTASH_USER / PRINTSTASH_PASSWORD when the app redirects.
 */
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "@playwright/test";

const { values: args } = parseArgs({
  options: {
    base: { type: "string", default: "http://localhost:3470" },
    out: { type: "string", default: ".motion-audit" },
    path: {
      type: "string",
      default: "/?type=all&sort=date-desc&c=printstash-data%2Fprintstash-data%2Forganization&cid=8",
    },
    scenario: { type: "string", default: "sidebar-close,sidebar-open,load,folder" },
    latency: { type: "string", default: "700" },
    // "cold" disables the HTTP cache for data scenarios, so a load shows a real
    // first visit (thumbnails downloaded, not replayed from disk cache).
    cache: { type: "string", default: "cold" },
    scheme: { type: "string", default: "dark" },
    headed: { type: "boolean", default: false },
    viewport: { type: "string", default: "1920x1080" },
  },
});

/** Elements whose motion is audited. Cards and skeleton cards carry data-library-key. */
const TRACKED = [
  ["[data-library-key]", (el) => el.getAttribute("data-library-key")],
  [".library-model-toolbar-wrap", () => "toolbar"],
  ["main h1", () => "title"],
  ["main h2", (el, i) => `heading:${i}`],
  ["main button[aria-expanded]", (el, i) => `disclosure:${i}`],
  ["main [role=status]", (el, i) => `status:${i}`],
  [".library-model-inspector", () => "inspector"],
  ["[role=dialog]", (el, i) => `dialog:${i}`],
];

const THRESHOLDS = {
  teleportPx: 12, // a single-frame move larger than this between still frames
  stillPx: 1.5,
  popOpacity: 0.9, // first visible frame at or above this opacity is a pop-in
  vanishOpacity: 0.1,
  droppedFrameMs: 34,
  longFrameMs: 50,
  burstGapMs: 60, // stillness between two motion bursts that makes them "sequential"
  mismatchPx: 2,
  // A glide longer than this whose first frame covers more than abruptFraction
  // of its travel reads as a jump, not a glide.
  abruptMinPx: 40,
  abruptFraction: 0.35,
  // A dialog that takes longer than this from the click to be visible is slow.
  dialogReadyMs: 300,
  // Allow a couple of frames for a 200 ms desktop pane animation to paint.
  sidebarTravelMs: 250,
};

/** Injected sampler. Runs in the page; records until stop() resolves. */
function installSampler(tracked) {
  const selectors = tracked.map(([selector, key]) => [selector, new Function(`return (${key})`)()]);
  const main = () => document.querySelector("main");
  const sidebar = () => document.getElementById("collections-pane");
  const effectiveOpacity = (element) => {
    let opacity = 1;
    for (let node = element; node && node !== document.documentElement; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.visibility === "hidden" || style.display === "none") return 0;
      opacity *= Number(style.opacity);
      if (opacity === 0) return 0;
    }
    return opacity;
  };
  // Visible part of the element after every clipping ancestor (pane clip, scroller).
  const clipped = (element) => {
    let rect = element.getBoundingClientRect();
    let { left, top, right, bottom } = rect;
    for (let node = element.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (
        style.overflowX !== "visible" ||
        style.overflowY !== "visible" ||
        style.contain.includes("paint")
      ) {
        const clip = node.getBoundingClientRect();
        if (style.overflowX !== "visible") {
          left = Math.max(left, clip.left);
          right = Math.min(right, clip.right);
        }
        if (style.overflowY !== "visible") {
          top = Math.max(top, clip.top);
          bottom = Math.min(bottom, clip.bottom);
        }
      }
    }
    left = Math.max(left, 0);
    top = Math.max(top, 0);
    right = Math.min(right, innerWidth);
    bottom = Math.min(bottom, innerHeight);
    return {
      rect,
      visibleArea: Math.max(0, right - left) * Math.max(0, bottom - top),
      // Horizontal fraction left after clipping: a card cut by its row's edge.
      visibleX: rect.width ? Math.max(0, right - left) / rect.width : 1,
    };
  };
  const frames = [];
  const longFrames = [];
  const shifts = [];
  let alive = true;
  const started = performance.now();
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries())
        longFrames.push({
          t: entry.startTime - started,
          duration: entry.duration,
          blocking: entry.blockingDuration,
          scripts: (entry.scripts ?? []).slice(0, 3).map((s) => ({
            source: `${s.sourceFunctionName || "?"} ${String(s.sourceURL).split("/").pop()}:${s.sourceCharPosition}`,
            duration: Math.round(s.duration),
          })),
        });
    }).observe({ type: "long-animation-frame", buffered: false });
  } catch {}
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries())
        shifts.push({ t: entry.startTime - started, value: entry.value });
    }).observe({ type: "layout-shift", buffered: false });
  } catch {}
  const sample = (now) => {
    if (!alive) return;
    const elements = {};
    const scope = main() ?? document;
    for (const [selector, keyOf] of selectors) {
      const root = selector.startsWith("main") ? document : scope;
      [...root.querySelectorAll(selector)].forEach((element, index) => {
        const key = keyOf(element, index);
        if (!key || elements[key]) return;
        const { rect, visibleArea, visibleX } = clipped(element);
        if (!rect.width && !rect.height) return;
        elements[key] = {
          x: Math.round(rect.left * 10) / 10,
          y: Math.round(rect.top * 10) / 10,
          w: Math.round(rect.width * 10) / 10,
          h: Math.round(rect.height * 10) / 10,
          o: Math.round(effectiveOpacity(element) * 1000) / 1000,
          area: Math.round(visibleArea),
          vx: Math.round(visibleX * 1000) / 1000,
          skeleton: !!element.closest(".library-loading-placeholder"),
          // A card under its opaque fill placeholder is not visible yet.
          covered: (() => {
            const fill = element.querySelector(".library-card-fill");
            return !!fill && effectiveOpacity(fill) > 0.5;
          })(),
          // The thumbnail shows through a fill whose preview area is see-through.
          imageThrough: (() => {
            const fill = element.querySelector(".library-card-fill");
            const image = element.querySelector("[data-library-thumbnail] img");
            if (!fill || !image || effectiveOpacity(fill) <= 0.5) return false;
            if (!(image.complete && image.naturalWidth > 0) || effectiveOpacity(image) <= 0.5)
              return false;
            const preview = fill.querySelector(".library-card-fill-preview");
            const alpha = (color) => {
              const match = color.match(/rgba?\(([^)]+)\)/);
              if (!match) return 1;
              const parts = match[1].split(/[ ,/]+/).filter(Boolean);
              return parts.length > 3 ? Number(parts[3]) : 1;
            };
            return !preview || alpha(getComputedStyle(preview).backgroundColor) < 0.5;
          })(),
          // Thumbnail readiness: a ready preview that turns pending is a media flash.
          media: (() => {
            const frame = element.querySelector("[data-library-thumbnail]");
            if (!frame) return null;
            const state = frame.getAttribute("data-library-thumbnail");
            if (state === "missing" || state === "failed") return state;
            const image = frame.querySelector("img");
            return image &&
              image.complete &&
              image.naturalWidth > 0 &&
              effectiveOpacity(image) > 0.5
              ? "ready"
              : "pending";
          })(),
        };
      });
    }
    // Grids wider than their box (more columns than fit), outside pane motion.
    const overflow =
      "paneMoving" in document.documentElement.dataset
        ? []
        : [...document.querySelectorAll("main .library-virtual-row")]
            .filter(
              (row) =>
                row.scrollWidth > row.clientWidth + 2 && row.querySelector("[data-library-key]"),
            )
            .map(
              (row) =>
                `${row.closest("[data-library-renderer]")?.parentElement?.className?.slice(0, 40) ?? "grid"}: content ${row.scrollWidth}px in ${row.clientWidth}px (${row.style.gridTemplateColumns})`,
            );
    frames.push({
      t: now - started,
      elements,
      overflow,
      sidebarWidth: sidebar()?.offsetWidth,
      workspaceLeft: main()?.getBoundingClientRect().left,
      workspaceScroll: main()?.scrollTop,
    });
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
  window.__motionAudit = {
    started,
    stop: () => {
      alive = false;
      return { frames, longFrames, shifts };
    },
  };
}

function analyse({ frames, longFrames, shifts }, label) {
  const findings = [];
  const add = (kind, t, detail) => findings.push({ kind, t: Math.round(t), detail });
  const sidebar =
    label === "sidebar-close" || label === "sidebar-open"
      ? measureSidebarTravel(frames, label === "sidebar-open", add)
      : null;
  // Dropped frames: rAF gaps. The first frame after the gesture often includes the click itself.
  for (let i = 1; i < frames.length; i++) {
    const dt = frames[i].t - frames[i - 1].t;
    if (dt > THRESHOLDS.droppedFrameMs)
      add(
        "dropped-frames",
        frames[i - 1].t,
        `${Math.round(dt)} ms between frames (~${Math.round(dt / 16.7) - 1} lost)`,
      );
  }
  for (const entry of longFrames)
    if (entry.duration > THRESHOLDS.longFrameMs)
      add(
        "long-frame",
        entry.t,
        `${Math.round(entry.duration)} ms frame, blocking ${Math.round(entry.blocking)} ms; ${entry.scripts.map((s) => `${s.source} ${s.duration}ms`).join(", ")}`,
      );
  const totalShift = shifts.reduce((sum, s) => sum + s.value, 0);
  if (totalShift > 0.01)
    add("layout-shift", shifts[0].t, `CLS ${totalShift.toFixed(3)} over ${shifts.length} shifts`);

  const keys = new Set(frames.flatMap((f) => Object.keys(f.elements)));
  const movingAt = new Map(); // frame index -> moving element count
  for (const key of keys) {
    const trace = frames.map((f) => f.elements[key] ?? null);
    for (let i = 1; i < trace.length; i++) {
      const prev = trace[i - 1];
      const cur = trace[i];
      const t = frames[i].t;
      const shown = (e) => e && e.area > 64 && e.o > THRESHOLDS.vanishOpacity;
      if (cur && !shown(prev) && shown(cur) && cur.o >= THRESHOLDS.popOpacity) {
        // Revealed by a moving clip edge is fine; that grows area gradually.
        const fullArea = cur.w * cur.h;
        if (!prev || prev.o <= THRESHOLDS.vanishOpacity || cur.area > fullArea * 0.5)
          add(
            "pop-in",
            t,
            `${key} appears at opacity ${cur.o} (${Math.round(cur.w)}×${Math.round(cur.h)} at ${Math.round(cur.x)},${Math.round(cur.y)})`,
          );
      }
      if (
        shown(prev) &&
        !shown(cur) &&
        prev.o > THRESHOLDS.popOpacity &&
        prev.area > prev.w * prev.h * 0.5
      )
        add("pop-out", t, `${key} vanishes from opacity ${prev.o}`);
      if (prev && cur && prev.media === "ready" && cur.media !== "ready" && cur.area > 0)
        add("media-flash", t, `${key} thumbnail ready → ${cur.media}`);
      if (
        !prev &&
        cur &&
        cur.area > 0 &&
        cur.media &&
        cur.media !== "ready" &&
        !cur.skeleton &&
        frames[i - 1] &&
        Object.keys(frames[i - 1].elements).length &&
        i > 1 &&
        frames.slice(0, i).some((f) => Object.values(f.elements).some((e) => e.media === "ready"))
      )
        add("media-blank", t, `${key} mounts without its thumbnail (${cur.media})`);
      // A preview that reads as missing/failed and later shows is a false
      // failure message flashed during an ordinary load.
      if (
        cur &&
        cur.media === "ready" &&
        trace.slice(0, i).some((e) => e && (e.media === "missing" || e.media === "failed"))
      )
        if (!trace.slice(0, i).some((e) => e && e.media === "ready"))
          add(
            "false-failure",
            t,
            `${key} showed a missing/failed preview before its thumbnail arrived`,
          );
      if (cur && cur.imageThrough && !(prev && prev.imageThrough))
        add(
          "image-before-title",
          t,
          `${key} shows its picture while its title is still a placeholder`,
        );
      if (prev && cur && Math.abs(cur.o - prev.o) > 0.45)
        add("opacity-snap", t, `${key} opacity ${prev.o} → ${cur.o} in one frame`);
      if (prev && cur) {
        const d = Math.hypot(cur.x - prev.x, cur.y - prev.y);
        const ds = Math.max(Math.abs(cur.w - prev.w), Math.abs(cur.h - prev.h));
        if (d > THRESHOLDS.stillPx || ds > THRESHOLDS.stillPx)
          movingAt.set(i, (movingAt.get(i) ?? 0) + 1);
        const before = trace[i - 2];
        const after = trace[i + 1];
        const still = (a, b) =>
          !a || !b || Math.hypot(a.x - b.x, a.y - b.y) <= THRESHOLDS.stillPx * 2;
        if (
          cur.area > 0 &&
          (d > THRESHOLDS.teleportPx || ds > THRESHOLDS.teleportPx) &&
          still(before, prev) &&
          still(cur, after)
        )
          add(
            "teleport",
            t,
            `${key} jumps ${Math.round(cur.x - prev.x)},${Math.round(cur.y - prev.y)} px / size ${Math.round(cur.w - prev.w)}×${Math.round(cur.h - prev.h)} in one frame`,
          );
      }
    }
  }
  // Abrupt glides: an element that travels far but covers most of the way in
  // its first moving frame. Teleports are caught above; this catches curves
  // that front-load the motion so it reads as a jump.
  for (const key of keys) {
    const trace = frames.map((f) => f.elements[key] ?? null);
    let start = null;
    for (let i = 1; i <= trace.length; i++) {
      const prev = trace[i - 1];
      const cur = trace[i];
      const moving = prev && cur && Math.hypot(cur.x - prev.x, cur.y - prev.y) > THRESHOLDS.stillPx;
      if (moving && start === null) start = i - 1;
      if (!moving && start !== null) {
        const from = trace[start];
        const to = trace[i - 1];
        const second = trace[start + 1];
        if (!from || !to || !second) {
          start = null;
          continue;
        }
        const travel = Math.hypot(to.x - from.x, to.y - from.y);
        const first = Math.hypot(second.x - from.x, second.y - from.y);
        if (travel > THRESHOLDS.abruptMinPx && first / travel > THRESHOLDS.abruptFraction)
          add(
            "abrupt-glide",
            frames[start + 1].t,
            `${key} covers ${Math.round((first / travel) * 100)}% of ${Math.round(travel)} px in its first frame (${Math.round(frames[i - 1].t - frames[start].t)} ms total)`,
          );
        start = null;
      }
    }
  }
  // Dialog timing: from the gesture to the first frame a dialog is visible,
  // and to the frame it reaches full opacity.
  const dialogShown = frames.find((f) =>
    Object.entries(f.elements).some(([k, e]) => k.startsWith("dialog") && e.o > 0.05),
  )?.t;
  const dialogOpaque = frames.find((f) =>
    Object.entries(f.elements).some(([k, e]) => k.startsWith("dialog") && e.o > 0.99),
  )?.t;
  if (dialogShown !== undefined && dialogShown > THRESHOLDS.dialogReadyMs)
    add(
      "slow-dialog",
      dialogShown,
      `dialog first visible ${Math.round(dialogShown)} ms after the gesture`,
    );
  // Rest invariants on the last frame: nothing the user can see is cut by a
  // horizontal clip, and no grid holds more columns than its width.
  const rest = frames.at(-1);
  if (rest)
    for (const [key, e] of Object.entries(rest.elements))
      if (key.startsWith("/") || key.startsWith("collection:"))
        if (e.y < 4000 && e.y + e.h > 0 && e.vx > 0 && e.vx < 0.99)
          add(
            "clipped-at-rest",
            rest.t,
            `${key} shows ${Math.round(e.vx * 100)}% of its width at ${Math.round(e.x)},${Math.round(e.y)}`,
          );
  for (const overflow of rest?.overflow ?? []) add("grid-overflow", rest.t, overflow);
  // Cards must never pass over the Models toolbar (e.g. flying from a stale origin).
  const overlaps = new Set();
  for (const frame of frames) {
    const bar = frame.elements.toolbar;
    if (!bar) continue;
    for (const [key, e] of Object.entries(frame.elements)) {
      if (!key.startsWith("/") || e.o < 0.1 || e.area === 0 || overlaps.has(key)) continue;
      const vertical = Math.min(e.y + e.h, bar.y + bar.h) - Math.max(e.y, bar.y);
      const horizontal = Math.min(e.x + e.w, bar.x + bar.w) - Math.max(e.x, bar.x);
      if (vertical > 4 && horizontal > 4) {
        overlaps.add(key);
        add("overlap", frame.t, `${key} covers the Models toolbar by ${Math.round(vertical)} px`);
      }
    }
  }
  // Skeleton → card handoff: the card at index i should take skeleton i's box.
  const lastSkeleton = [...frames]
    .reverse()
    .find((f) => Object.values(f.elements).some((e) => e.skeleton));
  if (lastSkeleton) {
    const skeletons = Object.entries(lastSkeleton.elements)
      .filter(([, e]) => e.skeleton && e.area > 0)
      .map(([, e]) => e)
      .sort((a, b) => a.y - b.y || a.x - b.x);
    const firstReal = frames.find(
      (f) =>
        f.t > lastSkeleton.t &&
        Object.values(f.elements).some((e) => !e.skeleton && e.area > 0 && e.w > 150),
    );
    if (firstReal) {
      const cards = Object.entries(firstReal.elements)
        .filter(([key, e]) => !e.skeleton && e.area > 0 && e.w > 150 && key.startsWith("/"))
        .map(([key, e]) => ({ key, ...e }))
        .sort((a, b) => a.y - b.y || a.x - b.x);
      skeletons.forEach((s, i) => {
        const c = cards.find(
          (card) => Math.abs(card.x - s.x) < s.w / 2 && Math.abs(card.y - s.y) < s.h / 2,
        );
        if (!c)
          return add(
            "skeleton-orphan",
            firstReal.t,
            `skeleton ${i} at ${Math.round(s.x)},${Math.round(s.y)} has no card in its place`,
          );
        const delta = Math.max(
          Math.abs(c.x - s.x),
          Math.abs(c.y - s.y),
          Math.abs(c.w - s.w),
          Math.abs(c.h - s.h),
        );
        if (delta > THRESHOLDS.mismatchPx)
          add(
            "skeleton-mismatch",
            firstReal.t,
            `skeleton ${i} ${Math.round(s.w)}×${Math.round(s.h)}@${Math.round(s.x)},${Math.round(s.y)} → ${c.key} ${Math.round(c.w)}×${Math.round(c.h)}@${Math.round(c.x)},${Math.round(c.y)}`,
          );
      });
    }
  }
  // Motion bursts: a stillness gap followed by more motion means two sequential phases.
  const bursts = [];
  for (let i = 1; i < frames.length; i++) {
    if (!movingAt.has(i)) continue;
    const last = bursts.at(-1);
    if (last && frames[i].t - last.end <= THRESHOLDS.burstGapMs) last.end = frames[i].t;
    else bursts.push({ start: frames[i].t, end: frames[i].t });
  }
  if (bursts.length > 1)
    add(
      "sequential-motion",
      bursts[1].start,
      `motion in ${bursts.length} bursts: ${bursts.map((b) => `${Math.round(b.start)}–${Math.round(b.end)} ms`).join(", ")}`,
    );
  const dts = frames
    .slice(1)
    .map((f, i) => f.t - frames[i].t)
    .sort((a, b) => a - b);
  return {
    label,
    frames: frames.length,
    durationMs: Math.round(frames.at(-1)?.t ?? 0),
    medianFrameMs: Math.round((dts[Math.floor(dts.length / 2)] ?? 0) * 10) / 10,
    p95FrameMs: Math.round((dts[Math.floor(dts.length * 0.95)] ?? 0) * 10) / 10,
    motionSettledMs: Math.round(bursts.at(-1)?.end ?? 0),
    sidebar,
    phases: phases(frames),
    dialog:
      dialogShown === undefined
        ? null
        : {
            shown: Math.round(dialogShown),
            opaque: dialogOpaque === undefined ? null : Math.round(dialogOpaque),
          },
    bursts,
    findings: dedupe(findings),
  };
}

function measureSidebarTravel(frames, opening, add) {
  const from = frames[0]?.sidebarWidth;
  const to = frames.at(-1)?.sidebarWidth;
  if (
    from === undefined ||
    to === undefined ||
    (opening ? to - from : from - to) <= THRESHOLDS.stillPx
  ) {
    add(
      "sidebar-not-toggled",
      0,
      `expected pane to ${opening ? "open" : "close"}, width ${from} → ${to}px`,
    );
    return null;
  }
  const first = frames.findIndex(
    (frame) => Math.abs(frame.sidebarWidth - from) > THRESHOLDS.stillPx,
  );
  const last = frames.find((frame) => Math.abs(frame.sidebarWidth - to) <= THRESHOLDS.stillPx);
  const travelMs = Math.round(last.t - frames[first - 1].t);
  if (travelMs > THRESHOLDS.sidebarTravelMs)
    add("slow-sidebar", last.t, `${travelMs} ms to settle the pane width`);
  const movingFrames = frames.slice(first, frames.indexOf(last));
  const anchor = frames[first - 1];
  const firstTravel = Math.abs(frames[first].workspaceLeft - anchor.workspaceLeft);
  if (
    firstTravel > THRESHOLDS.abruptMinPx &&
    firstTravel > Math.abs(to - from) * THRESHOLDS.abruptFraction
  )
    add(
      "workspace-snaps",
      frames[first].t,
      `workspace moves ${Math.round(firstTravel)} px in its first frame`,
    );
  const final = frames.at(-1);
  if (anchor.workspaceScroll === 0 && final.workspaceScroll > THRESHOLDS.stillPx)
    add(
      "workspace-scroll-jump",
      final.t,
      `workspace scroll changes from 0 to ${final.workspaceScroll} px`,
    );
  for (const frame of frames.slice(first, frames.indexOf(last) + 1)) {
    const panelTravel = frame.sidebarWidth - anchor.sidebarWidth;
    const workspaceTravel = frame.workspaceLeft - anchor.workspaceLeft;
    if (Math.abs(panelTravel - workspaceTravel) > THRESHOLDS.mismatchPx)
      add(
        "workspace-out-of-sync",
        frame.t,
        `pane ${Math.round(panelTravel)} px; workspace ${Math.round(workspaceTravel)} px`,
      );
  }
  const initialFrame = movingFrames[0];
  for (const { t, elements, workspaceLeft } of movingFrames.slice(1)) {
    const workspaceTravel = workspaceLeft - initialFrame.workspaceLeft;
    for (const [key, card] of Object.entries(elements)) {
      if (!key.startsWith("/models") && !key.startsWith("collection:")) continue;
      const initial = initialFrame.elements[key];
      if (!initial || initial.area === 0 || card.area === 0) continue;
      const change = Math.max(
        Math.abs(card.x - initial.x - workspaceTravel),
        Math.abs(initial.y - card.y),
        Math.abs(initial.w - card.w),
        Math.abs(initial.h - card.h),
      );
      if (change > THRESHOLDS.mismatchPx)
        add("card-moves-with-sidebar", t, `${key} shifts ${Math.round(change)} px`);
    }
  }
  return { from, to, travelMs };
}

/** When each stage of the Model grid became visible: placeholder, cards with
 * data, and the thumbnails of the cards on screen (first, median, last). */
function phases(frames) {
  const onScreen = (e) => e.area > 0 && e.o > 0.5 && !e.covered;
  const first = (test) =>
    frames.find((f) => Object.entries(f.elements).some(([k, e]) => test(k, e)))?.t;
  const skeleton = first((k, e) => e.skeleton && k.startsWith("skeleton") && onScreen(e));
  const cards = first((k, e) => k.startsWith("/models") && !e.skeleton && onScreen(e));
  const ready = new Map();
  for (const f of frames)
    for (const [k, e] of Object.entries(f.elements))
      if (k.startsWith("/models") && onScreen(e) && e.media === "ready" && !ready.has(k))
        ready.set(k, f.t);
  const visible = new Set();
  const last = frames.at(-1);
  for (const [k, e] of Object.entries(last?.elements ?? {}))
    if (k.startsWith("/models") && e.area > 0 && e.y < 1080) visible.add(k);
  const times = [...visible].map((k) => ready.get(k) ?? Infinity).sort((a, b) => a - b);
  const r = (v) => (v === undefined ? null : v === Infinity ? "never" : Math.round(v));
  return {
    skeletonShown: r(skeleton),
    cardsShown: r(cards),
    firstImage: r(times[0]),
    medianImage: r(times[Math.floor(times.length / 2)]),
    lastImage: r(times.at(-1)),
    visibleCards: visible.size,
  };
}

/** Requests made because of the gesture: count, bytes, and the critical path. */
function summariseNetwork(network = []) {
  const byType = {};
  for (const r of network) {
    byType[r.type] ??= { count: 0, bytes: 0 };
    byType[r.type].count++;
    byType[r.type].bytes += r.size;
  }
  const slowest = [...network].sort((a, b) => b.end - b.start - (a.end - a.start)).slice(0, 5);
  return {
    requests: network.length,
    lastEndMs: network.reduce((m, r) => Math.max(m, r.end), 0),
    byType,
    slowest: slowest.map(
      (r) => `${r.path} ${r.end - r.start} ms (${Math.round(r.size / 1024)} KB)`,
    ),
  };
}

/** Collapse the same defect on many cards in the same frame into one line. */
function dedupe(findings) {
  const groups = new Map();
  for (const f of findings) {
    const id = `${f.kind}@${f.t}`;
    const group = groups.get(id) ?? { kind: f.kind, t: f.t, count: 0, examples: [] };
    group.count++;
    if (group.examples.length < 3) group.examples.push(f.detail);
    groups.set(id, group);
  }
  return [...groups.values()].sort((a, b) => a.t - b.t);
}

async function filmstrip(context, shots, file, title) {
  if (!shots.length) return;
  const page = await context.newPage();
  const cells = shots
    .map(
      (s) =>
        `<figure><img src="data:image/jpeg;base64,${s.data}"><figcaption>${Math.round(s.t)} ms</figcaption></figure>`,
    )
    .join("");
  await page.setContent(`<!doctype html><style>
    body{margin:0;background:#111;color:#eee;font:14px monospace}
    h1{font-size:16px;margin:8px}
    main{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:6px}
    figure{margin:0;position:relative} img{width:100%;display:block;outline:1px solid #333}
    figcaption{position:absolute;left:4px;top:4px;background:#000c;padding:2px 6px;color:#ff0}
  </style><h1>${title}</h1><main>${cells}</main>`);
  await page.screenshot({ path: file, fullPage: true });
  await page.close();
}

async function signIn(page, url) {
  await page.goto(url);
  const login = page.getByRole("textbox", { name: "Username" });
  await Promise.race([
    login.waitFor({ timeout: 30000 }),
    page.waitForSelector("[data-library-key]", { timeout: 30000 }),
  ]);
  if (!(await login.isVisible())) return;
  const user = process.env.PRINTSTASH_USER;
  const password = process.env.PRINTSTASH_PASSWORD;
  if (!user || !password)
    throw new Error("Sign-in required: set PRINTSTASH_USER and PRINTSTASH_PASSWORD");
  await page.getByRole("textbox", { name: "Username" }).fill(user);
  await page.getByRole("textbox", { name: "Password" }).fill(password);
  await page.evaluate(() => document.querySelector("form")?.requestSubmit());
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 30000 });
  await page.goto(url);
}

async function record(page, cdp, name, gesture, settleMs = 1500) {
  const inflight = new Set();
  page.on("request", (r) => r.url().includes("/models/browse") && inflight.add(r));
  page.on("requestfinished", (r) => inflight.delete(r));
  page.on("requestfailed", (r) => inflight.delete(r));
  const shots = [];
  const network = [];
  let networkStart = null;
  const started = new Map();
  page.on("request", (r) => networkStart !== null && started.set(r, Date.now() - networkStart));
  page.on("requestfinished", async (r) => {
    if (!started.has(r)) return;
    const size = (await r.sizes().catch(() => null))?.responseBodySize ?? 0;
    const url = new URL(r.url());
    network.push({
      path: url.pathname + (url.search.length > 60 ? url.search.slice(0, 60) + "…" : url.search),
      type: r.resourceType(),
      start: started.get(r),
      end: Date.now() - networkStart,
      size,
    });
  });
  let t0 = null;
  const onFrame = async ({ data, metadata, sessionId }) => {
    const t = metadata.timestamp * 1000;
    t0 ??= t;
    shots.push({ t, data });
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  };
  cdp.on("Page.screencastFrame", onFrame);
  if (args.cache === "cold" && (name === "load" || name === "folder" || name === "upload-open")) {
    await cdp.send("Network.enable");
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
  }
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 70, everyNthFrame: 1 });
  await page.waitForTimeout(150);
  const plan = gesture instanceof Function ? { gesture } : gesture;
  if (!plan.reload)
    await page.evaluate(
      installSampler,
      TRACKED.map(([s, k]) => [s, k.toString()]),
    );
  const wallStart = Date.now();
  networkStart = wallStart;
  await plan.gesture();
  // Data scenarios settle when Model cards exist outside any placeholder and
  // no browse request is in flight; then the tail is recorded too.
  if (plan.reload || name === "folder") {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      await page.waitForTimeout(250);
      const ready = await page
        .evaluate(
          () =>
            document.querySelectorAll(
              "main [data-library-key^='/models']:not(.library-loading-placeholder *)",
            ).length > 0,
        )
        .catch(() => false);
      if (ready && inflight.size === 0) break;
    }
  }
  await page.waitForTimeout(settleMs);
  const trace = await page.evaluate(() => window.__motionAudit.stop());
  await cdp.send("Page.stopScreencast");
  cdp.off("Page.screencastFrame", onFrame);
  // Screencast timestamps are wall-clock seconds; align them to the gesture.
  const aligned = shots.map((s) => ({ ...s, t: s.t - wallStart })).filter((s) => s.t > -100);
  return { trace: { ...trace, network }, shots: aligned, name };
}

async function prepareSidebar(page, open) {
  const toggle = page.getByRole("button", { name: "Toggle sidebar", exact: true });
  if ((await toggle.getAttribute("aria-expanded")) !== String(open)) await toggle.click();
  await page.waitForFunction((expected) => {
    const pane = document.getElementById("collections-pane");
    return (
      pane?.dataset.open === String(expected) && !("paneMoving" in document.documentElement.dataset)
    );
  }, open);
  return () => toggle.click();
}

const scenarios = {
  async rest() {
    // No gesture: records the page at rest to check the rest invariants.
    return async () => {};
  },
  async "sidebar-close"(page) {
    return prepareSidebar(page, true);
  },
  async "sidebar-open"(page) {
    return prepareSidebar(page, false);
  },
  async "inspector-open"(page) {
    return () => page.locator('button[aria-label="Show or hide details"]').click();
  },
  async "inspector-close"(page) {
    await page.locator('button[aria-label="Show or hide details"]').click();
    await page.waitForTimeout(1200);
    return () => page.locator('button[aria-label="Show or hide details"]').click();
  },
  async "upload-open"(page) {
    // Cold: the first Upload in a session, so its code and reads are not cached.
    return () => page.getByRole("button", { name: "Upload", exact: true }).click();
  },
  async "upload-close"(page) {
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await page.getByRole("dialog").waitFor();
    await page.waitForTimeout(1200);
    return () => page.getByRole("dialog").getByRole("button", { name: "Close" }).first().click();
  },
  async load(page, { latency }) {
    // A cold, slow read: the skeleton must appear, then hand over to the cards in place.
    await page.route("**/api/v1/models/browse?**", async (route) => {
      await new Promise((r) => setTimeout(r, latency));
      await route.continue();
    });
    await page.addInitScript(
      installSampler,
      TRACKED.map(([s, k]) => [s, k.toString()]),
    );
    return { reload: true, gesture: () => page.reload({ waitUntil: "commit" }) };
  },
  async folder(page, { latency }) {
    await page.route("**/api/v1/models/browse?**", async (route) => {
      await new Promise((r) => setTimeout(r, latency));
      await route.continue();
    });
    return () =>
      page.locator("main [data-library-key^='collection:'] [data-collection-path]").first().click();
  },
};

const browser = await chromium.launch({ headless: !args.headed });
const [vw, vh] = args.viewport.split("x").map(Number);
const context = await browser.newContext({
  viewport: { width: vw, height: vh },
  colorScheme: args.scheme,
});
const outRoot = path.resolve(args.out);
await mkdir(outRoot, { recursive: true });
const summary = [];
for (const name of args.scenario.split(",")) {
  const page = await context.newPage();
  const url = args.base + args.path;
  await signIn(page, url);
  await page.waitForSelector("[data-library-key]", { timeout: 30000 });
  await page.waitForTimeout(2500);
  await page.mouse.move(1915, 1075);
  const cdp = await context.newCDPSession(page);
  const gesture = await scenarios[name](page, { url: args.path, latency: Number(args.latency) });
  const settle = 1500;
  const { trace, shots } = await record(page, cdp, name, gesture, settle);
  const result = analyse(trace, name);
  result.network = summariseNetwork(trace.network);
  const dir = path.join(outRoot, name);
  // A previous run's frames in the same folder would mix two recordings.
  await rm(dir, { recursive: true, force: true });
  await mkdir(path.join(dir, "frames"), { recursive: true });
  await writeFile(path.join(dir, "report.json"), JSON.stringify({ ...result, trace }, null, 1));
  await Promise.all(
    shots.map((s, i) =>
      writeFile(
        path.join(dir, "frames", `${String(i).padStart(3, "0")}_${Math.round(s.t)}ms.jpg`),
        Buffer.from(s.data, "base64"),
      ),
    ),
  );
  await filmstrip(
    context,
    shots,
    path.join(dir, "filmstrip.png"),
    `${name} — ${shots.length} frames`,
  );
  summary.push(result);
  await page.close();
}
await browser.close();

const lines = [];
for (const r of summary) {
  lines.push(
    `## ${r.label}`,
    `frames ${r.frames} · median ${r.medianFrameMs} ms · p95 ${r.p95FrameMs} ms · motion settled ${r.motionSettledMs} ms · bursts ${r.bursts.length}`,
  );
  const ph = r.phases;
  if (ph.cardsShown !== null)
    lines.push(
      `phases: skeleton ${ph.skeletonShown ?? "—"} ms → cards ${ph.cardsShown} ms → images first ${ph.firstImage} / median ${ph.medianImage} / last ${ph.lastImage} ms (${ph.visibleCards} on screen)`,
    );
  if (r.dialog)
    lines.push(`dialog: visible ${r.dialog.shown} ms · opaque ${r.dialog.opaque ?? "never"} ms`);
  if (r.sidebar)
    lines.push(`sidebar: ${r.sidebar.from} → ${r.sidebar.to} px in ${r.sidebar.travelMs} ms`);
  if (r.network?.requests)
    lines.push(
      `network: ${r.network.requests} requests, done ${r.network.lastEndMs} ms · ${Object.entries(
        r.network.byType,
      )
        .map(([t, v]) => `${t} ${v.count} (${Math.round(v.bytes / 1024)} KB)`)
        .join(", ")}`,
      ...r.network.slowest.map((x) => `  - ${x}`),
    );
  if (!r.findings.length) lines.push("- no findings");
  for (const f of r.findings)
    lines.push(`- **${f.kind}** @${f.t} ms ×${f.count}: ${f.examples.join(" | ")}`);
  lines.push("");
}
await writeFile(path.join(outRoot, "report.md"), lines.join("\n"));
console.log(lines.join("\n"));
process.exit(summary.some((r) => r.findings.length) ? 1 : 0);
