# Review tools and tips

Start with the target checks for file dimensions, bytes and format limits. Machine checks can also measure decoded edges and seams, and optionally body-mask drift and face containment. An intentional matte may occupy the edge. Use the actual export for visual review.

- `forge strips` shows each frame with a timestamp. Try the intended display size, the native canvas and a 4× view on light and dark backgrounds. The zoom can expose clipping, palette artefacts and stray pixels.
- Play the animation at normal speed to see if it achieves the intended effect. Watch the seam, timing, attachments, contrast or expression when those details matter to the idea.
- `forge snapshot` can save a baseline before a revision. `forge compare` presents versions side by side on the same clock. A comparison helps when you change an art technique or effect.
- For a detailed handoff, record what you observed, the version and the frame numbers or timestamps for any changes you suggest. After a revision, check the affected export again.

An example observation might be “Frames 5–6 at 1.10–1.38 s. The raised hand is readable on light but blends with the outline on dark at 22 px.” Name the intended result and let that guide the fix.
