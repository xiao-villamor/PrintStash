"""Create and review static and frame-by-frame animated emoji."""

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from shutil import copy2, copyfile, copytree
from tempfile import TemporaryDirectory

from PIL import Image
from PIL import __version__ as pillow_version

if __package__:
    from . import common
    from .animation_hook import hook_spec
    from .shader_config import SHADERS, image_paths, parameter_images, resolve_effect
else:
    import common
    from animation_hook import hook_spec
    from shader_config import SHADERS, image_paths, parameter_images, resolve_effect


SCRIPTS = Path(__file__).resolve().parent
SCRIPT_COMMANDS = {
    "register": "register_frames",
    "render": "render_shader",
    "assemble": "assemble_gif",
    "fit": "assemble_gif",
    "check": "check_pack",
    "strips": "make_strips",
    "preview": "build_preview",
    "bundle": "bundle",
}


@dataclass(frozen=True)
class Preset:
    size: int
    max_bytes: int
    max_frames: int

    def flags(self):
        return [
            "--size",
            self.size,
            "--max-bytes",
            self.max_bytes,
            "--max-frames",
            self.max_frames,
        ]


PRESETS = {"generic": Preset(256, 1_000_000, 120), "slack": Preset(128, 128_000, 50)}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def run_script(name, *args):
    run = subprocess.run(
        [sys.executable, SCRIPTS / (name + ".py"), *map(str, args)],
        check=False,
        capture_output=True,
        text=True,
    )
    if run.returncode:
        detail = run.stderr.strip() or run.stdout.strip()
        if not detail:
            detail = f"exit {run.returncode}"
        raise ValueError(f"{name}: {detail[-1600:]}")
    return run.stdout.strip()


def artifact(path):
    data = {"path": str(path), "bytes": path.stat().st_size}
    if path.suffix in {".gif", ".png", ".jpg", ".jpeg"}:
        with Image.open(path) as image:
            data.update(size=list(image.size), frames=getattr(image, "n_frames", 1))
    return data


def new_pack(args):
    args.path.mkdir(parents=True)
    (args.path / "cels").mkdir()
    (args.path / "style.md").write_text(
        "# Style notes\n\nWhat visual references or ideas would you like to try together? Add sketches, links, or notes in any format.\n"
    )
    (args.path / "storyboard.md").write_text(
        "# Storyboard\n\nWhat should someone feel when they use this emoji? Sketch an idea, a sequence, a single pose, or several options to try.\n"
    )
    files = [args.path / "style.md", args.path / "storyboard.md"]
    if args.no_manifest:
        return {"files": [artifact(path) for path in files]}
    manifest = write_json(
        args.path / "pack.json",
        {
            "target": args.preset,
            "emojis": [
                {
                    "name": "wave",
                    "use": "Say hello.",
                    "frames": [
                        {
                            "file": "cels/ready.png",
                            "body": "cels/ready-body.png",
                            "face": "cels/ready-face.png",
                            "ms": 400,
                        },
                        {
                            "file": "cels/action.png",
                            "body": "cels/action-body.png",
                            "face": "cels/action-face.png",
                            "ms": 600,
                        },
                    ],
                }
            ],
        },
    )
    return {"files": [artifact(path) for path in [manifest, *files]]}


def source_frames(entry, root):
    raw = entry.get("frames")
    if isinstance(raw, str):
        raw = common.cel_rows(root / raw)
    if raw is None and "script" in entry:
        count = (
            entry["script"].get("count", 0) if isinstance(entry["script"], dict) else 0
        )
        if type(count) is not int or count < 0:
            raise ValueError("script count must be a nonnegative integer")
        raw = [{} for _ in range(count)]
    if isinstance(raw, list):
        result = []
        for frame in raw:
            row = {"file": frame} if isinstance(frame, str) else dict(frame)
            for field in ("file", "body", "face"):
                if field in row:
                    path = (root / row[field]).resolve()
                    if not path.is_file():
                        raise ValueError(f"missing {field} image: {path}")
                    row[field] = str(path)
            if "params" in row:
                row["params"] = parameter_images(row["params"], root)
            result.append(row)
        if result:
            # For a generator with no input cels, apply the timing spec after it has created them.
            delays = common.exposure_delays(
                result, entry.get("timing"), entry.get("frame_ms", 100)
            )
            return [{**row, "ms": ms} for row, ms in zip(result, delays, strict=True)]
        if "script" in entry:
            return []
    raise ValueError("provide cels with frames or a Python script")


