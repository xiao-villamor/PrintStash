"""Draw an original set of small fixture cels, then run the public pipeline."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SCALE = 4
INK = "#172c3a"
FILL = "#7de1bf"
SHADE = "#4ab69b"
CREAM = "#f9ffed"


def bounds(coordinates):
    return tuple(round(value * SCALE) for value in coordinates)


def draw_cel(expression, action, amount):
    art = Image.new("RGBA", (128 * SCALE, 128 * SCALE))
    draw = ImageDraw.Draw(art)
    body = Image.new("L", art.size)
    face = Image.new("L", art.size)
    face_draw = ImageDraw.Draw(face)

    def line(points, fill=INK, width=3, pen=draw):
        pen.line(bounds(points), fill=fill, width=width * SCALE, joint="curve")

    def oval(box, fill, outline=None, width=2, pen=draw):
        pen.ellipse(bounds(box), fill=fill, outline=outline, width=width * SCALE)

    def round_box(box, radius, fill, outline=None, width=2, pen=draw):
        pen.rounded_rectangle(bounds(box), radius * SCALE, fill, outline, width * SCALE)

    def limb(points):
        line(points, CREAM, 5)
        line(points)

    # Each exposure is a full drawing. Body geometry is fixed by the style sheet.
    round_box((42, 90, 57, 103), 6, SHADE, INK)
    round_box((71, 90, 86, 103), 6, SHADE, INK)
    limb((62, 38, 62, 24, 74, 17))
    oval((68, 14, 84, 23), SHADE, INK)
    oval((52, 16, 63, 25), FILL, INK)
    round_box((29, 31, 99, 96), 25, CREAM, width=3)
    round_box((32, 34, 96, 93), 23, FILL, INK, 3)
    round_box((32, 34, 96, 93), 23, 255, pen=ImageDraw.Draw(body))
    oval((44, 46, 51, 49), "#b5f5d9")
    oval((54, 43, 58, 46), "#b5f5d9")
    oval((44, 65, 51, 69), "#f69b93")
    oval((77, 65, 84, 69), "#f69b93")

    if expression in {"happy", "blink"}:
        for x in (53, 75):
            points = (x - 4, 61, x, 57 if expression == "happy" else 61, x + 4, 61)
            line(points)
            line(points, 255, pen=face_draw)
    else:
        for x in (53, 75):
            box = (x - 2, 56, x + 2, 62)
            oval(box, INK)
            oval(box, 255, pen=face_draw)

    if expression == "happy":
        round_box((55, 66, 73, 79), 6, INK)
        round_box((55, 66, 73, 79), 6, 255, pen=face_draw)
        oval((59, 72, 69, 77), "#fa999e")
    elif expression == "sip":
        oval((61, 66, 67, 72), INK)
        oval((61, 66, 67, 72), 255, pen=face_draw)
    else:
        points = (58, 69, 62, 72, 66, 72, 70, 69)
        line(points, width=2)
        line(points, 255, width=2, pen=face_draw)

    if action == "wave":
        x = 106 + 5 * amount
        y = 80 - 32 * min(1, abs(amount)) - 9 * amount
        limb((92, 70, 101, 76 - 16 * abs(amount), x, y))
        oval((x - 5, y - 7, x + 5, y + 5), CREAM, INK)
        limb((34, 71, 26, 80))
        oval((22, 76, 30, 84), CREAM, INK)
    elif action == "coffee":
        y = 93 - amount * 13
        limb((36, 74, 43, 84 - 8 * amount, 54, y - 2))
        limb((92, 74, 84, 84 - 8 * amount, 75, y - 2))
        oval((72, y - 5, 81, y + 5), SHADE, INK)
        round_box((53, y - 8, 76, y + 10), 4, "#faac7e", INK)
        line((56, y - 5, 73, y - 5), "#ffe2b3", 2)
        oval((49, y - 6, 57, y + 2), CREAM, INK)
        oval((74, y - 6, 82, y + 2), CREAM, INK)
    else:
        y = 80 - amount * 38
        for x, direction in ((25, 1), (103, -1)):
            limb((x + direction * 9, 71, x + direction * 4, 75 - amount * 19, x, y))
            oval((x - 5, y - 5, x + 5, y + 5), CREAM, INK)
    return art, body, face


ANIMATIONS = {
    "wave": ("Say hello or goodbye.", [
        (0, "rest", 330), (.25, "rest", 200), (.55, "rest", 180), (.87, "happy", 350),
        (-.79, "happy", 260), (.96, "happy", 290), (-.66, "happy", 270), (.78, "happy", 380),
        (.44, "happy", 320), (.18, "rest", 260), (0, "blink", 180), (0, "rest", 180),
    ]),
    "coffee": ("Taking a coffee break.", [
        (0, "rest", 410), (.18, "rest", 240), (.44, "rest", 210), (.72, "sip", 220),
        (.91, "sip", 260), (1, "sip", 530), (.93, "blink", 250), (.69, "happy", 300),
        (.43, "happy", 260), (.18, "rest", 270), (0, "blink", 170), (0, "rest", 180),
    ]),
    "hooray": ("Celebrate a small win.", [
        (0, "rest", 340), (.16, "rest", 280), (.32, "rest", 200), (.58, "happy", 180),
        (.89, "happy", 200), (1, "happy", 250), (.95, "happy", 290), (1, "happy", 310),
        (.91, "happy", 350), (.64, "happy", 220), (.36, "happy", 260), (.12, "rest", 230),
        (0, "blink", 180), (0, "rest", 180),
    ]),
}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def draw_animation(root, name, poses):
    root.mkdir()
    rows = []
    for index, (amount, expression, ms) in enumerate(poses, 1):
        row = {"ms": ms}
        for field, layer in zip(
            ("file", "body", "face"), draw_cel(expression, name, amount), strict=True
        ):
            filename = f"{index:02d}-{field}.png"
            layer.save(root / filename)
            row[field] = filename
        if name == "hooray":
            row["anchor"] = [0.50, 0.76]
        rows.append(row)
    return rows


def build(output, work, args):
    entries = []
    for name, (use, poses) in ANIMATIONS.items():
        rows = draw_animation(work / name, name, poses)
        for row in rows:
            for field in ("file", "body", "face"):
                row[field] = f"{name}/{row[field]}"
        entry = {"name": name, "use": use, "frames": rows}
        if name == "hooray":
            entry["effect"] = {"shader": "sparkle", "params": {"anchor": [.5, .76]}}
        entries.append(entry)

    still, _, _ = draw_cel("rest", "hello", 0)
    still.save(work / "hello.png")
    entries.append({"name": "hello", "use": "A quiet greeting.", "static": True,
                    "frames": [{"file": "hello.png"}]})
    manifest = work / "pack.json"
    write_json(manifest, {"target": "slack", "preview_ms": 1550, "emojis": entries})
    channel = ["--channel", args.channel] if args.channel else []
    subprocess.run(
        [sys.executable, ROOT / "scripts" / "forge.py", "all", manifest, output,
         "--backend", args.backend, *channel, "--json"],
        check=True,
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--backend", default="auto", choices=["auto", "moderngl", "webgl"])
    parser.add_argument("--channel")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="emoji-forge-") as scratch:
        build(args.output, Path(scratch), args)
