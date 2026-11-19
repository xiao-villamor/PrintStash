"""Run a trusted local animation function on a sequence of Pillow cels."""

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from itertools import accumulate
from pathlib import Path

from PIL import Image

if __package__:
    from .common import cli, image, read_cel, read_json
else:
    from common import cli, image, read_cel, read_json

LAYERS = {"image": ("file", "RGBA"), "body": ("body", "L"), "face": ("face", "L")}


def hook_spec(raw, root):
    if isinstance(raw, str):
        path, _, function = raw.partition(":")
        raw = {"path": path, "function": function or "animate"}
    path = (root / raw["path"]).resolve()
    if path.suffix != ".py" or not path.is_file():
        raise ValueError(f"Python hook does not exist: {path}")
    mode = raw.get("mode", "emoji")
    if mode not in {"frame", "emoji"}:
        raise ValueError("script mode must be frame or emoji")
    return raw, path


def run_hook(raw, path, rows, output, context):
    output.mkdir()
    mode = raw.get("mode", "emoji")
    spec = spec_from_file_location("forge_animation_hook", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    frames = []
    for row in rows:
        frame = dict(row)
        for layer, (key, color) in LAYERS.items():
            if key in row:
                path_to_layer = Path(frame.pop(key))
                frame[layer] = (
                    read_cel(path_to_layer.parent, row)
                    if key == "file"
                    else image(path_to_layer, color)
                )
        frame.pop("source_frame", None)
        frames.append(frame)
    context = {**context, "params": raw.get("params", {}), "source_dir": path.parent}
    function = raw.get("function", "animate")
    try:
        spec.loader.exec_module(module)
        animate = getattr(module, function)
        if mode == "frame":
            starts = list(accumulate((frame["ms"] for frame in frames), initial=0))
            result = [
                animate(
                    frame,
                    {
                        **context,
                        "index": index,
                        "time": start / 1000,
                        "progress": start / starts[-1],
                    },
                )
                for index, (frame, start) in enumerate(
                    zip(frames, starts[:-1], strict=True)
                )
            ]
        else:
            result = animate(frames, context)
    except Exception as error:
        raise ValueError(
            f"animation hook {path.name}:{function} failed: {error}"
        ) from error
    if not isinstance(result, (list, tuple)) or not result:
        raise ValueError(
            f"{path.name}:{function} must produce a nonempty sequence of cels"
        )
    saved = []
    for index, frame in enumerate(result, 1):
        if not isinstance(frame, dict) or not isinstance(
            frame.get("image"), Image.Image
        ):
            raise TypeError(f"hook exposure {index} needs a Pillow image under 'image'")
        row = dict(frame)
        for layer, (key, color) in LAYERS.items():
            if layer in row:
                cel = row.pop(layer)
                if not isinstance(cel, Image.Image):
                    raise ValueError(
                        f"hook exposure {index} {layer!r} must be a Pillow image"
                    )
                target = output / f"{index:03d}-{key}.png"
                cel.convert(color).save(target)
                row[key] = str(target.resolve())
        saved.append(row)
    return saved


def configure(parser):
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)


def run(args):
    request = read_json(args.input)
    path = Path(request["path"])
    context = request["context"]
    context["project_dir"] = Path(context["project_dir"])
    sys.path.insert(0, str(path.parent))
    frames = run_hook(request["hook"], path, request["frames"], args.output, context)
    (args.output / "result.json").write_text(json.dumps(frames), encoding="utf-8")


if __name__ == "__main__":
    cli(__doc__, configure, run)
