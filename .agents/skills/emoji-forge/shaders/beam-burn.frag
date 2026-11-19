vec4 effect(vec2 uv, vec4 art) {
    vec2 p = uv - anchor;
    float shot = envelope(.23, .29, .35, .46);
    float travel = clamp((phase - .24) * 5.0, 0.0, 1.0);
    vec3 eye = vec3(anchor - .5, 1.0);
    vec3 front = mix(eye, vec3(vec2(.81, .68) - .5, 1.0) * .24, travel);
    vec2 tip = .5 + front.xy / front.z;
    vec2 ray = tip - anchor;
    float along = clamp(dot(p, ray) / max(dot(ray, ray), 1e-6), 0.0, 1.0);
    float inverseDepth = mix(1.0 / eye.z, 1.0 / front.z, along);
    float shaft = halo(sdSegment(uv, anchor, tip), .012 * inverseDepth) * shot;
    float flare = halo(sdCircle(uv - tip, 0.0), .036 / front.z) * shot;

    float burn = envelope(.38, .51, .68, .89);
    float radiusBurn = .02 + .19 * smoothstep(.39, .70, phase);
    float edge = length(p) - radiusBurn + .072 * (fbm(p * 36.0 + fbm(p * 12.0)) - .5);
    float cut = smoothstep(-.007, .009, edge);
    vec3 charred = mix(art.rgb, vec3(.12, .08, .11), halo(edge - .012, .05));
    vec3 ember = mix(charred, vec3(2.2, .66, .12), halo(edge - .005, .014) * burn);
    vec4 burned = vec4(mix(art.rgb, ember, burn), art.a * mix(1.0, cut, burn));
    return over(vec4(2.6, .52, .98, max(shaft, flare)), burned);
}
