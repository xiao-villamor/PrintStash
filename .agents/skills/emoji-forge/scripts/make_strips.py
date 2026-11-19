"""Show every decoded frame with a timestamp on light and dark backgrounds."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

if __package__:
    from .common import (
        Timeline,
        add_timing_flags,
        cli,
        decode,
        output_path,
        read_cel,
        read_timing,
    )
else:
    from common import (
        Timeline,
        add_timing_flags,
        cli,
        decode,
        output_path,
        read_cel,
        read_timing,
    )


def configure(parser):
    parser.add_argument("input", type=Path)
    add_timing_flags(parser)
    parser.add_argument("output", type=Path)
    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument("--size", type=int, nargs="+", default=[32, 128, 512])


def strips(args):
    if args.input.is_dir() or args.input.suffix.lower() == ".json":
        rows, delays = read_timing(args.input, args.timing_spec, args.frame_ms)
        timeline = Timeline([read_cel(args.input.parent, row) for row in rows], delays)
    else:
        timeline = decode(args.input)
    if args.columns < 1 or min(args.size) < 1:
        raise ValueError("column count and sizes must be positive")
    font = ImageFont.load_default(size=13)
    columns = min(args.columns, len(timeline.frames))
    sections = len(timeline.frames[::columns])
    row_height = 2 * sum(size + 4 for size in args.size) + 29
    cell_width = max(100, max(args.size) + 8)
    canvas = Image.new("RGB", (columns * cell_width, sections * row_height), "#e9edf3")
    draw = ImageDraw.Draw(canvas)
    for i, (frame, start) in enumerate(zip(timeline.frames, timeline.starts, strict=True)):
        x, y = (i % columns) * cell_width + 4, (i // columns) * row_height + 4
        draw.text((x, y), f"{i + 1} / {start:g} ms", fill="#192234", font=font)
        y += 21
        for size in args.size:
            resample = Image.Resampling.NEAREST if size >= frame.width else Image.Resampling.LANCZOS
            thumb = frame.resize((size, size), resample)
            for background in ("#222632", "#ffffff"):
                tile = Image.new("RGBA", thumb.size, background)
                tile.alpha_composite(thumb)
                canvas.paste(tile.convert("RGB"), (x, y))
                y += size + 4
    output_path(args.output)
    canvas.save(args.output)


if __name__ == "__main__":
    cli(__doc__, configure, strips)
