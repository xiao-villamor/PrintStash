"""Render custom GLSL passes over timed cels with ModernGL or headless WebGL 2."""

import argparse
import base64
import json
import re
import struct
from io import BytesIO
from itertools import accumulate
from pathlib import Path

from PIL import Image

if __package__:
    from .common import add_timing_flags, cli, image, read_cel, read_timing
    from .shader_config import (
        CLOCK_DEFAULTS,
        SHADERS,
        color,
        parameter_images,
        resolve_effect,
        sample,
    )
else:
    from common import add_timing_flags, cli, image, read_cel, read_timing
    from shader_config import (
        CLOCK_DEFAULTS,
        SHADERS,
        color,
        parameter_images,
        resolve_effect,
        sample,
    )

VERTEX = """
out vec2 uv;
void main() {
    uv = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    gl_Position = vec4(uv * 2.0 - 1.0, 0.0, 1.0);
}
"""
HEADER = """
in vec2 uv;
out vec4 result;
uniform sampler2D source;
uniform sampler2D original;
uniform sampler2D glow;
uniform vec2 resolution;
uniform float time;
uniform float progress;
uniform vec2 anchor;
uniform float phase;
uniform float seconds;
uniform float intensity;
"""
# GL enum -> scalar family and vector width (or matrix dimensions).
UNIFORMS = {
    5126: ("f", 1),
    35664: ("f", 2),
    35665: ("f", 3),
    35666: ("f", 4),
    5124: ("i", 1),
    35667: ("i", 2),
    35668: ("i", 3),
    35669: ("i", 4),
    35670: ("i", 1),
    35671: ("i", 2),
    35672: ("i", 3),
    35673: ("i", 4),
    5125: ("I", 1),
    36294: ("I", 2),
    36295: ("I", 3),
    36296: ("I", 4),
    35674: ("m", [2, 2]),
    35675: ("m", [3, 3]),
    35676: ("m", [4, 4]),
    35685: ("m", [2, 3]),
    35686: ("m", [2, 4]),
    35687: ("m", [3, 2]),
    35688: ("m", [3, 4]),
    35689: ("m", [4, 2]),
    35690: ("m", [4, 3]),
}
SAMPLER_2D = 35678


class ContextUnavailable(RuntimeError):
    pass


def fragment_source(stage, prefix, library):
    if stage.body is not None:
        return f"{prefix}{HEADER}{library}\nvoid main() {{ {stage.body} }}"
    source = stage.path.read_text(encoding="utf-8")
    uncommented = re.sub(r"//[^\n]*|/\*.*?\*/", "", source, flags=re.DOTALL)
    if re.search(r"\bvoid\s+main\s*\(", uncommented):
        return prefix + re.sub(r"(?m)^\s*#version[^\n]*\n?", "", source, count=1)
    return f"{prefix}{HEADER}{library}\n#line 1\n{source}\nvoid main() {{ result = effect(uv, cel(uv)); }}"


def modern_context():
    try:
        import moderngl
    except ImportError as error:
        raise ContextUnavailable("install moderngl and glcontext") from error
    for options in ({}, {"backend": "egl"}):
        try:
            return moderngl.create_standalone_context(require=330, **options)
        except Exception as error:  # noqa: BLE001 - glcontext raises plain Exception on backend failure.
            last_error = error
    raise ContextUnavailable(str(last_error))


def components(value, width, matrix=False):
    if isinstance(value, str):
        if width not in (3, 4) or matrix:
            raise ValueError("use a vec3 or vec4 for a CSS colour")
        return color(value, width)
    if isinstance(value, dict) and "rgba" in value and width in (3, 4) and not matrix:
        return value["rgba"][:width]
    if (
        matrix
        and isinstance(value, list)
        and all(isinstance(col, list) for col in value)
    ):
        value = [item for col in value for item in col]
    if isinstance(value, list) and len(value) == width:
        return value
    if not isinstance(value, (list, dict)) and width == 1:
        return [value]
    raise ValueError(f"expected {width} component(s)")


def number(value, family):
    if family in {"i", "I"} and isinstance(value, (int, float)) and value == int(value):
        return int(value)
    if family in {"f", "m"} and type(value) in (int, float):
        return float(value)
    raise ValueError(
        f"expected {'an integer or boolean' if family in {'i', 'I'} else 'a number'}"
    )


def numeric_binding(value, gl_type, count):
    family, dimensions = UNIFORMS[gl_type]
    width = dimensions[0] * dimensions[1] if family == "m" else dimensions
    # Drivers may report only the prefix of a GLSL array reached by the shader.
    if (
        count == 1
        and isinstance(value, list)
        and value
        and family != "m"
        and (
            (width == 1 and len(value) != 1) or isinstance(value[0], (list, str, dict))
        )
    ):
        value = value[0]
    parts = value[:count] if count > 1 and isinstance(value, list) else [value]
    if len(parts) != count:
        raise ValueError(f"expected a GLSL array with {count} active entries")
    flat = [
        number(item, family)
        for part in parts
        for item in components(part, width, family == "m")
    ]
    return family, dimensions, flat


