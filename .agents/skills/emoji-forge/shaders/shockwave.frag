vec4 effect(vec2 uv, vec4 art) {
    vec2 p = uv - anchor;
    float progress = clamp((phase - .28) / .5, 0.0, 1.0);
    float distanceToRing = sdCircle(p, .03 + .40 * progress);
    float ring = halo(distanceToRing, .025) * envelope(.28, .33, .54, .78);
    vec2 warp = p / max(length(p), .001) * ring * sin(distanceToRing * 65.0) * .014;
    return over(cel(uv + warp * (1.0 - art.a * .82)), vec4(.3, 1.4, 2.3, ring));
}
