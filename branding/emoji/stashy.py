"""Draw Stashy cels for Emoji Forge on a supersampled 128-unit grid."""

from math import cos, pi, sin

from PIL import Image, ImageDraw

SCALE = 4
INK = "#102552"
BLUE = "#2767FF"
DEEP_BLUE = "#0E48F0"
ICE = "#A9CDFF"
WHITE = "#F5FAFF"
MINT = "#7CF5CF"
GOLD = "#FFBB57"
PINK = "#FF709A"


def pixels(coordinates):
    return tuple(round(value * SCALE) for value in coordinates)


class Cel:
    def __init__(self):
        self.image = Image.new("RGBA", (128 * SCALE, 128 * SCALE))
        self.pen = ImageDraw.Draw(self.image)

    def box(self, bounds, radius, fill, outline=None, width=3):
        self.pen.rounded_rectangle(
            pixels(bounds), radius * SCALE, fill, outline, width * SCALE
        )

    def oval(self, bounds, fill, outline=None, width=3):
        self.pen.ellipse(pixels(bounds), fill, outline, width * SCALE)

    def shape(self, points, fill):
        self.pen.polygon(pixels(points), fill)

    def line(self, points, fill, width):
        scaled = pixels(points)
        self.pen.line(scaled, fill, width * SCALE, joint="curve")
        for x, y in (scaled[:2], scaled[-2:]):
            radius = width * SCALE / 2
            self.pen.ellipse((x - radius, y - radius, x + radius, y + radius), fill)

    def arm(self, points):
        self.line(points, INK, 10)
        self.line(points, ICE, 4)
        x, y = points[-2:]
        self.box((x - 8, y - 8, x + 8, y + 8), 6, WHITE, INK)
        self.line((x, y + 1, x + 4, y - 2), ICE, 2)

    def platform(self):
        self.box((35, 102, 90, 120), 7, INK)
        self.box((30, 101, 95, 113), 6, BLUE, INK)
        self.line((37, 104, 88, 104), ICE, 2)
        for x in (45, 79):
            self.line((x, 115, x + 3, 115), ICE, 2)

    def housing(self, expression):
        # The white P retains the icon's upright stem and rounded counter.
        self.box((34, 25, 62, 85), 10, INK)
        self.box((34, 16, 94, 69), 22, INK)
        self.box((39, 27, 57, 80), 6, ICE)
        self.box((39, 20, 89, 62), 17, ICE)
        self.box((39, 21, 58, 72), 7, WHITE)
        self.box((40, 20, 89, 58), 16, WHITE)
        self.box((49, 27, 84, 54), 11, INK)
        self.box((52, 30, 81, 51), 8, DEEP_BLUE)
        self.line((59, 33, 73, 33), BLUE, 2)
        if expression == "happy":
            for x in (60, 73):
                self.line((x - 3, 42, x, 38, x + 3, 42), MINT, 3)
            self.line((64, 45, 67, 47, 70, 45), WHITE, 2)
        elif expression == "panic":
            for x in (59, 74):
                self.oval((x - 3, 36, x + 3, 42), GOLD)
            self.oval((64, 43, 69, 48), WHITE)
        elif expression == "love":
            for x in (60, 73):
                self.heart(x, 39, 4, PINK)
            self.line((64, 45, 67, 47, 70, 45), WHITE, 2)
        elif expression == "focus":
            self.line((56, 39, 62, 40), MINT, 3)
            self.line((70, 40, 77, 39), MINT, 3)
            self.line((65, 46, 70, 46), WHITE, 2)
        else:
            for x in (62, 75):
                self.oval((x - 2, 37, x + 2, 43), MINT)
            self.line((63, 46, 68, 47), WHITE, 2)

    def nozzle(self, x):
        self.box((39, 66, x + 7, 77), 5, ICE, INK)
        self.box((x - 12, 72, x + 12, 85), 5, BLUE, INK)
        self.line((x - 7, 76, x + 7, 76), ICE, 2)
        self.shape((x - 9, 83, x + 9, 83, x + 2, 92, x - 2, 92), INK)
        self.shape((x - 4, 85, x + 4, 85, x, 89), GOLD)

    def heart(self, x, y, r, color):
        self.oval((x - r, y - r, x, y + 1), color)
        self.oval((x, y - r, x + r, y + 1), color)
        self.shape((x - r, y - 1, x, y + r, x + r, y - 1), color)

    def sparkle(self, x, y, radius, color):
        slim = radius * 0.35
        self.shape(
            (
                x,
                y - radius,
                x + slim,
                y - slim,
                x + radius,
                y,
                x + slim,
                y + slim,
                x,
                y + radius,
                x - slim,
                y + slim,
                x - radius,
                y,
                x - slim,
                y - slim,
            ),
            color,
        )

    def badge(self, x, y, fill):
        self.oval((x - 16, y - 16, x + 16, y + 16), fill, INK, 3)
        self.pen.arc(
            pixels((x - 12, y - 12, x + 12, y + 12)), 190, 265, WHITE, 2 * SCALE
        )

    def cube(self, x, y):
        self.shape(
            (
                x,
                y - 15,
                x + 14,
                y - 8,
                x + 14,
                y + 5,
                x,
                y + 12,
                x - 14,
                y + 5,
                x - 14,
                y - 8,
            ),
            INK,
        )
        self.shape((x, y - 12, x + 10, y - 7, x, y - 2, x - 10, y - 7), WHITE)
        self.shape((x - 11, y - 4, x - 1, y + 1, x - 1, y + 8, x - 11, y + 3), MINT)
        self.shape(
            (x + 11, y - 4, x + 1, y + 1, x + 1, y + 8, x + 11, y + 3), "#23BCAD"
        )


