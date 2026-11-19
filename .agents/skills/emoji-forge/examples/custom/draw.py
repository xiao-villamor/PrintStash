"""Draw a lantern and a moving filament as independent cels."""

from math import pi, sin

from PIL import Image, ImageDraw


def animate(frames, context):
    size = context["size"]
    radiance = context["params"].get("line_color", "#fff5c4")
    result = []
    for index in range(12):
        t = index / 11
        cel = Image.new("RGBA", (size, size))
        pen = ImageDraw.Draw(cel)

        def scale(box):
            return tuple(round(value * size) for value in box)

        pen.arc(
            scale((0.40, 0.15, 0.60, 0.39)),
            180,
            359,
            fill="#40305b",
            width=round(size * 0.045),
        )
        pen.rounded_rectangle(
            scale((0.29, 0.27, 0.71, 0.78)),
            round(size * 0.11),
            fill="#fff1cf",
            outline="#ede6ff",
            width=round(size * 0.025),
        )
        pen.rounded_rectangle(
            scale((0.31, 0.29, 0.69, 0.76)),
            round(size * 0.09),
            fill="#dd714b",
            outline="#40305b",
            width=round(size * 0.028),
        )
        pen.rounded_rectangle(
            scale((0.375, 0.38, 0.625, 0.68)),
            round(size * 0.05),
            fill="#572848",
            outline="#442b55",
            width=round(size * 0.018),
        )
        middle = 0.5 + 0.055 * sin(t * 2 * pi)
        pen.line(
            scale((0.43, 0.61, middle, 0.48 - 0.04 * sin(pi * t), 0.57, 0.61)),
            fill=radiance,
            width=round(size * 0.018),
            joint="curve",
        )
        result.append({"image": cel, "params": {"wick": [middle, 0.48]}})
    return result
