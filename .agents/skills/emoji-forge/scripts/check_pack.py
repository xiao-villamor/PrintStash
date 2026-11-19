"""Check target limits, frame seams, and character geometry from supplied masks."""

import argparse
import json
from pathlib import Path
from statistics import median

from common import cli, decode, image, mask_geometry, pack_entries, read_timing
from PIL import Image, ImageChops, ImageColor, ImageStat


def configure(parser):
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--size", type=int, default=128)
    parser.add_argument("--max-bytes", type=int, default=128000)
    parser.add_argument("--max-frames", type=int, default=50)
    parser.add_argument("--drift-px", type=float, default=2)
    parser.add_argument(
        "--seam", type=float, help="optional maximum mean seam difference, 0–1"
    )
    parser.add_argument("--require-masks", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--manifest-limits", action="store_true")


def check_geometry(timing, size, tolerance):
    rows, _ = read_timing(timing)
    measurements = []
    for index, row in enumerate(rows, 1):
        body = image(timing.parent / row["body"], "L")
        face = image(timing.parent / row["face"], "L")
        if (
            body.size != face.size
            or body.size != image(timing.parent / row["file"]).size
        ):
            raise ValueError(f"exposure {index}: mask and cel canvas sizes differ")
        if body.width != body.height:
            raise ValueError(f"exposure {index}: expected square masks")
        factor = size / body.width
        (x, y), (width, height) = mask_geometry(body)
        dx, dy, dw = row.get("motion", [0, 0, 0])
        metrics = (x - dx, y - dy, width - dw, height - dw * height / width)
        measurements.append(tuple(metric * factor for metric in metrics))
        body, face = [
            layer.point(lambda channel: 255 if channel >= 128 else 0)
            for layer in (body, face)
        ]
        if not face.getbbox() or ImageChops.subtract(face, body).getbbox():
            raise ValueError(f"exposure {index}: face mask is empty or leaves body")
    centers = [median(column) for column in zip(*measurements, strict=True)]
    for index, metrics in enumerate(measurements, 1):
        drift = max(
            abs(value - center) for value, center in zip(metrics, centers, strict=True)
        )
        if drift > tolerance:
            raise ValueError(
                f"exposure {index}: centroid/scale drift {drift:.2f}px exceeds {tolerance}px"
            )


def border_is_clear(frame, matte):
    edges = [
        frame.crop((0, 0, frame.width, 1)),
        frame.crop((0, frame.height - 1, frame.width, frame.height)),
        frame.crop((0, 0, 1, frame.height)),
        frame.crop((frame.width - 1, 0, frame.width, frame.height)),
    ]
    if matte:
        return all(
            edge.getcolors(1) == [(edge.width * edge.height, matte)] for edge in edges
        )
    return all(edge.getchannel("A").getextrema() == (0, 0) for edge in edges)


def seam_difference(first, last):
    # Premultiplied color ignores invisible RGB while alpha still measures silhouette changes.
    difference = ImageChops.difference(first.convert("RGBa"), last.convert("RGBa"))
    return sum(ImageStat.Stat(difference).mean) / (4 * 255)


def check_emoji(args, entry, path):
    size = path.stat().st_size
    if size > args.max_bytes:
        raise ValueError(f"{size} bytes exceeds {args.max_bytes}")
    with Image.open(path) as source:
        if source.size != (args.size, args.size):
            raise ValueError(f"expected {args.size}×{args.size}, got {source.size}")
        animated = getattr(source, "n_frames", 1) > 1
        if animated and source.format != "GIF":
            raise ValueError("expected GIF for animated raster")
        if source.format == "GIF" and (
            not animated or source.n_frames > args.max_frames
        ):
            raise ValueError(
                f"expected 2–{args.max_frames} GIF frames, got {source.n_frames}"
            )
        repeat = source.info.get("loop")
        if animated and "loop" in entry and repeat != entry["loop"]:
            raise ValueError("unexpected GIF loop metadata")
    timeline = decode(path)
    matte = ImageColor.getcolor(entry["matte"], "RGBA") if "matte" in entry else None
    for index, frame in enumerate(timeline.frames, 1):
        if not border_is_clear(frame, matte):
            raise ValueError(f"frame {index}: pixels reach canvas border")
    if not animated:
        return {"frames": 1, "bytes": size, "size": [args.size] * 2}
    seam = seam_difference(timeline.frames[0], timeline.frames[-1])
    limit = entry.get("seam", args.seam)
    if limit is not None and seam > limit:
        raise ValueError(
            f"frames {len(timeline.frames)} → 1: seam difference {seam:.3f}"
        )
    timing = entry.get("timing")
    if timing:
        timing = args.manifest.parent / timing
        check_geometry(timing, args.size, args.drift_px)
        requested = read_timing(timing)[1]
        minimum = round(sum(requested) / 10) * 10
        maximum = max(minimum, 20 * len(requested))
        if not minimum <= timeline.duration <= maximum:
            raise ValueError(
                "decoded duration differs from annotated timing beyond GIF rounding"
            )
    elif args.require_masks:
        raise ValueError("animated entry is missing timing with body and face masks")
    return {
        "frames": len(timeline.frames),
        "duration_ms": timeline.duration,
        "bytes": size,
        "size": [args.size] * 2,
        "loop": repeat,
        "seam": round(seam, 5),
        "geometry_checked": bool(timing),
    }


def check(args):
    results = []
    for entry, path in pack_entries(args.manifest):
        result = {"name": entry["name"], "path": str(path)}
        try:
            settings = vars(args).copy()
            if args.manifest_limits:
                for field in ("size", "max_bytes", "max_frames"):
                    settings[field] = entry.get("limits", {}).get(
                        field, settings[field]
                    )
            result.update(check_emoji(argparse.Namespace(**settings), entry, path))
        except (ValueError, KeyError, OSError) as error:
            result.update(ok=False, error=str(error))
        else:
            result["ok"] = True
        results.append(result)
    ok = all(result["ok"] for result in results)
    if args.json:
        print(json.dumps({"ok": ok, "emojis": results}))
    else:
        for result in results:
            if result["ok"]:
                suffix = ", geometry checked" if result.get("geometry_checked") else ""
                print(
                    f"PASS {result['name']}: {result['frames']} frame(s), {result['bytes']} B{suffix}"
                )
            else:
                print(f"FAIL {result['name']}: {result['error']}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    cli(__doc__, configure, check)
