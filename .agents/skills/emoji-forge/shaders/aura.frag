vec4 effect(vec2 uv, vec4 art) {
    vec2 p = uv - anchor;
    float power = envelope(.08, .44, .67, .95);
    float angle = atan(p.y, p.x);
    float cloud = fbm(p * vec2(11.0, 8.0) - vec2(0, seconds * 1.5));
    float arc = halo(length(p * vec2(1.0, .88)) - (.24 + cloud * .12), .08);
    float ridge = abs(fract(angle / TAU * 9.0 + cloud * 3.0) - .5);
    float lightning = halo(ridge, .034) * arc * step(.62, hash(vec2(floor(seconds * 15.0), 8)));
    float opacity = power * max(lightning, arc * cloud * .74);
    vec3 color = mix(vec3(.27, .59, 1.8), vec3(2.1, 1.3, .32), smoothstep(.32, .56, phase));
    return over(art, vec4(color, opacity));
}
