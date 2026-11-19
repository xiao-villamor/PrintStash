"""Build a flat, labelled GIF grid and a representative still PNG."""

from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

if __package__:
    from .common import cli, decode, output_path, pack_entries, preview_frame
else:
    from common import cli, decode, output_path, pack_entries, preview_frame


def configure(parser):
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path, help="output stem, without extension")
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--tile", type=int, default=96)
    parser.add_argument("--duration-ms", type=int, help="defaults to longest emoji cycle")
    parser.add_argument("--still-ms", type=int, default=0)
    parser.add_argument("--background", default="#20252f")
    parser.add_argument("--text", default="#ffffff")


def build(args):
    items = [(entry["name"], decode(path)) for entry, path in pack_entries(args.manifest)]
    if args.columns < 1 or args.tile < 1 or (args.duration_ms is not None and args.duration_ms < 20):
        raise ValueError("invalid grid size or duration")
    columns = min(args.columns, len(items))
    rows = (len(items) + columns - 1) // columns
    font = ImageFont.load_default(size=14)
    width = max(args.tile + 20, round(max(font.getlength(name) for name, _ in items)) + 20)
    height = args.tile + 38
    duration = args.duration_ms or max(timeline.duration for _, timeline in items)
    duration = round(duration / 10) * 10
    starts = [timeline.starts for _, timeline in items]
    boundaries = {0, duration}
    for (_, timeline), offsets in zip(items, starts, strict=True):
        if len(offsets) == 1:
            continue
        boundaries.update(
            t for cycle in range(0, duration, timeline.duration) for offset in offsets
            if 0 < (t := cycle + offset) < duration
        )

    def grid(timestamp):
        canvas = Image.new("RGBA", (columns * width, rows * height), args.background)
        draw = ImageDraw.Draw(canvas)
        for index, ((name, timeline), offsets) in enumerate(zip(items, starts, strict=True)):
            left, top = (index % columns) * width, (index // columns) * height
            frame = preview_frame(timeline, offsets, timestamp).copy()
            frame.thumbnail((args.tile, args.tile), Image.Resampling.LANCZOS)
            canvas.alpha_composite(frame, (left + (width - frame.width) // 2, top + 8))
            draw.text((left + width / 2, top + args.tile + 13), name, font=font, anchor="mt", fill=args.text)
        return canvas.convert("RGB")

    times = sorted(boundaries)
    frames = [grid(time) for time in times[:-1]]
    output_path(args.output.with_suffix(".png"))
    grid(args.still_ms).save(args.output.with_suffix(".png"))
    frames[0].save(
        args.output.with_suffix(".gif"), save_all=True, append_images=frames[1:],
        duration=[b - a for a, b in pairwise(times)], loop=0, disposal=2,
    )


if __name__ == "__main__":
    cli(__doc__, configure, build)
