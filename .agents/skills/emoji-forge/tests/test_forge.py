import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "forge.py"


def call(*args, status=0):
    run = subprocess.run(
        [sys.executable, CLI, *map(str, args), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == status, (run.stdout, run.stderr)
    return json.loads(run.stdout)


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def make_cel(root, index, offset=0, wide=0, eye=(16, 17), color="gold", border=False):
    cel = Image.new("RGBA", (32, 32))
    body, face = Image.new("L", cel.size), Image.new("L", cel.size)
    geometry = (8 + offset, 8, 22 + offset + wide, 25)
    ImageDraw.Draw(cel).ellipse(geometry, fill=color)
    ImageDraw.Draw(body).ellipse(geometry, fill=255)
    ImageDraw.Draw(face).point(eye, fill=255)
    cel.putpixel(eye, (0, 0, 0, 255))
    if border:
        cel.putpixel((0, 0), (10, 0, 0, 255))
    result = {}
    for field, layer in (("file", cel), ("body", body), ("face", face)):
        filename = f"{index}-{field}.png"
        layer.save(root / filename)
        result[field] = filename
    return {"ms": 100, **result}


@pytest.fixture
def cels(tmp_path):
    rows = [make_cel(tmp_path, 1), make_cel(tmp_path, 2, color="royalblue")]
    return save(
        tmp_path / "timing.json", {"frames": [rows[0], rows[0], rows[1], rows[0]]}
    )


def test_register_removes_accidental_translation_and_keeps_declared_motion(tmp_path):
    first = make_cel(tmp_path, 1)
    second = make_cel(tmp_path, 2, offset=4, eye=(20, 17))
    timing = save(
        tmp_path / "timing.json",
        {"frames": [first, second, {**second, "motion": [2, 0, 0]}]},
    )
    directory = tmp_path / "registered"
    assert call("register", timing, directory)["ok"]
    rows = json.loads((directory / "timing.json").read_text())["frames"]
    boxes = [Image.open(directory / row["body"]).getbbox() for row in rows]
    assert boxes[:2] == [(8, 8, 23, 26)] * 2
    assert boxes[2] == (10, 8, 25, 26)


@pytest.mark.parametrize("matte", [None, "#20252f"])
def test_fit_combines_holds_without_losing_transparency_or_exact_matte(
    tmp_path, cels, matte
):
    gif = tmp_path / "sample.gif"
    matte_args = ("--matte", matte) if matte else ()
    result = call(
        "fit",
        cels,
        gif,
        "--size",
        32,
        "--max-frames",
        3,
        "--max-bytes",
        4000,
        *matte_args,
    )
    assert result["files"][0]["frames"] == 3
    with Image.open(gif) as image:
        assert image.info["loop"] == 0
        for index, duration in enumerate((200, 100, 100)):
            image.seek(index)
            assert image.info["duration"] == duration
            assert image.convert("RGBA").getpixel((0, 0)) == (
                (32, 37, 47, 255) if matte else (0, 0, 0, 0)
            )
    entry = {"name": "hello", "file": gif.name, "timing": cels.name}
    if matte:
        entry["matte"] = matte
    pack = save(tmp_path / "pack.json", {"emojis": [entry]})
    result = call("check", pack, "--size", 32, "--require-masks")
    assert result["emojis"][0]["duration_ms"] == 400
    assert result["emojis"][0]["geometry_checked"]
    assert call(
        "fit", cels, tmp_path / "too-big.gif", "--size", 32, "--max-bytes", 10, status=1
    )["error"]
    assert not (tmp_path / "too-big.gif").exists()


def test_preview_strips_and_bundle_preserve_order_names_and_pixels(tmp_path, cels):
    gif = tmp_path / "sample.gif"
    call("assemble", "--size", 32, cels, gif)
    png = tmp_path / "still.png"
    Image.new("RGBA", (32, 32)).save(png)
    pack = save(
        tmp_path / "pack.json",
        {
            "emojis": [
                {"name": "first", "file": png.name, "use": "A greeting"},
                {"name": "second", "file": gif.name, "use": "A wave"},
            ]
        },
    )
    stem = tmp_path / "gallery"
    preview = call("preview", pack, stem, "--columns", 2)
    assert [item["frames"] for item in preview["files"]] == [3, 1]
    output = tmp_path / "strip.png"
    assert (
        call("strips", gif, output, "--size", 8, 32, 128)["files"][0]["size"][0] == 408
    )
    folder = tmp_path / "pack"
    archive = tmp_path / "deliver.zip"
    call("bundle", pack, folder, f"--zip={archive}")
    with ZipFile(archive) as zipped:
        assert zipped.namelist() == ["NAMES.md", "first.png", "second.gif"]
        assert zipped.read("second.gif") == gif.read_bytes()
        assert "| 1 | `first` | A greeting |" in zipped.read("NAMES.md").decode()


@pytest.mark.parametrize(
    ("failure", "hint"),
    [
        ("face", "face mask"),
        ("drift", "drift"),
        ("seam", "seam"),
        ("border", "canvas border"),
        ("frames", "GIF frames"),
        ("bytes", "exceeds"),
        ("size", "expected"),
        ("masks", "missing timing"),
    ],
)
def test_checker_reports_bad_assets_in_json(tmp_path, failure, hint):
    first = make_cel(tmp_path, 1)
    second = make_cel(
        tmp_path,
        2,
        offset=5 if failure == "drift" else 0,
        color="royalblue",
        eye=(1, 1) if failure == "face" else (16, 17),
        border=failure == "border",
    )
    third = second if failure == "seam" else first
    timing = save(tmp_path / "timing.json", {"frames": [first, second, third]})
    gif = tmp_path / "sample.gif"
    call("assemble", timing, gif)
    entry = {"name": "sample", "file": gif.name, "timing": timing.name}
    if failure == "masks":
        entry.pop("timing")
    pack = save(tmp_path / "pack.json", {"emojis": [entry]})
    args = {
        "frames": ["--max-frames", 2],
        "bytes": ["--max-bytes", 4],
        "seam": ["--seam", 0.0001],
        "drift": ["--drift-px", 1],
    }.get(failure, [])
    result = call(
        "check",
        pack,
        "--size",
        64 if failure == "size" else 32,
        "--require-masks",
        *args,
        status=1,
    )
    assert not result["ok"]
    assert hint in result["emojis"][0]["error"]


def test_scaffold_build_cache_edits_snapshot_and_comparison(tmp_path):
    project = tmp_path / "brief"
    call("new", project, "--preset", "slack")
    assert {"style.md", "storyboard.md", "pack.json", "cels"} == {
        p.name for p in project.iterdir()
    }
    source = Image.new("RGBA", (512, 512))
    ImageDraw.Draw(source).ellipse((120, 120, 290, 300), fill="gold")
    source.save(project / "body.png")
    source.save(project / "another.png")
    manifest = save(
        project / "pack.json",
        {
            "target": "slack",
            "emojis": [
                {"name": "hey", "static": True, "frames": [{"file": "body.png"}]},
                {
                    "name": "hello",
                    "static": True,
                    "target": "generic",
                    "frames": [{"file": "another.png"}],
                },
            ],
        },
    )
    before = tmp_path / "before"
    draft = call("all", manifest, before, "--draft", "--jobs", 2)
    assert [item["size"] for item in draft["checks"]] == [[64, 64], [64, 64]]
    assert draft["cache_hits"] == []
    cached = call("all", manifest, tmp_path / "second", "--draft")
    assert cached["cache_hits"] == ["hey", "hello"]
    saved = Path(call("snapshot", before, tmp_path / "review")["path"])
    assert (saved / "pack.zip").read_bytes() == (before / "pack.zip").read_bytes()
    assert call("snapshot", before, before / "inside", status=1)["error"]
    assert call("all", manifest, before, status=1)["error"]

    after = tmp_path / "after"
    full = call("all", manifest, after)
    assert full["cache_hits"] == []
    assert [item["size"] for item in full["checks"]] == [[128, 128], [256, 256]]
    assert call("check", after / "pack.json")["ok"]
    source.putpixel((245, 245), (1, 2, 3, 255))
    source.save(project / "body.png")
    assert call("all", manifest, tmp_path / "changed")["cache_hits"] == ["hello"]
    comparison = call("compare", saved, after, tmp_path / "comparison")
    assert not comparison["added"] and Path(comparison["files"][1]["path"]).is_file()
    assert call("fit", "--not-an-option", status=1)["error"]


def test_every_shader_renders_on_modern_gl(tmp_path, cels):
    base = json.loads(cels.read_text())["frames"][0]
    save(cels, {"frames": [{**base, "ms": 420}, {**base, "ms": 580}]})
    for shader in sorted((ROOT / "shaders").glob("*.frag")):
        result = call(
            "render",
            cels,
            shader,
            tmp_path / shader.stem,
            "--backend",
            "moderngl",
            "--size",
            24,
        )
        assert len(result["files"]) == 3
        first, peak = [
            Image.open(result["files"][i]["path"]).convert("RGBA") for i in (0, 1)
        ]
        assert first.getbbox() and first.getpixel((0, 0))[3] == 0
        assert first.tobytes() != peak.tobytes(), shader.name


def test_headless_webgl_fallback_renders(tmp_path, cels):
    channel = os.getenv("FORGE_TEST_CHROME")
    if not channel:
        pytest.skip(
            "set FORGE_TEST_CHROME=chrome to run browser backend with installed Chrome"
        )
    result = call(
        "render",
        cels,
        ROOT / "shaders/sparkle.frag",
        tmp_path / "webgl",
        "--backend",
        "webgl",
        "--channel",
        channel,
        "--draft",
    )
    assert result["files"][0]["size"] == [128, 128]
    first = Image.open(result["files"][0]["path"]).convert("RGBA")
    assert first.getpixel((0, 0))[3] == 0
    assert first.getpixel((60, 64))[3] > 250


@pytest.mark.parametrize("backend", ["moderngl", "webgl"])
def test_custom_uniforms_textures_curves_and_named_passes(tmp_path, backend):
    channel = os.getenv("FORGE_TEST_CHROME")
    if backend == "webgl" and not channel:
        pytest.skip("set FORGE_TEST_CHROME to test WebGL")
    cels = tmp_path / "cels"
    cels.mkdir()
    for index in range(3):
        make_cel(cels, index)
    Image.new("RGBA", (7, 4), "blue").save(tmp_path / "texture.png")
    (tmp_path / "paint.frag").write_text(
        """uniform vec3 palette[2];
uniform float weights[2];
uniform sampler2D pattern;
uniform vec2 delta;
uniform bool isLit;
uniform float blueGain;
vec4 effect(vec2 uv, vec4 base) {
    vec3 layer = palette[0] * weights[0] + palette[1] * weights[1]
        + texture(pattern, uv + delta).rgb * blueGain;
    return vec4(isLit ? layer : base.rgb, base.a);
}
""",
        encoding="utf-8",
    )
    (tmp_path / "finish.frag").write_text(
        """#version 330
in vec2 uv;
out vec4 painted;
uniform sampler2D source;
uniform sampler2D first;
uniform sampler2D original;
uniform float time;
uniform float progress;
uniform vec2 resolution;
void main() {
    vec4 before = texture(first, uv);
    vec4 current = texture(source, uv);
    painted = vec4(before.r, current.g,
        before.b + time * .1 + progress * .01 + 1.0 / resolution.x,
        texture(original, uv).a);
}
""",
        encoding="utf-8",
    )
    chain = save(
        tmp_path / "chain.json",
        {
            "passes": [
                {
                    "name": "paint",
                    "shader": "paint.frag",
                    "inputs": {"pattern": "texture.png"},
                },
                "bloom",
                {"shader": "finish.frag", "inputs": {"first": "paint"}},
            ],
            "params": {
                "palette": ["red", "#00ff00"],
                "weights": [0.25, 0.50],
                "delta": [0.03, 0.01],
                "isLit": True,
                "blueGain": {
                    "keyframes": [{"ms": 0, "value": 0.0}, {"ms": 300, "value": 1.0}]
                },
            },
        },
    )
    flags = ["--channel", channel] if backend == "webgl" else []
    result = call(
        "render",
        cels,
        chain,
        tmp_path / "rendered",
        "--backend",
        backend,
        "--size",
        32,
        "--frame-ms",
        150,
        *flags,
    )
    first, mid = [
        Image.open(result["files"][i]["path"]).convert("RGBA") for i in (0, 1)
    ]
    assert first.getpixel((16, 16)) == pytest.approx((64, 128, 8, 255), abs=1)
    assert mid.getpixel((16, 16)) == pytest.approx((64, 128, 140, 255), abs=2)
    assert mid.getpixel((0, 0))[3] == 0
    chain_json = json.loads(chain.read_text())
    chain_json["params"]["delta"] = [1, 2, 3]
    save(chain, chain_json)
    failed = call(
        "render",
        cels,
        chain,
        tmp_path / "failed",
        "--backend",
        backend,
        "--size",
        32,
        *flags,
        status=1,
    )
    assert "delta" in failed["error"] and "expected 2 component" in failed["error"]


def test_plain_files_and_keyframed_timings(tmp_path):
    cels = tmp_path / "cels"
    cels.mkdir()
    for index, color in enumerate(("gold", "royalblue", "teal")):
        make_cel(cels, index, color=color)
    timing = save(
        tmp_path / "timing.json",
        {
            "keyframes": [
                {"frame": 0, "ms": 0, "ease": "ease-in"},
                {"frame": 2, "ms": 242},
                {"frame": 3, "ms": 401},
            ]
        },
    )
    registered = tmp_path / "registered"
    call("register", cels, registered, "--timing-spec", timing)
    assert [
        frame["ms"]
        for frame in json.loads((registered / "timing.json").read_text())["frames"]
    ] == [
        60.5,
        181.5,
        159,
    ]
    gif = tmp_path / "render.gif"
    call("fit", cels, gif, "--timing-spec", timing, "--size", 32, "--loop", 3)
    with Image.open(gif) as image:
        assert image.n_frames == 3
        observed = []
        for index in range(image.n_frames):
            image.seek(index)
            observed.append(image.info["duration"])
        assert observed == [60, 180, 160]
        assert image.info["loop"] == 3
    assert call("check", gif, "--size", 32)["ok"]
    assert call("strips", cels, tmp_path / "strips.png")["ok"]
    assert call("preview", gif, tmp_path / "preview")["ok"]
    assert call("bundle", gif, tmp_path / "pack")["ok"]
    saved = Path(call("snapshot", gif, tmp_path / "snapshots")["path"])
    assert saved.read_bytes() == gif.read_bytes()
    assert call("compare", gif, saved, tmp_path / "compare")["ok"]
    result = call("all", cels, tmp_path / "all", "--draft", "--no-register", status=1)
    assert "only downsamples" in result["error"]
    second = tmp_path / "from-gif.gif"
    call("assemble", gif, second, "--loop", 2)
    with Image.open(second) as image:
        assert image.n_frames == 3
        assert image.info["loop"] == 2
    assert call("check", second, "--size", 32)["emojis"][0]["loop"] == 2
    for frame in cels.glob("*.png"):
        with Image.open(frame) as cel:
            larger = cel.resize((128, 128), Image.Resampling.NEAREST)
        larger.save(frame)
    exported = tmp_path / "plain-export"
    result = call(
        "all", cels, exported, "--draft", "--no-register", "--timing-spec", timing
    )
    assert result["checks"][0]["duration_ms"] == 400
    assert (
        call("all", exported / "exports", tmp_path / "plain-pack", "--draft")["checks"][
            0
        ]["frames"]
        == 3
    )


def test_hook_runs_per_frame_and_manifest_runs_only_when_requested(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "draw.py").write_text(
        """from PIL import Image, ImageDraw

def animate(frame, ctx):
    print("hook output should stay out of the JSON stream")
    assert ctx["time"] == (0, .11, .34)[ctx["index"]]
    color = ctx["params"]["colors"][ctx["index"]]
    image = Image.new("RGBA", (ctx["size"], ctx["size"]))
    ImageDraw.Draw(image).ellipse((32, 32, 96, 96), fill=color)
    return {**frame, "image": image}
""",
        encoding="utf-8",
    )
    dependency = project / "palette.txt"
    dependency.write_text("first", encoding="utf-8")
    manifest = save(
        project / "pack.json",
        {
            "target": "slack",
            "emojis": [
                {
                    "name": "pulse",
                    "timing": [110, 230, 150],
                    "script": {
                        "path": "draw.py",
                        "mode": "frame",
                        "count": 3,
                        "deps": ["palette.txt"],
                        "params": {"colors": ["yellow", "blue", "yellow"]},
                    },
                }
            ],
        },
    )
    assert (
        "--allow-scripts"
        in call("all", manifest, tmp_path / "denied", "--draft", status=1)["error"]
    )
    built = call("all", manifest, tmp_path / "first", "--draft", "--allow-scripts")
    assert built["checks"][0]["frames"] == 3
    assert built["checks"][0]["duration_ms"] == 490
    assert call("all", manifest, tmp_path / "repeat", "--draft", "--allow-scripts")[
        "cache_hits"
    ] == ["pulse"]
    dependency.write_text("changed", encoding="utf-8")
    assert (
        call("all", manifest, tmp_path / "edit", "--draft", "--allow-scripts")[
            "cache_hits"
        ]
        == []
    )


def test_custom_example_runs_with_whole_sequence_hook(tmp_path):
    result = call(
        "all",
        ROOT / "examples/custom/pack.json",
        tmp_path / "lantern",
        "--allow-scripts",
        "--cache",
        tmp_path / "cache",
        "--draft",
        "--backend",
        "moderngl",
    )
    assert result["checks"][0]["frames"] == 12
    assert result["checks"][0]["duration_ms"] == 2600
    assert (tmp_path / "lantern/pack/lantern.gif").is_file()
    direct = call(
        "all",
        ROOT / "examples/custom/draw.py",
        tmp_path / "direct",
        "--cache",
        tmp_path / "cache",
        "--draft",
        "--no-register",
    )
    assert direct["checks"][0]["frames"] == 12
