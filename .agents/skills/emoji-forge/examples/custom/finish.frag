uniform sampler2D unblurred;
uniform float coolMix;

vec4 effect(vec2 uv, vec4 art) {
    float glowAlpha = max(0.0, art.a - texture(unblurred, uv).a);
    vec3 tint = vec3(0.49, 0.59, 1.0);
    return vec4(mix(art.rgb, tint, coolMix * glowAlpha), art.a);
}