def cache_key(entry, frames, dependencies, preset, args):
    digest = sha256()
    config = {
        "entry": entry,
        "frames": frames,
        "preset": vars(preset),
        "draft": args.draft,
        "backend": args.backend,
        "channel": args.channel,
        "pillow": pillow_version,
    }
    digest.update(json.dumps(config, sort_keys=True).encode())
    files = set(SCRIPTS.glob("*.py")) | {SCRIPTS / "webgl.js", SHADERS / "library.glsl"}
    files.update(
        Path(row[field])
        for row in frames
        for field in ("file", "body", "face")
        if field in row
    )
    files.update(dependencies)
    for row in frames:
        for value in row.get("params", {}).values():
            files.update(image_paths(value))
    for path in sorted(files):
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def render_entry(entry, frames, effect, source, root, preset, args):
    name = entry["name"]
    if entry.get("static") and len(frames) != 1:
        raise ValueError("static output needs one cel")
    if entry.get("static") and not effect:
        with Image.open(frames[0]["file"]) as cel:
            png = cel.convert("RGBA").resize(
                (preset.size, preset.size), Image.Resampling.LANCZOS
            )
            if entry.get("matte"):
                background = Image.new("RGBA", png.size, entry["matte"])
                background.alpha_composite(png)
                png = background
            png.save(root / f"{name}.png", optimize=True)
        return
    timing = write_json(root / "input.json", {"frames": frames})
    registered = root / "registered"
    if entry.get("register", True) and not entry.get("static"):
        run_script(
            "register_frames",
            timing,
            registered,
            "--reference",
            entry.get("reference", 1),
        )
        final = registered / "timing.json"
    else:
        final = timing
    if effect:
        effect_dir = root / "effects"
        size = max(128, preset.size) if args.draft else max(512, preset.size)
        channel = ["--channel", args.channel] if args.channel else []
        config = write_json(root / "effect.json", effect)
        run_script(
            "render_shader",
            final,
            config,
            effect_dir,
            "--effect-root",
            source.resolve(),
            "--size",
            size,
            "--backend",
            args.backend,
            *channel,
        )
        final = effect_dir / "timing.json"
    if entry.get("static"):
        frame = common.image(final.parent / common.read_timing(final)[0][0]["file"])
        png = frame.resize((preset.size, preset.size), Image.Resampling.LANCZOS)
        if entry.get("matte"):
            background = Image.new("RGBA", png.size, entry["matte"])
            background.alpha_composite(png)
            png = background
        png.save(root / f"{name}.png", optimize=True)
        timing.unlink()
        return
    options = ["--matte", entry["matte"]] if "matte" in entry else []
    if "loop" in entry:
        options += ["--loop", entry["loop"]]
    run_script("assemble_gif", final, root / f"{name}.gif", *preset.flags(), *options)
    timing.unlink()


