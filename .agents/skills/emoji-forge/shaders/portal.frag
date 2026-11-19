vec4 effect(vec2 uv, vec4 art) {
    vec2 p = uv - anchor;
    p.y *= 2.2;
    float open = envelope(.11, .36, .68, .93);
    float angle = atan(p.y, p.x);
    float r = length(p);
    float flow = fbm(vec2(r * 22.0 - seconds * 2.0, sin(angle * 4.0 + seconds * 3.0)));
    float ring = halo(r - (.02 + .28 * open + .015 * (flow - .5)), .04) * open;
    float vortex = (1.0 - smoothstep(.02, .30 * open + .02, r)) * open * (.3 + .45 * flow);
    vec4 bent = cel(uv + vec2(-p.y, p.x) * vortex * .02 * (1.0 - art.a));
    return over(bent, vec4(.5 + flow, .16, 2.2, max(ring, vortex)));
}
