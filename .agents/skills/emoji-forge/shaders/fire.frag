vec4 effect(vec2 uv, vec4 art) {
    vec2 p = uv - anchor;
    float strength = envelope(.05, .3, .62, .94);
    vec2 flow = p * vec2(12.0, 5.0) - vec2(0.0, seconds * 2.8);
    float plume = fbm(flow + vec2(fbm(flow + 4.0), 0.0) * 2.0);
    float height = (0.14 + plume * 0.23) * strength;
    float flame = smoothstep(-.025, .025, height - p.y - abs(p.x) * .85)
        * smoothstep(-.02, .03, p.y) * strength;
    vec3 color = mix(vec3(2.3, 1.3, .15), vec3(1.5, .16, .02), smoothstep(0.0, .25, p.y));
    vec4 warm = cel(uv + vec2((plume - .5) * .018 * flame, 0));
    return over(warm, vec4(color, flame));
}