def cached_entry(entry, root, cache, preset, args):
    name = entry["name"]
    try:
        encoded = (root / entry["encoded"]).resolve() if "encoded" in entry else None
        script = hook_spec(entry["script"], root) if "script" in entry else None
        if script and not (
            args.allow_scripts or args.script or args.manifest.suffix == ".py"
        ):
            raise ValueError(
                "run with --allow-scripts to execute trusted hooks from the manifest"
            )
        frames = [] if encoded else source_frames(entry, root)
        effect = entry.get("effect")
        dependencies = resolve_effect(effect, root)[2] if effect else set()
        if encoded:
            dependencies.add(encoded)
        if script:
            rules, path = script
            dependencies.update(
                {path, *((root / file).resolve() for file in rules.get("deps", []))}
            )
        key = cache_key(entry, frames, dependencies, preset, args)
        target = cache / key
        if target.is_dir():
            return target, True
        with TemporaryDirectory(prefix=".cel-", dir=cache) as tmp:
            staging = Path(tmp) / "entry"
            staging.mkdir()
            if encoded:
                copyfile(
                    encoded,
                    staging / f"{name}{'.png' if entry.get('static') else '.gif'}",
                )
            else:
                if script:
                    frames = script_frames(
                        entry, script, frames, root, staging, preset, args
                    )
                render_entry(entry, frames, effect, root, staging, preset, args)
            try:
                staging.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
        return target, False
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"{name}: {error}") from error


def script_frames(entry, script, frames, root, staging, preset, args):
    hook, path = script
    request = write_json(
        staging / "hook-input.json",
        {
            "hook": hook,
            "path": str(path),
            "frames": frames,
            "context": {
                "name": entry["name"],
                "size": max(128 if args.draft else 512, preset.size),
                "draft": args.draft,
                "project_dir": str(root.resolve()),
            },
        },
    )
    run_script("animation_hook", request, staging / "hook")
    frames = common.read_json(staging / "hook/result.json")
    request.unlink()
    spec = entry.get("timing") if all("ms" not in frame for frame in frames) else None
    delays = common.exposure_delays(frames, spec, entry.get("frame_ms", 100))
    for row, delay in zip(frames, delays, strict=True):
        row["ms"] = delay
        if "params" in row:
            row["params"] = parameter_images(row["params"], root)
    return frames


def source_manifest(args):
    path = args.manifest
    if path.is_file() and path.suffix.lower() == ".json":
        return common.read_json(path)
    timing = common.read_json(args.timing_spec) if args.timing_spec else None
    entry = {
        "name": common.plain_name(path),
        "timing": timing,
        "frame_ms": args.frame_ms,
        "register": not args.no_register,
    }
    script = None
    if args.script:
        spec, script_path = hook_spec(args.script, Path.cwd())
        script = {**spec, "path": str(script_path)}
    if path.is_file() and path.suffix == ".py":
        entry["script"] = script or str(path.resolve())
    else:
        pack = common.image_files(path)
        if (
            path.is_dir()
            and not (args.effect or script or timing)
            and (args.exports or any(file.suffix.lower() == ".gif" for file in pack))
        ):
            entries = [
                {
                    "name": common.plain_name(file),
                    "encoded": str(file),
                    "static": file.suffix.lower() == ".png",
                }
                if file.suffix.lower() in {".png", ".gif"}
                else {
                    "name": common.plain_name(file),
                    "static": True,
                    "frames": [str(file)],
                }
                for file in pack
            ]
            return {"target": args.preset, "emojis": entries}
        frames = common.cel_rows(path)
        if script:
            entry.update(frames=frames, script=script)
        elif path.is_file() and path.suffix.lower() == ".gif" and not args.effect:
            entry["encoded"] = str(path.resolve())
        else:
            entry.update(frames=frames, static=len(frames) == 1)
    if args.effect:
        entry["effect"] = str(args.effect.resolve())
    return {"target": args.preset, "emojis": [entry]}


