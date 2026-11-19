# Render a shader you write

Forge runs local fragment shaders on ModernGL or headless WebGL 2. It supplies timed cels, a clock and optional image inputs. You can use the example GLSL helpers, write a full fragment program, or combine your own passes.

## Write an effect

Save this as `wash.frag`. Declare your own uniform names and read them in your fragment code.

```glsl
uniform vec3 washColor;
uniform float washAmount;

vec4 effect(vec2 uv, vec4 art) {
    float traveling = 0.5 + 0.5 * sin(time * 4.0 + uv.y * 8.0);
    return vec4(mix(art.rgb, washColor, washAmount * traveling), art.a);
}
```

Save a local `uniforms.json` and pass it with `--params`.

```json
{
  "washColor": "#70c6ff",
  "washAmount": {"keyframes": [
    {"at": 0.0, "value": 0.0, "ease": "ease-in-out"},
    {"at": 0.5, "value": 0.8},
    {"at": 1.0, "value": 0.0}
  ]}
}
```

```sh
uv run --extra gpu forge render cels/ wash.frag out/wash --params uniforms.json
uv run forge fit out/wash/timing.json out/wash.gif --preset slack
```

For an explicit pass chain, use a JSON file in place of `wash.frag`. In a pack entry you can also put that JSON structure directly under `effect`. Paths are relative to the config that declares them.

```json
{
  "passes": [
    {"name": "emit", "shader": "./custom-emit.frag", "params": {"ink": "#ffac41"},
     "inputs": {"grain": "./grain.png"}},
    "bloom",
    {"shader": "./custom-composite.frag", "inputs": {"unblurred": "emit"}}
  ],
  "params": {"palette": ["#e73f76", "#91dcea"]}
}
```

A pass reads the previous result as `source`. It can also read the original cel through `original`, or a named earlier pass using `inputs`. In `params` you can use `{"image": "grain.png"}` for a texture or `{"pass": "emit"}` to bind an earlier result. Multiple samplers and sampler arrays are supported. Intermediate passes retain high dynamic range. The example `bloom` step extracts and blurs bright pixels then composites them. Omit it to use your own passes throughout. A single-fragment shorthand carries the original optional bloom pass. Use `"bloom": false` or an explicit one-pass chain to render only that shader.

## Uniform contract

| GLSL name | Meaning |
| --- | --- |
| `time` | Seconds from the start of the current sequence to this exposure. |
| `progress` | That same value normalized from 0 to 1. |
| `resolution` | Render width and height in pixels, as a `vec2`. |
| `source`, `original` | Straight-alpha RGBA `sampler2D` textures. Read with `uv` from the bottom left. |
| `anchor`, `intensity`, `phase`, `seconds` | Convenience uniforms used by older examples. `phase` mirrors progress and `seconds` can be scaled with a `speed` parameter. |

In the `effect(vec2 uv, vec4 art)` form, Forge declares the uniforms in the table and adds its helper library. Write only the extra declarations you need. A complete fragment file can instead define `void main()`, read `in vec2 uv`, and use one `out vec4` color. In that form declare the inputs and uniforms you use. Forge supplies the runtime GLSL version for its fullscreen vertex shader. For a file that runs on both backends, use the shared desktop GLSL 330 and ES 300 syntax.

Uniform names and types come from the compiled shader. Pass numbers, booleans, vectors, column-major matrices, arrays, CSS colors for `vec3` or `vec4`, or image bindings. With a uniform array, use one JSON entry per element, such as `[[0.1, 0.2], [0.3, 0.4]]` for a `vec2[2]`. A curve can use normalized `at`, absolute `ms` or zero-based `frame` keys. Easing supports `linear`, `ease-in`, `ease-out`, `ease-in-out` and `hold`. For booleans or textures, use hold interpolation. Frame-level values can override global parameters. Pass-specific values apply to their pass. Unused uniforms may be optimized away by the shader compiler.

## Backend and export options

ModernGL tries native OpenGL and then EGL. On headless Ubuntu the EGL path needs `libegl1`, `libegl-mesa0`, `libgl1` and `libgl1-mesa-dri`. Auto mode can fall back to Playwright Chromium. Install it with `uv run --extra webgl playwright install chromium` or use `--channel chrome`. To choose the backend, pass `--backend moderngl` or `--backend webgl`. WebGL needs floating-point render targets and may use a software renderer.

A larger resolution can help with fine lines or soft lighting. Try the default full-size render and downsample, or use `--draft` to get quick feedback. For soft glows, compare a solid matte to GIF's single transparent palette index. Check both themes if the target uses them.

The bundled fragments are starting material to read and remix. They cover [fire](../shaders/fire.frag), [sparkle](../shaders/sparkle.frag), [shockwave](../shaders/shockwave.frag), [beam and burn](../shaders/beam-burn.frag), [glitch](../shaders/glitch.frag), [portal](../shaders/portal.frag) and [aura](../shaders/aura.frag). The [shared library](../shaders/library.glsl) includes noise, fbm, distance fields and bloom helpers. The [custom example](../examples/custom/README.md) uses its own animation hook and shaders.
