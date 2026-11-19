"""Resolve shader stages, texture paths, and animated parameter values."""

import re
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from PIL import ImageColor

if __package__:
    from .common import ease, read_json
else:
    from common import ease, read_json

SCRIPTS = Path(__file__).resolve().parent
SHADERS = SCRIPTS / "shaders"
if not SHADERS.is_dir():
    SHADERS = SCRIPTS.parent / "shaders"

BLOOM = (
    ("bright", "vec4 c = texture(source, uv); result = vec4(brightPass(c), 1.0);"),
    ("blur-x", "result = blurPass(source, uv, vec2(1.0, 0.0) / resolution);"),
    ("blur-y", "result = blurPass(source, uv, vec2(0.0, 1.0) / resolution);"),
    (
        "resolve",
        """vec4 c = texture(source, uv);
       vec3 light = texture(glow, uv).rgb * 0.40;
       vec4 base = over(c, vec4(0.68, 0.83, 1.0, clamp(length(light), 0.0, 0.45)));
       result = vec4(clamp(base.rgb + light, 0.0, 1.0), base.a * edgeFade(uv));""",
    ),
)

CLOCK_DEFAULTS = {"anchor": [0.5, 0.5], "speed": 1, "intensity": 1}


@dataclass
class Stage:
    name: str
    source: str
    path: Path | None = None
    body: str | None = None
    params: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)


def absolute_images(value, root):
    if isinstance(value, dict):
        if set(value) == {"image"} and isinstance(value["image"], str):
            path = (root / value["image"]).resolve()
            if not path.is_file():
                raise ValueError(f"texture file does not exist: {path}")
            return {"image": str(path)}
        return {key: absolute_images(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [absolute_images(item, root) for item in value]
    return value


def parameter_images(params, root):
    return {name: absolute_images(value, root) for name, value in params.items()}


def image_paths(value):
    if isinstance(value, dict):
        if set(value) == {"image"} and isinstance(value["image"], str):
            yield Path(value["image"])
        else:
            for item in value.values():
                yield from image_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from image_paths(item)


def shader_path(root, name):
    path = (root / name).resolve()
    if not path.is_file() and re.fullmatch(r"[a-z0-9-]+", name):
        path = SHADERS / (name + ".frag")
    if path.suffix.lower() != ".frag" or not path.is_file():
        raise ValueError(f"fragment shader does not exist: {name}")
    return path


def resolve_effect(raw, root):
    if isinstance(raw, str):
        config = root / raw
        if config.suffix.lower() == ".json":
            stages, params, dependencies = resolve_effect(
                read_json(config), config.parent
            )
            return stages, params, dependencies | {config.resolve()}
        raw = {"shader": raw}
    if "passes" in raw:
        passes = raw["passes"]
    else:
        passes = [raw["shader"]]
        # Preserve the original single-effect preset. Explicit chains control every stage.
        if raw.get("bloom", True):
            passes.append("bloom")
    if not passes:
        raise ValueError("provide at least one shader pass")
    params = parameter_images(raw.get("params", {}), root)
    dependencies = {path for value in params.values() for path in image_paths(value)}
    stages = []
    previous = "art"
    for index, stage in enumerate(passes, 1):
        stage = {"shader": stage} if isinstance(stage, str) else stage
        name = stage.get(
            "name", "bloom" if stage["shader"] == "bloom" else f"pass{index}"
        )
        names = {"art", *(item.name for item in stages)}
        if name in names or name.startswith("@"):
            raise ValueError(f"duplicate or reserved shader pass name: {name!r}")
        if stage["shader"] == "bloom":
            base = previous
            glow = f"@{index}-blur-y"
            for label, body in BLOOM:
                output = name if label == "resolve" else f"@{index}-{label}"
                inputs = {"glow": {"pass": glow}} if label == "resolve" else {}
                stages.append(
                    Stage(
                        output, base if inputs else previous, body=body, inputs=inputs
                    )
                )
                previous = output
            continue
        path = shader_path(root, stage["shader"])
        bindings = parameter_images(stage.get("params", {}), root)
        inputs = {}
        for uniform, value in stage.get("inputs", {}).items():
            if isinstance(value, str):
                value = {"pass": value} if value in names else {"image": value}
            inputs[uniform] = absolute_images(value, root)
        dependencies.add(path)
        for value in (*bindings.values(), *inputs.values()):
            dependencies.update(image_paths(value))
        stages.append(Stage(name, previous, path, params=bindings, inputs=inputs))
        previous = name
    return stages, params, dependencies


def color(value, width=4):
    channels = ImageColor.getcolor(value, "RGBA")
    return [channel / 255 for channel in channels[:width]]


def interpolate(first, second, amount):
    if isinstance(first, str) and isinstance(second, str):
        return {"rgba": interpolate(color(first), color(second), amount)}
    if isinstance(first, list) and isinstance(second, list):
        return [interpolate(a, b, amount) for a, b in zip(first, second, strict=True)]
    if type(first) in (int, float) and type(second) in (int, float):
        return first + (second - first) * amount
    raise ValueError("curves with booleans or textures need hold interpolation")


def sample(value, ms, progress, frame=0):
    if not isinstance(value, dict) or "keyframes" not in value:
        return value
    keys = value["keyframes"]
    axes = {"at": progress, "ms": ms, "frame": frame}
    axis = next((axis for axis in axes if keys and axis in keys[0]), "at")
    now = axes[axis]
    marks = [key[axis] for key in keys]
    if not marks or any(a >= b for a, b in pairwise(marks)):
        raise ValueError(
            "uniform curve keys must increase on one axis: at, ms or frame"
        )
    if now <= marks[0]:
        return keys[0]["value"]
    for first, second in pairwise(keys):
        if now < second[axis]:
            method = first.get("ease", "linear")
            if method == "hold":
                return first["value"]
            amount = (now - first[axis]) / (second[axis] - first[axis])
            return interpolate(first["value"], second["value"], ease(amount, method))
    return keys[-1]["value"]
