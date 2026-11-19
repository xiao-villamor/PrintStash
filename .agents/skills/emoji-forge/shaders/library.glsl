// Shared straight-alpha compositing and procedural primitives.
const float TAU = 6.28318530718;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x),
               mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}

float fbm(vec2 p) {
    float accum = 0.0, gain = 0.5;
    for (int i = 0; i < 5; i++) {
        accum += gain * noise(p);
        p = mat2(1.6, 1.2, -1.2, 1.6) * p + 5.0;
        gain *= 0.5;
    }
    return accum;
}

float sdCircle(vec2 p, float radius) {
    return length(p) - radius;
}

float sdSegment(vec2 p, vec2 a, vec2 b) {
    vec2 segment = b - a;
    float t = clamp(dot(p - a, segment) / max(dot(segment, segment), 1e-6), 0.0, 1.0);
    return length(p - a - t * segment);
}

float halo(float distanceToEdge, float width) {
    return 1.0 - smoothstep(0.0, width, abs(distanceToEdge));
}

float envelope(float start, float peak, float release, float end) {
    return intensity * smoothstep(start, peak, phase) * (1.0 - smoothstep(release, end, phase));
}

float edgeFade(vec2 p) {
    vec2 margin = min(p, 1.0 - p);
    return smoothstep(0.008, 0.07, min(margin.x, margin.y));
}

vec4 cel(vec2 p) {
    return texture(source, p);
}

vec4 over(vec4 front, vec4 back) {
    float a = front.a + back.a * (1.0 - front.a);
    vec3 premult = front.rgb * front.a + back.rgb * back.a * (1.0 - front.a);
    return vec4(premult / max(a, 1e-5), a);
}

vec3 brightPass(vec4 color) {
    return max(color.rgb - 1.0, vec3(0.0)) * color.a;
}

vec4 blurPass(sampler2D tex, vec2 p, vec2 stepUV) {
    vec4 blur = texture(tex, p) * 0.227027;
    blur += (texture(tex, p + stepUV) + texture(tex, p - stepUV)) * 0.194595;
    blur += (texture(tex, p + 2.0 * stepUV) + texture(tex, p - 2.0 * stepUV)) * 0.121622;
    blur += (texture(tex, p + 3.0 * stepUV) + texture(tex, p - 3.0 * stepUV)) * 0.054054;
    blur += (texture(tex, p + 4.0 * stepUV) + texture(tex, p - 4.0 * stepUV)) * 0.016216;
    return blur;
}
