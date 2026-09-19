//! Immutable bounded closest-surface queries, detached from Python.
use crate::{Error, Point, Result, Triangle};
use nalgebra::{Matrix3, Vector3};
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Alignment {
    pub rotation: [[f64; 3]; 3],
    pub translation: Point,
    pub convergence: f64,
    pub error: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ClosestPoint {
    pub distance: f64,
    pub point: Point,
}
const MAX_TRIANGLES: usize = 2_000_000;
const MAX_WORK: usize = 32_000_000;

fn sub(a: Point, b: Point) -> Point {
    std::array::from_fn(|i| a[i] - b[i])
}
fn dot(a: Point, b: Point) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn cross(a: Point, b: Point) -> Point {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
struct Node {
    low: Point,
    high: Point,
    start: usize,
    end: usize,
    children: Option<(usize, usize)>,
}
impl Node {
    fn distance(&self, p: Point) -> f64 {
        let delta = std::array::from_fn(|i| (self.low[i] - p[i]).max(p[i] - self.high[i]).max(0.0));
        dot(delta, delta)
    }
}

pub struct SurfaceTree {
    triangles: Vec<Triangle>,
    order: Vec<usize>,
    nodes: Vec<Node>,
}

impl SurfaceTree {
    pub fn try_new(triangles: Vec<Triangle>) -> Result<Self> {
        if triangles.is_empty() || triangles.len() > MAX_TRIANGLES {
            return Err(Error::InvalidProximitySurface);
        }
        for triangle in &triangles {
            if !triangle.iter().flatten().all(|value| value.is_finite()) {
                return Err(Error::InvalidProximitySurface);
            }
            let normal = cross(sub(triangle[1], triangle[0]), sub(triangle[2], triangle[0]));
            let area = dot(normal, normal);
            if !area.is_finite() || area <= 0.0 {
                return Err(Error::InvalidProximitySurface);
            }
        }
        let bounds: Vec<(Point, Point)> = triangles
            .iter()
            .map(|triangle| {
                (
                    std::array::from_fn(|axis| {
                        triangle[0][axis]
                            .min(triangle[1][axis])
                            .min(triangle[2][axis])
                    }),
                    std::array::from_fn(|axis| {
                        triangle[0][axis]
                            .max(triangle[1][axis])
                            .max(triangle[2][axis])
                    }),
                )
            })
            .collect();
        let centers: Vec<Point> = bounds
            .iter()
            .map(|(low, high)| std::array::from_fn(|axis| (low[axis] + high[axis]) / 2.0))
            .collect();
        if !centers.iter().flatten().all(|value| value.is_finite()) {
            return Err(Error::InvalidProximitySurface);
        }
        let count = triangles.len();
        let mut tree = Self {
            triangles,
            order: (0..count).collect(),
            nodes: Vec::new(),
        };
        tree.branch(0, count, &bounds, &centers);
        Ok(tree)
    }

    fn branch(
        &mut self,
        start: usize,
        end: usize,
        bounds: &[(Point, Point)],
        centers: &[Point],
    ) -> usize {
        let mut low = [f64::INFINITY; 3];
        let mut high = [f64::NEG_INFINITY; 3];
        let mut center_low = low;
        let mut center_high = high;
        for &id in &self.order[start..end] {
            for axis in 0..3 {
                low[axis] = low[axis].min(bounds[id].0[axis]);
                high[axis] = high[axis].max(bounds[id].1[axis]);
                center_low[axis] = center_low[axis].min(centers[id][axis]);
                center_high[axis] = center_high[axis].max(centers[id][axis]);
            }
        }
        let id = self.nodes.len();
        self.nodes.push(Node {
            low,
            high,
            start,
            end,
            children: None,
        });
        if end - start > 32 {
            let mut axis = 0;
            for candidate in 1..3 {
                if center_high[candidate] - center_low[candidate]
                    > center_high[axis] - center_low[axis]
                {
                    axis = candidate;
                }
            }
            self.order[start..end].sort_by(|&a, &b| centers[a][axis].total_cmp(&centers[b][axis]));
            let middle = start + (end - start) / 2;
            let left = self.branch(start, middle, bounds, centers);
            let right = self.branch(middle, end, bounds, centers);
            self.nodes[id].children = Some((left, right));
        }
        id
    }

    pub fn closest(&self, points: &[Point], max_work: usize) -> Result<Vec<ClosestPoint>> {
        if points.is_empty() || points.len() > 5000 {
            return Err(Error::InvalidProximityPoints);
        }
        if max_work == 0 || max_work > MAX_WORK {
            return Err(Error::InvalidProximityBudget);
        }
        let mut output = Vec::with_capacity(points.len());
        let mut work = 0usize;
        for &p in points {
            if !p.iter().all(|v| v.is_finite()) {
                return Err(Error::InvalidProximityPoints);
            }
            let mut best = f64::INFINITY;
            let mut nearest = [0.0; 3];
            let mut stack = vec![(0, false)];
            if self.nodes[0].children.is_some() {
                stack.push((0, true));
            }
            while let Some((id, seed)) = stack.pop() {
                let node = &self.nodes[id];
                if node.distance(p) > best + 1e-20 {
                    continue;
                }
                if let Some((left, right)) = node.children {
                    if seed {
                        stack.push((
                            if self.nodes[left].distance(p) <= self.nodes[right].distance(p) {
                                left
                            } else {
                                right
                            },
                            true,
                        ));
                    } else {
                        stack.push((right, false));
                        stack.push((left, false));
                    }
                } else {
                    work += node.end - node.start;
                    if work > max_work {
                        return Err(Error::ProximityWorkLimit);
                    }
                    for &index in &self.order[node.start..node.end] {
                        let (distance, closest) = triangle_closest(p, self.triangles[index]);
                        if distance < best {
                            best = distance;
                            nearest = closest;
                        }
                    }
                }
            }
            if !best.is_finite() {
                return Err(Error::InvalidProximityPoints);
            }
            output.push(ClosestPoint {
                distance: best.sqrt(),
                point: nearest,
            });
        }
        Ok(output)
    }

    pub fn align(&self, points: &[Point], rotation: [f64; 9], diagonal: f64) -> Result<Alignment> {
        if !rotation.iter().all(|value| value.is_finite())
            || !diagonal.is_finite()
            || diagonal <= 0.0
        {
            return Err(Error::InvalidAlignment);
        }
        let mut rotation = Matrix3::from_row_slice(&rotation);
        let mut offset = Vector3::zeros();
        let as_vector = |point: Point| Vector3::new(point[0], point[1], point[2]);
        let move_points = |rotation: &Matrix3<f64>, offset: &Vector3<f64>| {
            points
                .iter()
                .map(|&point| {
                    let value = rotation.transpose() * as_vector(point) + offset;
                    [value[0], value[1], value[2]]
                })
                .collect::<Vec<_>>()
        };
        let mut moved = move_points(&rotation, &offset);
        let mut closest = self.closest(&moved, MAX_WORK)?;
        let initial = closest.iter().map(|closest| closest.distance).sum::<f64>()
            / closest.len() as f64
            / diagonal;
        let mut best = (initial, rotation, offset, 0.0);
        for _ in 0..8 {
            let center_a = moved
                .iter()
                .fold(Vector3::zeros(), |sum, &point| sum + as_vector(point))
                / moved.len() as f64;
            let center_b = closest.iter().fold(Vector3::zeros(), |sum, closest| {
                sum + as_vector(closest.point)
            }) / closest.len() as f64;
            let covariance =
                moved
                    .iter()
                    .zip(&closest)
                    .fold(Matrix3::zeros(), |sum, (&point, closest)| {
                        sum + (as_vector(point) - center_a)
                            * (as_vector(closest.point) - center_b).transpose()
                    });
            let decomposition = covariance.svd(true, true);
            let u = decomposition.u.ok_or(Error::AlignmentFailed)?;
            let v_t = decomposition.v_t.ok_or(Error::AlignmentFailed)?;
            let parity = (u * v_t).determinant();
            let correction = u * Matrix3::from_diagonal(&Vector3::new(1.0, 1.0, parity)) * v_t;
            rotation *= correction;
            offset = correction.transpose() * (offset - center_a) + center_b;
            moved = move_points(&rotation, &offset);
            closest = self.closest(&moved, MAX_WORK)?;
            let error = closest.iter().map(|closest| closest.distance).sum::<f64>()
                / closest.len() as f64
                / diagonal;
            if error >= best.0 - 1e-9 {
                break;
            }
            best = (error, rotation, offset, error);
        }
        let matrix = std::array::from_fn(|row| std::array::from_fn(|column| best.1[(row, column)]));
        Ok(Alignment {
            rotation: matrix,
            translation: [best.2[0], best.2[1], best.2[2]],
            convergence: best.3,
            error: best.0,
        })
    }
}

fn triangle_closest(p: Point, [a, b, c]: Triangle) -> (f64, Point) {
    let ab = sub(b, a);
    let ac = sub(c, a);
    let normal = cross(ab, ac);
    let square = dot(normal, normal);
    let height = dot(sub(p, a), normal) / square;
    let projected = std::array::from_fn(|i| p[i] - height * normal[i]);
    let ap = sub(projected, a);
    let d00 = dot(ab, ab);
    let d01 = dot(ab, ac);
    let d11 = dot(ac, ac);
    let d20 = dot(ap, ab);
    let d21 = dot(ap, ac);
    let v = (d11 * d20 - d01 * d21) / square;
    let w = (d00 * d21 - d01 * d20) / square;
    let mut distance = if v >= 0.0 && w >= 0.0 && v + w <= 1.0 {
        height * height * square
    } else {
        f64::INFINITY
    };
    let mut chosen = projected;
    for (first, second) in [(a, b), (b, c), (c, a)] {
        let edge = sub(second, first);
        let parameter = (dot(sub(p, first), edge) / dot(edge, edge)).clamp(0.0, 1.0);
        let closest = std::array::from_fn(|i| first[i] + parameter * edge[i]);
        let delta = sub(p, closest);
        let squared = dot(delta, delta);
        if squared < distance {
            distance = squared;
            chosen = closest;
        }
    }
    (distance, chosen)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn triangle() -> Vec<Triangle> {
        vec![[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]]
    }

    #[test]
    fn returns_the_closest_surface_point() {
        let tree = SurfaceTree::try_new(triangle()).unwrap();

        let result = tree.closest(&[[0.25, 0.25, 1.0]], 1).unwrap();

        assert_eq!(
            result,
            vec![ClosestPoint {
                distance: 1.0,
                point: [0.25, 0.25, 0.0],
            }]
        );
    }

    #[test]
    fn rejects_an_exhausted_work_budget() {
        let tree = SurfaceTree::try_new(triangle()).unwrap();

        assert_eq!(
            tree.closest(&[[0.25, 0.25, 1.0]], 0),
            Err(Error::InvalidProximityBudget)
        );
    }
}