def build_pack(args):
    manifest = source_manifest(args)
    target = manifest.get("target", "generic")
    if target not in PRESETS:
        raise ValueError(f"unknown target {target!r}; choose generic or slack")
    if args.jobs < 1:
        raise ValueError("--jobs must be at least 1")
    entries = manifest["emojis"]
    names = [entry["name"] for entry in entries]
    if (
        not names
        or len(set(names)) != len(names)
        or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name) for name in names)
    ):
        raise ValueError("use a nonempty list of unique lowercase emoji names")
    profiles = []
    for entry in entries:
        profile = entry.get("target", target)
        if profile not in PRESETS:
            raise ValueError(f"{entry['name']}: unknown target {profile!r}")
        preset = PRESETS[profile]
        profiles.append(
            replace(preset, size=min(64, preset.size)) if args.draft else preset
        )
    if args.output.exists():
        raise ValueError(f"{args.output} already exists; use a new version folder")
    cache = args.cache or args.manifest.parent / ".forge-cache"
    if cache.resolve().is_relative_to(args.output.resolve()):
        raise ValueError("cache must sit outside the version folder")
    cache.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def build(entry, preset):
        return cached_entry(entry, args.manifest.parent, cache, preset, args)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        finished = list(pool.map(build, entries, profiles))
    with TemporaryDirectory(prefix=".pack-", dir=args.output.parent) as tmp:
        staging = Path(tmp) / "version"
        staging.mkdir()
        exports = staging / "exports"
        exports.mkdir()
        rows, hits = [], []
        for entry, (cached, hit), preset in zip(
            entries, finished, profiles, strict=True
        ):
            name = entry["name"]
            filename = name + (".png" if entry.get("static") else ".gif")
            copyfile(cached / filename, exports / filename)
            row = {
                key: entry[key]
                for key in ("name", "use", "matte", "seam", "loop")
                if key in entry
            }
            row["file"] = f"exports/{filename}"
            row["limits"] = vars(preset)
            registered = cached / "registered"
            if registered.is_dir():
                copytree(registered, staging / "registered" / name)
                timing = registered / "timing.json"
                if all("face" in frame for frame in common.read_timing(timing)[0]):
                    row["timing"] = f"registered/{name}/timing.json"
            run_script(
                "make_strips",
                exports / filename,
                staging / "strips" / f"{name}.png",
                "--size",
                32,
                preset.size,
                preset.size * 4,
            )
            rows.append(row)
            if hit:
                hits.append(name)
        export_manifest = write_json(
            staging / "pack.json", {"target": target, "emojis": rows}
        )
        checks = json.loads(
            run_script("check_pack", export_manifest, "--manifest-limits", "--json")
        )
        for result, row in zip(checks["emojis"], rows, strict=True):
            result["path"] = row["file"]
        write_json(staging / "checks.json", checks)
        run_script(
            "build_preview",
            export_manifest,
            staging / "preview",
            "--still-ms",
            manifest.get("preview_ms", 0),
        )
        run_script("bundle", export_manifest, staging / "pack")
        staging.rename(args.output)
    return {
        "target": target,
        "draft": args.draft,
        "cache_hits": hits,
        "checks": checks["emojis"],
        "version": str(args.output),
        "files": [
            artifact(args.output / path)
            for path in (
                "preview.gif",
                "preview.png",
                "pack.zip",
                "checks.json",
                *(row["file"] for row in rows),
            )
        ],
    }


def snapshot(args):
    if not args.source.exists():
        raise ValueError("snapshot source must be an existing file or directory")
    if args.destination.resolve().is_relative_to(args.source.resolve()):
        raise ValueError("snapshot destination must be outside the source directory")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    if args.source.is_dir():
        output = args.destination / f"{args.source.name}-{stamp}"
        copytree(args.source, output)
    else:
        args.destination.mkdir(parents=True, exist_ok=True)
        output = args.destination / f"{args.source.stem}-{stamp}{args.source.suffix}"
        copy2(args.source, output)
    return {"path": str(output)}


def assets(directory):
    if directory.is_file():
        return {common.plain_name(directory): directory.resolve()}
    if (directory / "pack").is_dir():
        directory /= "pack"
    if not directory.is_dir():
        raise ValueError(f"version folder not found: {directory}")
    files = sorted(
        path.resolve()
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".gif", ".png", ".jpg", ".jpeg"}
    )
    return {common.plain_name(path): path for path in files}


