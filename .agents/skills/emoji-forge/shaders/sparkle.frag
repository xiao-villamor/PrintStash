vec4 effect(vec2 uv, vec4 art) {
    float sparks = 0.0;
    for (int i = 0; i < 9; i++) {
        float seed = float(i);
        float age = (phase - (.18 + .038 * seed)) * 3.0;
        vec2 seedPoint = vec2(seed, 9.0);
        float angle = TAU * hash(seedPoint);
        vec2 direction = vec2(cos(angle), sin(angle));
        vec2 p = uv - anchor - direction * (.035 + max(age, 0.0) * .21);
        vec2 q = abs(p);
        float crossShape = min(q.x + q.y * .24, q.y + q.x * .24);
        float life = smoothstep(0.0, .2, age) * (1.0 - smoothstep(.32, 1.0, age));
        sparks = max(sparks, halo(crossShape, .02) * life);
    }
    return over(art, vec4(2.2, 1.5, .3, sparks * intensity));
}
