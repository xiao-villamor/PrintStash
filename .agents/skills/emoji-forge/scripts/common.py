"""Shared input formats for the command-line tools."""

import argparse
import json
import math
import re
from bisect import bisect_right
from dataclasses import dataclass
from itertools import accumulate, pairwise
from pathlib import Path

from PIL import Image, ImageSequence


def cli(description, configure, run):
    parser = argparse.ArgumentParser(description=description)
    configure(parser)
    try:
        run(parser.parse_args())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def image(path, mode="RGBA"):
    with Image.open(path) as source:
        return source.convert(mode)


@dataclass(frozen=True)
class Timeline:
    frames: list[Image.Image]
    delays: list[int]

    @property
    def duration(self):
        return sum(self.delays)

    @property
    def starts(self):
        return [0, *accumulate(self.delays[:-1])]


RASTER = {".gif", ".png", ".jpg", ".jpeg"}


def image_files(path):
    if not path.is_dir():
        if path.suffix.lower() not in RASTER or not path.is_file():
            raise ValueError(f"expected an image file or folder: {path}")
        return [path.resolve()]
    return sorted(
        item.resolve()
        for item in path.iterdir()
        if item.is_file()
        and item.suffix.lower() in RASTER
        and not item.stem.endswith(("-body", "-face"))
    )


def cel_rows(path):
    rows = []
    for file in image_files(path):
        row = {"file": str(file)}
        for layer in ("body", "face"):
            sidecar = file.with_name(f"{file.stem}-{layer}.png")
            if sidecar.is_file():
                row[layer] = str(sidecar)
        if file.suffix.lower() == ".gif":
            with Image.open(file) as source:
                rows.extend(
                    {
                        **row,
                        "source_frame": index,
                        "ms": frame.info.get("duration") or 100,
                    }
                    for index, frame in enumerate(ImageSequence.Iterator(source))
                )
        else:
            rows.append(row)
    return rows


def read_cel(root, row):
    with Image.open(root / row["file"]) as cel:
        cel.seek(row.get("source_frame", 0))
        return cel.convert("RGBA")


def plain_name(path):
    return re.sub(r"[^a-z0-9_-]+", "-", path.stem.lower()).strip("-_") or "emoji"


def ease(amount, name):
    if name == "linear":
        return amount
    if name == "ease-in":
        return amount * amount
    if name == "ease-out":
        return 1 - (1 - amount) ** 2
    if name == "ease-in-out":
        return amount * amount * (3 - 2 * amount)
    if name == "hold":
        return 0.0
    raise ValueError(f"unknown easing: {name!r}")


def exposure_delays(rows, timing=None, default=100):
    if not rows:
        raise ValueError("timing needs at least one exposure")
    if timing is None:
        delays = [row.get("ms", default) for row in rows]
    elif isinstance(timing, list):
        delays = timing
    elif "durations_ms" in timing:
        delays = timing["durations_ms"]
    else:
        keys = timing.get("keyframes")
        if keys is None:
            keys = [
                {"frame": 0, "ms": 0, "ease": timing.get("easing", "linear")},
                {"frame": len(rows), "ms": timing["duration_ms"]},
            ]
        if not keys or (keys[0]["frame"], keys[0]["ms"], keys[-1]["frame"]) != (
            0,
            0,
            len(rows),
        ):
            raise ValueError(
                f"timing keyframes must span frame boundaries 0 to {len(rows)}, starting at 0 ms"
            )
        boundaries = []
        for start, end in pairwise(keys):
            a, b = start["frame"], end["frame"]
            if (
                not isinstance(a, int)
                or not isinstance(b, int)
                or not 0 <= a < b <= len(rows)
            ):
                raise ValueError("timing keyframe boundaries must increase")
            if end["ms"] <= start["ms"]:
                raise ValueError("timing keyframe times must increase")
            boundaries.extend(
                start["ms"]
                + (end["ms"] - start["ms"])
                * ease((index - a) / (b - a), start.get("ease", "linear"))
                for index in range(a, b)
            )
        delays = [b - a for a, b in pairwise([*boundaries, keys[-1]["ms"]])]
    if len(delays) != len(rows) or any(
        type(ms) not in (int, float) or not math.isfinite(ms) or ms <= 0
        for ms in delays
    ):
        raise ValueError("provide one positive, finite duration per exposure")
    return delays


def read_timing(path, timing_path=None, frame_ms=100):
    if path.is_file() and path.suffix.lower() == ".json":
        data = read_json(path)
        rows = data["frames"]
        spec = data.get("timing")
    else:
        rows = cel_rows(path)
        spec = None
    if timing_path is not None:
        spec = read_json(timing_path)
    return rows, exposure_delays(rows, spec, frame_ms)


def add_timing_flags(parser):
    parser.add_argument(
        "--timing-spec", type=Path, help="duration array or easing/keyframe JSON"
    )
    parser.add_argument(
        "--frame-ms", type=float, default=100, help="default exposure for plain cels"
    )


def gif_delays(delays):
    # Allocate integer ticks around cumulative boundaries while reserving a readable hold per frame.
    total = max(2 * len(delays), round(sum(delays) / 10))
    previous = 0
    holds = []
    for index, boundary in enumerate(accumulate(delays), 1):
        limit = total - 2 * (len(delays) - index)
        tick = (
            total
            if index == len(delays)
            else max(previous + 2, min(limit, round(boundary / 10)))
        )
        holds.append((tick - previous) * 10)
        previous = tick
    if max(holds, default=0) > 655350:
        raise ValueError("a hold exceeds the GIF format's 655350 ms per exposure")
    return holds


def decode(path):
    with Image.open(path) as source:
        frames, delays = [], []
        for frame in ImageSequence.Iterator(source):
            frames.append(frame.convert("RGBA"))
            delays.append(frame.info.get("duration") or 100)
    return Timeline(frames, delays)


def preview_frame(timeline, starts, timestamp):
    return timeline.frames[bisect_right(starts, timestamp % timeline.duration) - 1]


def pack_entries(manifest):
    if manifest.is_file() and manifest.suffix.lower() == ".json":
        entries = read_json(manifest)["emojis"]
    else:
        entries = [
            {"name": plain_name(path), "file": str(path)}
            for path in image_files(manifest)
        ]
    if not entries:
        raise ValueError("pack needs at least one emoji")
    seen = set()
    for entry in entries:
        path = manifest.parent / entry["file"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", entry["name"]):
            raise ValueError(f"invalid emoji name: {entry['name']}")
        if entry["name"] in seen:
            raise ValueError(f"duplicate emoji name: {entry['name']}")
        if path.suffix.lower() not in RASTER:
            raise ValueError(f"unsupported raster format: {path.name}")
        seen.add(entry["name"])
        yield entry, path


def mask_geometry(mask):
    binary = mask.point(lambda value: 255 if value >= 128 else 0)
    bounds = binary.getbbox()
    if bounds is None:
        raise ValueError("body mask is empty")
    left, top, right, bottom = bounds
    cropped = binary.crop(bounds).tobytes()
    width = right - left
    xs, ys = 0, 0
    count = cropped.count(255)
    for index, pixel in enumerate(cropped):
        if pixel:
            y, x = divmod(index, width)
            xs += x
            ys += y
    return (left + xs / count, top + ys / count), (width, bottom - top)


def output_path(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