def compare(args):
    previous, current = assets(args.previous), assets(args.current)
    if args.previous.is_file() and args.current.is_file():
        name = common.plain_name(args.previous)
        current = {name: args.current.resolve()}
    names = sorted(previous.keys() | current.keys())
    if not names:
        raise ValueError("no emoji files found in either version")
    with TemporaryDirectory(prefix="forge-compare-") as tmp:
        placeholder = Path(tmp) / "missing.png"
        Image.new("RGBA", (32, 32)).save(placeholder)
        entries = [
            {"name": f"{label}-{name}", "file": str(files.get(name, placeholder))}
            for name in names
            for label, files in (("before", previous), ("after", current))
        ]
        manifest = write_json(Path(tmp) / "pack.json", {"emojis": entries})
        run_script(
            "build_preview",
            manifest,
            args.output,
            "--columns",
            2,
            "--still-ms",
            args.still_ms,
        )
    return {
        "added": sorted(current.keys() - previous.keys()),
        "removed": sorted(previous.keys() - current.keys()),
        "files": [
            artifact(args.output.with_suffix(suffix)) for suffix in (".gif", ".png")
        ],
    }


def add_budget(parser, default="slack"):
    parser.add_argument("--preset", choices=PRESETS, default=default)
    parser.add_argument("--size", type=int)
    parser.add_argument("--max-bytes", type=int)
    parser.add_argument("--max-frames", type=int)


def budget(args):
    preset = PRESETS[args.preset]
    return [
        "--size",
        args.size if args.size is not None else preset.size,
        "--max-bytes",
        args.max_bytes if args.max_bytes is not None else preset.max_bytes,
        "--max-frames",
        args.max_frames if args.max_frames is not None else preset.max_frames,
    ]


