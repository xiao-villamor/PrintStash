vec4 effect(vec2 uv, vec4 art) {
    float burst = envelope(.25, .28, .39, .44) + envelope(.57, .60, .64, .71);
    float line = floor(uv.y * 35.0);
    float shift = (hash(vec2(line, floor(seconds * 14.0))) - .5) * burst * .075;
    vec4 cyan = cel(uv + vec2(shift - .014 * burst, 0));
    vec4 pink = cel(uv + vec2(shift + .014 * burst, 0));
    vec4 slices = cel(uv + vec2(shift, 0));
    float scans = .5 + .5 * sin(uv.y * 340.0);
    slices.rgb *= 1.0 - burst * scans * .3;
    vec4 trails = over(vec4(.12, 1.3, 1.4, cyan.a * burst * .75),
                       vec4(1.5, .10, .66, pink.a * burst * .75));
    return over(slices, trails);
}