def mascot(expression, nozzle_x=51, left_hand=(25, 82), right_hand=(89, 83)):
    cel = Cel()
    cel.arm((43, 64, 32, 69, *left_hand))
    cel.arm((57, 67, 72, 70, *right_hand))
    cel.platform()
    cel.housing(expression)
    cel.nozzle(nozzle_x)
    return cel


def wave(progress):
    sway = sin(2 * pi * progress)
    x, y = 104 + 6 * sway, 61 + 4 * sway
    cel = mascot("happy", right_hand=(x, y))
    cel.line((110, 46, 116, 42), INK, 5)
    cel.line((110, 46, 116, 42), MINT, 2)
    cel.line((117, 53, 122, 52), INK, 5)
    cel.line((117, 53, 122, 52), MINT, 2)
    return cel


def love(_progress):
    cel = mascot("love", right_hand=(87, 88))
    cel.heart(101, 24, 20, INK)
    cel.heart(101, 24, 15, PINK)
    cel.oval((91, 13, 96, 18), WHITE)
    return cel


def thinking(_progress):
    cel = mascot("curious", right_hand=(64, 69))
    cel.badge(103, 23, GOLD)
    cel.line((97, 20, 98, 17, 103, 15, 108, 17, 108, 20, 103, 24), INK, 4)
    cel.oval((101, 29, 105, 33), INK)
    return cel


def approved(_progress):
    cel = mascot("happy", right_hand=(95, 78))
    cel.badge(104, 28, MINT)
    cel.line((96, 29, 102, 35, 112, 22), INK, 5)
    return cel


def printing(progress):
    x = 51 + 23 * (1 - cos(2 * pi * progress)) / 2
    cel = mascot("focus", nozzle_x=x, right_hand=(100, 82))
    cel.box((47, 94, 80, 103), 4, GOLD, INK, width=2)
    cel.line((x, 90, x, 96), GOLD, 3)
    cel.line((51, 98, x, 98), WHITE, 2)
    cel.sparkle(104, 56, 7, MINT)
    return cel


def done(progress):
    lift = (1 - cos(2 * pi * progress)) / 2
    cel = mascot("happy", right_hand=(98, 52 - 8 * lift))
    cel.cube(72, 88 - 3 * lift)
    cel.sparkle(107, 22, 11 - 3 * lift, GOLD)
    cel.sparkle(22, 40, 8 + 3 * lift, MINT)
    return cel


def spaghetti(progress):
    wiggle = 5 * sin(2 * pi * progress)
    cel = mascot("panic", left_hand=(22, 54), right_hand=(101, 61))
    for bounds in (
        (44, 94, 78, 105),
        (68, 89, 96 + wiggle, 103),
        (61, 80, 84, 99),
    ):
        cel.oval(bounds, INK)
        cel.pen.arc(pixels(bounds), 20, 325, GOLD, 3 * SCALE)
    cel.line(
        (
            51,
            90,
            56,
            98,
            66,
            99,
            76,
            91,
            85 + wiggle,
            92,
            88 + wiggle,
            98,
            99,
            100,
            107,
            94,
        ),
        INK,
        7,
    )
    cel.line(
        (
            51,
            90,
            56,
            98,
            66,
            99,
            76,
            91,
            85 + wiggle,
            92,
            88 + wiggle,
            98,
            99,
            100,
            107,
            94,
        ),
        GOLD,
        3,
    )
    cel.line((101, 26, 105, 36), INK, 6)
    cel.line((101, 26, 105, 36), GOLD, 3)
    cel.oval((105, 42, 110, 47), GOLD, INK, 1)
    return cel


REACTIONS = {
    "wave": (wave, (280, 90, 90, 100, 160, 100, 90, 90, 120, 100, 90, 90)),
    "love": (love, (100,)),
    "thinking": (thinking, (100,)),
    "approved": (approved, (100,)),
    "printing": (printing, (110,) * 16),
    "done": (done, (280, 100, 100, 110, 120, 140, 120, 110, 100, 100)),
    "spaghetti": (spaghetti, (140, 90, 90, 90, 90, 90, 90, 90, 90, 90, 90, 90)),
}


def animate(_frames, context):
    draw, holds = REACTIONS[context["name"].removeprefix("stashy-")]
    return [
        {"image": draw(index / len(holds)).image, "ms": hold}
        for index, hold in enumerate(holds)
    ]


def badge_cel(frames, _context):
    badge = frames[0]["image"].resize((112, 112), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (128, 128))
    canvas.alpha_composite(badge, (8, 8))
    return [{"image": canvas}]