def bindings(stage, uniforms, params, ms, progress, frame, ready, texture_ref):
    values = {**params, **stage.params, **stage.inputs}
    for name, gl_type, count in uniforms:
        try:
            if name not in values and gl_type != SAMPLER_2D:
                continue
            value = sample(values.get(name), ms, progress, frame)
            if gl_type == SAMPLER_2D:
                value = (
                    {"pass": stage.source}
                    if name == "source" and value is None
                    else value
                )
                value = (
                    {"pass": "art"} if name == "original" and value is None else value
                )
                refs = value if isinstance(value, list) else [value]
                if len(refs) < count:
                    raise ValueError(f"expected {count} texture inputs")
                yield (
                    name,
                    "texture",
                    1,
                    [texture_ref(ref, ready) for ref in refs[:count]],
                )
            else:
                yield name, *numeric_binding(value, gl_type, count)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError(
                f"pass {stage.name!r}, uniform {name!r}: {error}"
            ) from error


def texture_source(ref, ready):
    if isinstance(ref, dict) and "image" in ref:
        return "image", ref["image"]
    if isinstance(ref, dict) and ref.get("pass") in ready:
        return "pass", ref["pass"]
    raise ValueError(
        "bind an image or an earlier pass using {'image': path} or {'pass': name}"
    )


def pixels(cel):
    return cel.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()


def render_modern(context, stages, frames, size, library):
    import moderngl

    def texture(dimensions, data=None, floating=False):
        tex = context.texture(dimensions, 4, data, dtype="f2" if floating else "f1")
        tex.repeat_x = tex.repeat_y = False
        tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return tex

    art = texture((size, size))
    previous = {"art": art}
    images = {}

    def get_texture(ref, ready):
        kind, name = texture_source(ref, ready)
        if kind == "pass":
            return previous[name]
        if name not in images:
            frame = image(Path(name))
            images[name] = texture(frame.size, pixels(frame))
        return images[name]

    passes = []
    for index, stage in enumerate(stages):
        try:
            program = context.program(
                vertex_shader="#version 330\n" + VERTEX,
                fragment_shader=fragment_source(stage, "#version 330\n", library),
            )
        except moderngl.Error as error:
            raise ValueError(
                f"could not compile pass {stage.name!r} ({stage.path}): {error}"
            ) from error
        uniforms = [
            (name, member.gl_type, member.array_length)
            for name in program
            if isinstance(member := program[name], moderngl.Uniform)
        ]
        target = texture((size, size), floating=index < len(stages) - 1)
        passes.append(
            (
                stage,
                context.vertex_array(program, []),
                program,
                uniforms,
                context.framebuffer([target]),
            )
        )
        previous[stage.name] = target

    for index, (rgba, params, ms, progress) in enumerate(frames):
        art.write(rgba)
        ready = {"art"}
        for stage, array, program, uniforms, target in passes:
            unit = 0
            for name, family, _, values in bindings(
                stage, uniforms, params, ms, progress, index, ready, get_texture
            ):
                if family == "texture":
                    for tex in values:
                        tex.use(unit)
                        unit += 1
                    values = list(range(unit - len(values), unit))
                    family = "i"
                if family == "m":
                    family = "f"
                try:
                    program[name].write(struct.pack(f"={len(values)}{family}", *values))
                except struct.error as error:
                    raise ValueError(
                        f"pass {stage.name!r}, uniform {name!r}: {error}"
                    ) from error
            target.use()
            array.render(mode=moderngl.TRIANGLES, vertices=3)
            ready.add(stage.name)
        raw = passes[-1][-1].read(components=4, alignment=1)
        yield Image.frombytes("RGBA", (size, size), raw).transpose(
            Image.Transpose.FLIP_TOP_BOTTOM
        )


def as_base64(data):
    return base64.b64encode(data).decode("ascii")


