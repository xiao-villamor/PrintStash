"""Encode drawn cels with their exposure times and an optional byte/frame budget."""

from io import BytesIO
from itertools import groupby
from pathlib import Path

from PIL import Image, ImageChops, ImageColor

if __package__:
    from .common import (
        add_timing_flags,
        cli,
        gif_delays,
        output_path,
        read_cel,
        read_timing,
    )
else:
    from common import (
        add_timing_flags,
        cli,
        gif_delays,
        output_path,
        read_cel,
        read_timing,
    )


def configure(parser):
    parser.add_argument("timing", type=Path, help="timing JSON, image, or folder of cels")
    add_timing_flags(parser)
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, help="downsample to this square size")
    parser.add_argument("--max-bytes", type=int)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--matte", help="solid CSS color for compositing soft alpha")
    parser.add_argument("--alpha-threshold", type=int, default=128)
    parser.add_argument("--loop", type=int, default=0, help="GIF loop count, 0 repeats forever")


def palette_frame(frame, colors, dither, matte, threshold):
    # Quantize the cel's visible rectangle so transparent RGB cannot waste palette entries.
    keep = frame.getchannel("A").point(lambda alpha: 255 if alpha >= threshold else 0)
    if matte:
        rgb = frame.convert("RGB")
        differences = ImageChops.difference(rgb, Image.new("RGB", rgb.size, matte))
        red, green, blue = differences.split()
        keep = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        keep = keep.point(lambda value: 255 if value else 0)
    bounds = keep.getbbox()
    result = Image.new("P", frame.size, 255)
    if bounds:
        pixels = frame.crop(bounds)
        palette = pixels.convert("RGB").quantize(colors=colors, dither=dither)
        if dither != Image.Dither.NONE:
            palette = pixels.convert("RGB").quantize(palette=palette, dither=dither)
        result.putpalette(palette.getpalette())
        result.paste(palette, bounds[:2], keep.crop(bounds))
    # Give fully transparent frames a palette too, for disposal and delta encoding.
    entries = result.getpalette() if bounds else []
    reserved = list(matte or (0, 0, 0))
    result.putpalette(entries[:765] + [0] * (765 - min(765, len(entries))) + reserved)
    return result


def encode(frames, delays, loop, transparent):
    stream = BytesIO()
    options = {"transparency": 255} if transparent else {}
    frames[0].save(
        stream, format="GIF", save_all=True, append_images=frames[1:],
        duration=delays, loop=loop, disposal=2, optimize=False, background=255, **options,
    )
    return stream.getvalue()


def exposures(rows, delays, root, size, matte):
    cache = {}
    loaded = []
    for row, ms in zip(rows, delays, strict=True):
        key = row["file"], row.get("source_frame")
        if key not in cache:
            frame = read_cel(root, row)
            if size:
                if frame.width != frame.height or size > frame.width:
                    raise ValueError("--size requires square sources and only downsamples")
                frame = frame.resize((size, size), Image.Resampling.LANCZOS)
            if matte:
                background = Image.new("RGBA", frame.size, (*matte, 255))
                background.alpha_composite(frame)
                frame = background
            cache[key] = frame
        loaded.append((cache[key], ms))
    if len({frame.size for frame, _ in loaded}) != 1:
        raise ValueError("all cels must share a canvas")
    # Combine consecutive identical art into a single hold before trying palettes.
    grouped = []
    for _, run in groupby(loaded, key=lambda exposure: exposure[0].tobytes()):
        group = list(run)
        first = group[0][0]
        hold = sum(delay for _, delay in group)
        grouped.append((first, hold))
    return zip(*grouped, strict=True)


def assemble(args):
    if (args.size is not None and args.size < 1) or not 1 <= args.alpha_threshold <= 255:
        raise ValueError("invalid size or alpha threshold")
    if not 0 <= args.loop <= 65535:
        raise ValueError("loop count must be 0–65535")
    matte = ImageColor.getcolor(args.matte, "RGB") if args.matte else None
    rows, delays = read_timing(args.timing, args.timing_spec, args.frame_ms)
    frames, holds = exposures(rows, delays, args.timing.parent, args.size, matte)
    holds = gif_delays(holds)
    if len(frames) < 2:
        raise ValueError("animation needs two distinct exposures; use PNG for stills")
    smallest = None
    for colors in (255, 127, 63, 31, 15, 7):
        for dither in (Image.Dither.FLOYDSTEINBERG, Image.Dither.NONE):
            paletted = [palette_frame(frame, colors, dither, matte, args.alpha_threshold) for frame in frames]
            data = encode(paletted, holds, args.loop, matte is None)
            with Image.open(BytesIO(data)) as gif:
                count = gif.n_frames
            if count < 2 or (args.max_frames is not None and count > args.max_frames):
                continue
            smallest = min(smallest or len(data), len(data))
            if args.max_bytes is None or len(data) <= args.max_bytes:
                output_path(args.output).write_bytes(data)
                print(f"{args.output.name}: {count} frames, {sum(holds)} ms, {len(data)} bytes")
                return
    raise ValueError(
        f"could not fit byte/frame budget (smallest eligible encode: {smallest}); "
        "replace low-value in-betweens with holds and try again"
    )


if __name__ == "__main__":
    cli(__doc__, configure, assemble)
