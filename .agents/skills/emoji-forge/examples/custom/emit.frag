uniform vec3 filamentColor;
uniform vec2 wick;
uniform float pulseRadius;

vec4 effect(vec2 uv, vec4 art) {
    float arc = halo(sdCircle(uv - wick, pulseRadius), 0.022);
    float rise = sin(progress * 3.14159265);
    vec4 light = vec4(filamentColor * 2.5, arc * rise * 0.9);
    return over(art, light);
}