def script_command(command, args, argv):
    # Installed scripts keep the same CLI as when run from a checkout.
    if command == "check":
        options = ["--require-masks"] if args.require_masks else []
        manifest = (
            common.read_json(args.manifest)
            if args.manifest.suffix.lower() == ".json"
            else {}
        )
        if all(
            value is None
            for value in (args.preset, args.size, args.max_bytes, args.max_frames)
        ):
            options.append("--manifest-limits")
        args.preset = args.preset or manifest.get("target", "slack")
        check = subprocess.run(
            [
                sys.executable,
                SCRIPTS / "check_pack.py",
                args.manifest,
                *map(str, budget(args)),
                "--json",
                "--drift-px",
                str(args.drift_px),
                *(["--seam", str(args.seam)] if args.seam is not None else []),
                *options,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if not check.stdout.strip():
            raise ValueError(check.stderr.strip() or "check did not return results")
        return json.loads(check.stdout)
    if command == "fit":
        options = ["--matte", args.matte] if args.matte else []
        if args.timing_spec:
            options += ["--timing-spec", args.timing_spec]
        run_script(
            "assemble_gif",
            args.timing,
            args.output,
            *budget(args),
            "--frame-ms",
            args.frame_ms,
            "--loop",
            args.loop,
            *options,
        )
        return {"files": [artifact(args.output)]}

    # Argument parsing and help stay with the individual script. Capture only its result.
    run_script(SCRIPT_COMMANDS[command], *argv)
    output = args.output
    if command == "preview":
        paths = [output.with_suffix(".gif"), output.with_suffix(".png")]
    elif command in {"register", "render"}:
        paths = sorted(output.glob("*.png")) + [output / "timing.json"]
    elif command == "bundle":
        zipped = args.zip or output.with_name(output.name + ".zip")
        paths = sorted(output.iterdir()) + [zipped]
    else:
        paths = [output]
    return {"files": [artifact(path) for path in paths]}


def main():
    argv = sys.argv[1:]
    wants_json = "--json" in argv
    if wants_json:
        argv.remove("--json")

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            if wants_json:
                raise ValueError(message)
            super().error(message)

    parser = Parser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit one JSON result")
    subcommands = parser.add_subparsers(dest="command", required=True)
    new = subcommands.add_parser("new", help="scaffold a manifest and drawing briefs")
    new.add_argument("path", type=Path)
    new.add_argument("--no-manifest", action="store_true")
    new.add_argument("--preset", choices=PRESETS, default="generic")
    pipeline = subcommands.add_parser(
        "all", help="render, check, preview and bundle a pack"
    )
    pipeline.add_argument(
        "manifest", type=Path, help="pack JSON, image, cel folder, or Python hook"
    )
    common.add_timing_flags(pipeline)
    pipeline.add_argument(
        "--preset",
        choices=PRESETS,
        default="generic",
        help="target for plain files and folders",
    )
    pipeline.add_argument(
        "--allow-scripts",
        action="store_true",
        help="execute local hooks referenced by a trusted manifest",
    )
    pipeline.add_argument(
        "--script", help="trusted hook path[:function] for plain cels"
    )
    pipeline.add_argument(
        "--exports",
        action="store_true",
        help="treat a plain folder as finished exports",
    )
    pipeline.add_argument(
        "--no-register", action="store_true", help="keep placements in plain cels"
    )
    pipeline.add_argument(
        "--effect", type=Path, help="shader or pass chain for plain cels"
    )
    pipeline.add_argument("output", type=Path)
    pipeline.add_argument("--jobs", type=int, default=2)
    pipeline.add_argument("--cache", type=Path)
    pipeline.add_argument("--draft", action="store_true")
    pipeline.add_argument(
        "--backend", choices=["auto", "moderngl", "webgl"], default="auto"
    )
    pipeline.add_argument("--channel")
    save = subcommands.add_parser("snapshot", help="copy the version to a dated folder")
    save.add_argument("source", type=Path)
    save.add_argument("destination", type=Path)
    diff = subcommands.add_parser("compare", help="compare two versions side by side")
    diff.add_argument("previous", type=Path)
    diff.add_argument("current", type=Path)
    diff.add_argument("output", type=Path)
    diff.add_argument("--still-ms", type=int, default=0)
    fit = subcommands.add_parser("fit", help="encode within a target budget")
    fit.add_argument("timing", type=Path)
    fit.add_argument("output", type=Path)
    fit.add_argument("--matte")
    fit.add_argument(
        "--loop", type=int, default=0, help="GIF repeat count, 0 repeats indefinitely"
    )
    common.add_timing_flags(fit)
    add_budget(fit)
    check = subcommands.add_parser(
        "check", help="check encoded files and source geometry"
    )
    check.add_argument("manifest", type=Path)
    check.add_argument("--drift-px", type=float, default=2)
    check.add_argument("--seam", type=float, help="optional maximum seam difference")
    check.add_argument("--require-masks", action="store_true")
    add_budget(check, default=None)
    command = argv[0] if argv else ""
    if command in SCRIPT_COMMANDS.keys() - {"fit", "check"}:
        module = import_module(
            f".{SCRIPT_COMMANDS[command]}" if __package__ else SCRIPT_COMMANDS[command],
            package=__package__,
        )
        module.configure(subcommands.add_parser(command, description=module.__doc__))
    else:
        for name in sorted(SCRIPT_COMMANDS.keys() - {"fit", "check"}):
            subcommands.add_parser(name, help=f"see forge {name} --help")

    handlers = {
        "new": new_pack,
        "all": build_pack,
        "snapshot": snapshot,
        "compare": compare,
    }
    try:
        args = parser.parse_args(argv)
        result = (
            handlers[command](args)
            if command in handlers
            else script_command(command, args, argv[1:])
        )
        result = {"ok": True, "command": command, **result}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        result = {"ok": False, "command": command, "error": str(error)}
    if wants_json:
        print(json.dumps(result))
    elif result["ok"] or "emojis" in result:
        print(json.dumps(result.get("emojis", result), indent=2))
    else:
        print(f"error: {result['error']}", file=sys.stderr)
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
