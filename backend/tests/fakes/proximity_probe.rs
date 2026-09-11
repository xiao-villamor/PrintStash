// Research-only closest-surface prototype; not linked or executed by PrintStash.
// Input: little-endian u64 face/point counts, float64 triangle/point coordinates.
// Only the admitted, cleaned arrays from the companion probe are supported.
// Output: float64 build/query seconds, u64 work, then distance and closest xyz.

use std::{env, fs, io::Write, time::Instant};
type V = [f64; 3];
fn sub(a: V, b: V) -> V {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn add(a: V, b: V) -> V {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}
fn mul(a: V, s: f64) -> V {
    [a[0] * s, a[1] * s, a[2] * s]
}
fn dot(a: V, b: V) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn cross(a: V, b: V) -> V {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn closest(p: V, t: [V; 3]) -> (f64, V) {
    let [a, b, c] = t;
    let ab = sub(b, a);
    let ac = sub(c, a);
    let n = cross(ab, ac);
    let nn = dot(n, n);
    let h = dot(sub(p, a), n) / nn;
    let projected = sub(p, mul(n, h));
    let ap = sub(projected, a);
    let d00 = dot(ab, ab);
    let d01 = dot(ab, ac);
    let d11 = dot(ac, ac);
    let d20 = dot(ap, ab);
    let d21 = dot(ap, ac);
    let v = (d11 * d20 - d01 * d21) / nn;
    let w = (d00 * d21 - d01 * d20) / nn;
    let mut best = if v >= 0.0 && w >= 0.0 && v + w <= 1.0 {
        h * h * nn
    } else {
        f64::INFINITY
    };
    let mut point = projected;
    for (first, second) in [(a, b), (b, c), (c, a)] {
        let edge = sub(second, first);
        let u = (dot(sub(p, first), edge) / dot(edge, edge)).clamp(0.0, 1.0);
        let q = add(first, mul(edge, u));
        let d = dot(sub(p, q), sub(p, q));
        if d < best {
            best = d;
            point = q;
        }
    }
    (best, point)
}
struct Node {
    low: V,
    high: V,
    begin: usize,
    end: usize,
    children: Option<(usize, usize)>,
}
fn build(nodes: &mut Vec<Node>, ids: &mut [usize], triangles: &[[V; 3]], offset: usize) -> usize {
    let mut low = [f64::INFINITY; 3];
    let mut high = [f64::NEG_INFINITY; 3];
    for &id in ids.iter() {
        for p in triangles[id] {
            for k in 0..3 {
                low[k] = low[k].min(p[k]);
                high[k] = high[k].max(p[k]);
            }
        }
    }
    let idx = nodes.len();
    nodes.push(Node {
        low,
        high,
        begin: offset,
        end: offset + ids.len(),
        children: None,
    });
    if ids.len() > 32 {
        let mut axis = 0;
        for k in 1..3 {
            if high[k] - low[k] > high[axis] - low[axis] {
                axis = k;
            }
        }
        let center = |id: usize| {
            let t = triangles[id];
            t.iter().map(|v| v[axis]).fold(f64::INFINITY, f64::min)
                + t.iter().map(|v| v[axis]).fold(f64::NEG_INFINITY, f64::max)
        };
        ids.sort_unstable_by(|&a, &b| center(a).total_cmp(&center(b)).then(a.cmp(&b)));
        let middle = ids.len() / 2;
        let (left, right) = ids.split_at_mut(middle);
        let a = build(nodes, left, triangles, offset);
        let b = build(nodes, right, triangles, offset + middle);
        nodes[idx].children = Some((a, b));
    }
    idx
}
fn distance(p: V, n: &Node) -> f64 {
    let mut d = 0.0;
    for k in 0..3 {
        let a = (n.low[k] - p[k]).max(p[k] - n.high[k]).max(0.0);
        d += a * a;
    }
    d
}
fn main() {
    let args: Vec<_> = env::args().collect();
    let bytes = fs::read(&args[1]).unwrap();
    let mut cursor = 0;
    let mut next = || {
        let value = u64::from_le_bytes(bytes[cursor..cursor + 8].try_into().unwrap());
        cursor += 8;
        value
    };
    let count = next() as usize;
    let queries = next() as usize;
    assert!(count > 0 && count <= 2_000_000 && queries > 0 && queries <= 5000);
    assert_eq!(bytes.len(), 16 + (9 * count + 3 * queries) * 8);
    let mut point = || {
        [
            f64::from_bits(next()),
            f64::from_bits(next()),
            f64::from_bits(next()),
        ]
    };
    let triangles: Vec<_> = (0..count).map(|_| [point(), point(), point()]).collect();
    let points: Vec<_> = (0..queries).map(|_| point()).collect();
    assert!(triangles
        .iter()
        .flatten()
        .flatten()
        .chain(points.iter().flatten())
        .all(|v| v.is_finite()));
    let now = Instant::now();
    let mut ids: Vec<_> = (0..count).collect();
    let mut nodes = Vec::new();
    build(&mut nodes, &mut ids, &triangles, 0);
    let build_seconds = now.elapsed().as_secs_f64();
    let now = Instant::now();
    let mut work = 0u64;
    let mut results = Vec::new();
    for p in points {
        let mut best = f64::INFINITY;
        let mut nearest = [0.0; 3];
        let mut stack = vec![0];
        while let Some(idx) = stack.pop() {
            let n = &nodes[idx];
            if distance(p, n) > best + 1e-20 {
                continue;
            }
            if let Some((left, right)) = n.children {
                if distance(p, &nodes[left]) <= distance(p, &nodes[right]) {
                    stack.push(right);
                    stack.push(left);
                } else {
                    stack.push(left);
                    stack.push(right);
                }
            } else {
                for &id in &ids[n.begin..n.end] {
                    work += 1;
                    assert!(work <= 32_000_000, "proximity_work_limit");
                    let (d, q) = closest(p, triangles[id]);
                    if d < best {
                        best = d;
                        nearest = q;
                    }
                }
            }
        }
        results.push((best.sqrt(), nearest));
    }
    let query_seconds = now.elapsed().as_secs_f64();
    let mut output = fs::File::create(&args[2]).unwrap();
    output.write_all(&build_seconds.to_le_bytes()).unwrap();
    output.write_all(&query_seconds.to_le_bytes()).unwrap();
    output.write_all(&work.to_le_bytes()).unwrap();
    for (d, p) in results {
        for value in [d, p[0], p[1], p[2]] {
            output.write_all(&value.to_le_bytes()).unwrap();
        }
    }
}