def render_webgl(stages, frames, size, library, channel):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as error:
        raise ValueError(
            "WebGL fallback needs playwright; see references/shaders.md"
        ) from error
    prefix = "#version 300 es\nprecision highp float;\nprecision highp int;\n"
    js = Path(__file__).with_name("webgl.js").read_text(encoding="utf-8")
    loaded = set()
    try:
        with (
            sync_playwright() as playwright,
            playwright.chromium.launch(
                args=["--enable-unsafe-swiftshader"],
                **({"channel": channel} if channel else {}),
            ) as browser,
        ):
            page = browser.new_page()
            uniforms = page.evaluate(
                js,
                {
                    "vertex": prefix + VERTEX,
                    "size": size,
                    "stages": [
                        [stage.name, fragment_source(stage, prefix, library)]
                        for stage in stages
                    ],
                },
            )

            def get_texture(ref, ready):
                kind, name = texture_source(ref, ready)
                if kind == "image" and name not in loaded:
                    frame = image(Path(name))
                    page.evaluate(
                        "input => window.loadTexture(input)",
                        [name, *frame.size, as_base64(pixels(frame))],
                    )
                    loaded.add(name)
                return [kind, name]

            for index, (rgba, params, ms, progress) in enumerate(frames):
                ready = {"art"}
                bound = []
                for stage, members in zip(stages, uniforms, strict=True):
                    bound.append(
                        list(
                            bindings(
                                stage,
                                members,
                                params,
                                ms,
                                progress,
                                index,
                                ready,
                                get_texture,
                            )
                        )
                    )
                    ready.add(stage.name)
                result = page.evaluate(
                    "args => window.renderCel(args)", [as_base64(rgba), bound]
                )
                with Image.open(BytesIO(base64.b64decode(result))) as rendered:
                    yield rendered.convert("RGBA")
    except Error as error:
        raise ValueError(
            f"WebGL render failed: {str(error).split('Call log:')[0].split('at eval')[0].strip()}"
        ) from error


def configure(parser):
    parser.add_argument(
        "timing", type=Path, help="timing JSON, image, or folder of cels"
    )
    add_timing_flags(parser)
    parser.add_argument("effect", type=Path, help=".frag file or JSON pass chain")
    parser.add_argument("output", type=Path, help="new directory")
    parser.add_argument("--effect-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--params", type=Path, help="optional JSON uniform values")
    parser.add_argument("--size", type=int)
    parser.add_argument(
        "--draft", action="store_true", help="render at 128px unless --size is set"
    )
    parser.add_argument(
        "--backend", choices=["auto", "moderngl", "webgl"], default="auto"
    )
    parser.add_argument("--channel", help="browser channel for WebGL, e.g. chrome")


def render(args):
    if args.size is None:
        args.size = 128 if args.draft else 512
    if args.size < 1:
        raise ValueError("size must be positive")
    rows, delays = read_timing(args.timing, args.timing_spec, args.frame_ms)
    if args.effect_root is None:
        stages, params, _ = resolve_effect(str(args.effect), Path.cwd())
    else:
        effect = json.loads(args.effect.read_text(encoding="utf-8"))
        stages, params, _ = resolve_effect(effect, args.effect_root)
    if args.params:
        extra = json.loads(args.params.read_text(encoding="utf-8"))
        params.update(parameter_images(extra, args.params.parent))
    library = (SHADERS / "library.glsl").read_text(encoding="utf-8")

    def inputs():
        duration = sum(delays)
        starts = accumulate(delays, initial=0)
        for index, (row, start) in enumerate(zip(rows, list(starts)[:-1], strict=True)):
            cel = read_cel(args.timing.parent, row)
            cel = cel.resize((args.size, args.size), Image.Resampling.LANCZOS)
            progress = start / duration
            defaults = {
                **CLOCK_DEFAULTS,
                **params,
                **{key: row[key] for key in CLOCK_DEFAULTS if key in row},
            }
            defaults.update(parameter_images(row.get("params", {}), args.timing.parent))
            speed = sample(defaults["speed"], start, progress, index)
            clock = {
                "time": start / 1000,
                "progress": progress,
                "phase": progress,
                "seconds": start * speed / 1000,
                "resolution": [args.size, args.size],
            }
            yield pixels(cel), {**defaults, **clock}, start, progress

    context = None
    if args.backend != "webgl":
        try:
            context = modern_context()
        except ContextUnavailable as error:
            if args.backend == "moderngl":
                raise
            print(f"ModernGL context unavailable ({error}); trying headless WebGL")

    output = (
        render_modern(context, stages, inputs(), args.size, library)
        if context
        else render_webgl(stages, inputs(), args.size, library, args.channel)
    )
    results = []
    try:
        args.output.mkdir(parents=True, exist_ok=False)
        for index, (frame, ms) in enumerate(zip(output, delays, strict=True), 1):
            name = f"{index:03d}.png"
            frame.save(args.output / name)
            results.append({"file": name, "ms": ms})
        (args.output / "timing.json").write_text(
            json.dumps({"frames": results}, indent=2) + "\n"
        )
        print(
            f"{len(results)} frames rendered with {'ModernGL' if context else 'WebGL 2'}"
        )
    finally:
        output.close()
        if context:
            context.release()


if __name__ == "__main__":
    cli(__doc__, configure, render)
