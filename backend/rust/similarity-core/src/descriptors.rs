//! Bounded descriptors, equivalence checks, and neighborhood operations.
use crate::{Error, Face, Mesh, Point, Result, Transform, Triangle};
use nalgebra::{Matrix3, SymmetricEigen, Vector3};

const MAX_INTERSECTIONS: usize = 2_000_000;
const MAX_FACES: usize = 2_000_000;

fn grid_index(coordinates: [usize; 3], resolution: usize) -> usize {
    (coordinates[0] * resolution + coordinates[1]) * resolution + coordinates[2]
}

fn project(
    triangles: &[[Point; 3]],
    axis: usize,
    resolution: usize,
    fill: bool,
) -> Result<Vec<u8>> {
    let projected = [(axis + 1) % 3, (axis + 2) % 3];
    let mut crossings: Vec<Vec<f64>> = vec![Vec::new(); resolution * resolution];
    let mut total = 0usize;
    for triangle in triangles {
        let low: [isize; 2] = std::array::from_fn(|dimension| {
            let coordinate = projected[dimension];
            let minimum = triangle
                .iter()
                .map(|point| point[coordinate])
                .fold(f64::INFINITY, f64::min);
            (minimum - 0.5).ceil().max(0.0) as isize
        });
        let high: [isize; 2] = std::array::from_fn(|dimension| {
            let coordinate = projected[dimension];
            let maximum = triangle
                .iter()
                .map(|point| point[coordinate])
                .fold(f64::NEG_INFINITY, f64::max);
            (maximum - 0.5).floor().min((resolution - 1) as f64) as isize
        });
        if high[0] < low[0] || high[1] < low[1] {
            continue;
        }
        let a = [
            triangle[0][projected[0]],
            triangle[0][projected[1]],
            triangle[0][axis],
        ];
        let b = [
            triangle[1][projected[0]],
            triangle[1][projected[1]],
            triangle[1][axis],
        ];
        let c = [
            triangle[2][projected[0]],
            triangle[2][projected[1]],
            triangle[2][axis],
        ];
        let denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
        if denominator.abs() <= 1e-12 {
            continue;
        }
        for x in low[0]..=high[0] {
            for y in low[1]..=high[1] {
                let center_x = x as f64 + 0.5;
                let center_y = y as f64 + 0.5;
                let u = ((b[1] - c[1]) * (center_x - c[0]) + (c[0] - b[0]) * (center_y - c[1]))
                    / denominator;
                let v = ((c[1] - a[1]) * (center_x - c[0]) + (a[0] - c[0]) * (center_y - c[1]))
                    / denominator;
                if u < -1e-10 || v < -1e-10 || u + v > 1.0 + 1e-10 {
                    continue;
                }
                total += 1;
                if total > MAX_INTERSECTIONS {
                    return Err(Error::VoxelResourceLimit);
                }
                let z = u * a[2] + v * b[2] + (1.0 - u - v) * c[2];
                crossings[x as usize * resolution + y as usize].push(z);
            }
        }
    }

    let mut grid = vec![0u8; resolution * resolution * resolution];
    for (column, values) in crossings.iter_mut().enumerate() {
        if values.is_empty() {
            continue;
        }
        values.sort_by(f64::total_cmp);
        values.dedup_by(|left, right| (*left - *right).abs() <= 1e-7);
        let x = column / resolution;
        let y = column % resolution;
        if fill {
            let mut events = vec![0u32; resolution + 1];
            for &z in values.iter() {
                let boundary = (z - 0.5).ceil().clamp(0.0, resolution as f64) as usize;
                events[boundary] += 1;
            }
            let mut parity = 0u32;
            for (depth, event) in events.into_iter().take(resolution).enumerate() {
                parity += event;
                if parity % 2 == 1 {
                    let mut coordinates = [0usize; 3];
                    coordinates[projected[0]] = x;
                    coordinates[projected[1]] = y;
                    coordinates[axis] = depth;
                    grid[grid_index(coordinates, resolution)] = 1;
                }
            }
        }
        for &z in values.iter() {
            let depth = z.floor().clamp(0.0, (resolution - 1) as f64) as usize;
            let mut coordinates = [0usize; 3];
            coordinates[projected[0]] = x;
            coordinates[projected[1]] = y;
            coordinates[axis] = depth;
            grid[grid_index(coordinates, resolution)] = 1;
        }
    }
    Ok(grid)
}

fn voxel_grid(
    vertices: &[Point],
    faces: &[Face],
    half_width: f64,
    resolution: usize,
    fill: bool,
) -> Result<Vec<u8>> {
    if !matches!(resolution, 16 | 32 | 64) || !half_width.is_finite() || half_width <= 0.0 {
        return Err(Error::InvalidVoxelRecipe);
    }
    let factor = resolution as f64 / 2.0;
    let triangles: Vec<[Point; 3]> = faces
        .iter()
        .map(|face| {
            std::array::from_fn(|corner| {
                std::array::from_fn(|axis| {
                    (vertices[face[corner]][axis] / half_width + 1.0) * factor
                })
            })
        })
        .collect();
    let mut normal_total = [0.0; 3];
    for triangle in &triangles {
        let first: Point = std::array::from_fn(|i| triangle[1][i] - triangle[0][i]);
        let second: Point = std::array::from_fn(|i| triangle[2][i] - triangle[0][i]);
        let normal = [
            first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0],
        ];
        for axis in 0..3 {
            normal_total[axis] += normal[axis].abs();
        }
    }
    let axis = (1..3).fold(0, |best, candidate| {
        if normal_total[candidate] > normal_total[best] {
            candidate
        } else {
            best
        }
    });
    let mut grid = project(&triangles, axis, resolution, fill)?;
    if !fill {
        for other in 0..3 {
            if other == axis {
                continue;
            }
            for (target, value) in grid
                .iter_mut()
                .zip(project(&triangles, other, resolution, false)?)
            {
                *target |= value;
            }
        }
    }
    Ok(grid)
}

pub fn nearest_neighbors(source: &[Point], target: &[Point]) -> Result<Vec<(f64, usize)>> {
    if source.len() > 5_000 || target.is_empty() || target.len() > 5_000 {
        return Err(Error::InvalidNeighborPoints);
    }
    if source
        .iter()
        .chain(target)
        .flatten()
        .any(|value| !value.is_finite())
    {
        return Err(Error::NonfiniteGeometry);
    }
    Ok(source
        .iter()
        .map(|point| {
            let (index, squared) = target
                .iter()
                .enumerate()
                .map(|(index, candidate)| {
                    let squared = (0..3)
                        .map(|axis| {
                            let delta = point[axis] - candidate[axis];
                            delta * delta
                        })
                        .sum::<f64>();
                    (index, squared)
                })
                .min_by(|left, right| left.1.total_cmp(&right.1))
                .expect("target was checked as non-empty");
            (squared.max(0.0).sqrt(), index)
        })
        .collect())
}

pub fn equivalent_triangles(
    left: &Mesh,
    right: &Mesh,
    transform: Transform,
    tolerance: f64,
) -> Result<bool> {
    let transform = transform.validate(tolerance)?;
    if left.vertices().len() != right.vertices().len() || left.faces().len() != right.faces().len()
    {
        return Ok(false);
    }
    let target: Vec<Point> = right
        .vertices()
        .iter()
        .map(|&point| transform.apply(point))
        .collect();
    for offset in [0.0, 0.5] {
        let mut order_left: Vec<usize> = (0..left.vertices().len()).collect();
        let mut order_right: Vec<usize> = (0..target.len()).collect();
        let key = |point: Point| -> Result<[i64; 3]> {
            let mut output = [0i64; 3];
            for axis in 0..3 {
                let value = (point[axis] / tolerance + offset).floor();
                if value < i64::MIN as f64 || value > i64::MAX as f64 {
                    return Err(Error::NumericRange);
                }
                output[axis] = value as i64;
            }
            Ok(output)
        };
        let left_keys: Vec<[i64; 3]> = left
            .vertices()
            .iter()
            .map(|&point| key(point))
            .collect::<Result<_>>()?;
        let right_keys: Vec<[i64; 3]> = target
            .iter()
            .map(|&point| key(point))
            .collect::<Result<_>>()?;
        order_left.sort_by_key(|&index| left_keys[index]);
        order_right.sort_by_key(|&index| right_keys[index]);
        if order_left
            .iter()
            .zip(&order_right)
            .any(|(&left_index, &right_index)| {
                (0..3)
                    .map(|axis| {
                        let delta = left.vertices()[left_index][axis] - target[right_index][axis];
                        delta * delta
                    })
                    .sum::<f64>()
                    .sqrt()
                    > tolerance
            })
        {
            continue;
        }
        let mut mapping = vec![0usize; target.len()];
        for (&left_index, &right_index) in order_left.iter().zip(&order_right) {
            mapping[right_index] = left_index;
        }
        let mut left_faces = left.faces().to_vec();
        let mut right_faces: Vec<Face> = right
            .faces()
            .iter()
            .map(|face| std::array::from_fn(|corner| mapping[face[corner]]))
            .collect();
        for face in left_faces.iter_mut().chain(&mut right_faces) {
            face.sort_unstable();
        }
        left_faces.sort_unstable();
        right_faces.sort_unstable();
        if left_faces == right_faces {
            return Ok(true);
        }
    }
    Ok(false)
}

pub fn voxelize(mesh: &Mesh, half_width: f64, resolution: usize, fill: bool) -> Result<Vec<u8>> {
    voxel_grid(mesh.vertices(), mesh.faces(), half_width, resolution, fill)
}

fn factorial(value: usize) -> f64 {
    (2..=value).fold(1.0, |total, factor| total * factor as f64)
}

fn spectrum(vertices: &[Point], faces: &[Face], fill: bool) -> Result<Vec<f64>> {
    let radius = vertices
        .iter()
        .map(|point| point.iter().map(|value| value * value).sum::<f64>().sqrt())
        .fold(0.0, f64::max)
        * 1.01;
    let occupancy = voxel_grid(vertices, faces, radius, 64, fill)?;
    let mut xyz = Vec::new();
    let mut distance = Vec::new();
    for (index, occupied) in occupancy.into_iter().enumerate() {
        if occupied == 0 {
            continue;
        }
        let x = index / (64 * 64);
        let y = index / 64 % 64;
        let z = index % 64;
        let point = [
            (x as f64 + 0.5) / 32.0 - 1.0,
            (y as f64 + 0.5) / 32.0 - 1.0,
            (z as f64 + 0.5) / 32.0 - 1.0,
        ];
        let radius = point.iter().map(|value| value * value).sum::<f64>().sqrt();
        if radius > 0.0 && radius <= 1.0 {
            xyz.push(point);
            distance.push(radius);
        }
    }
    if xyz.is_empty() {
        return Err(Error::EmptyOccupancy);
    }
    let shells: Vec<usize> = distance
        .iter()
        .map(|value| ((value * 32.0) as usize).min(31))
        .collect();
    let z: Vec<f64> = xyz
        .iter()
        .zip(&distance)
        .map(|(point, radius)| point[2] / radius)
        .collect();
    let phi: Vec<f64> = xyz.iter().map(|point| point[1].atan2(point[0])).collect();
    let mut power = vec![0.0f64; 32 * 17];
    let mut diagonal = vec![1.0f64; xyz.len()];
    let normalization = xyz.len() as f64;
    for order in 0..17 {
        if order > 0 {
            for (value, z) in diagonal.iter_mut().zip(&z) {
                *value *= -((2 * order - 1) as f64) * (1.0 - z * z).max(0.0).sqrt();
            }
        }
        let mut previous = vec![0.0f64; xyz.len()];
        let mut current = diagonal.clone();
        let cosine: Vec<f64> = phi
            .iter()
            .map(|value| (order as f64 * value).cos())
            .collect();
        let sine: Vec<f64> = phi
            .iter()
            .map(|value| (order as f64 * value).sin())
            .collect();
        for degree in order..17 {
            if degree > order {
                let following: Vec<f64> = z
                    .iter()
                    .zip(&current)
                    .zip(&previous)
                    .map(|((z, current), previous)| {
                        ((2 * degree - 1) as f64 * z * current
                            - (degree + order - 1) as f64 * previous)
                            / (degree - order) as f64
                    })
                    .collect();
                previous = current;
                current = following;
            }
            let coefficient = (((2 * degree + 1) as f64 / (4.0 * std::f64::consts::PI))
                * factorial(degree - order)
                / factorial(degree + order))
            .sqrt();
            let mut real = [0.0f64; 32];
            let mut imaginary = [0.0f64; 32];
            for index in 0..xyz.len() {
                real[shells[index]] += current[index] * cosine[index];
                imaginary[shells[index]] += current[index] * sine[index];
            }
            for shell in 0..32 {
                let real = real[shell] * coefficient / normalization;
                let imaginary = imaginary[shell] * coefficient / normalization;
                power[shell * 17 + degree] +=
                    (real * real + imaginary * imaginary) * if order == 0 { 1.0 } else { 2.0 };
            }
        }
    }
    Ok(power.into_iter().map(f64::sqrt).collect())
}

pub fn sh_spectrum(mesh: &Mesh, fill: bool) -> Result<Vec<f64>> {
    spectrum(mesh.vertices(), mesh.faces(), fill)
}

pub fn dct_hash(pixels: &[f64]) -> Result<[u8; 8]> {
    if pixels.len() != 64 * 64 || !pixels.iter().all(|value| value.is_finite()) {
        return Err(Error::InvalidViewImage);
    }
    let mut basis = [[0.0f64; 64]; 8];
    for (frequency, row) in basis.iter_mut().enumerate() {
        for (position, value) in row.iter_mut().enumerate() {
            *value =
                (std::f64::consts::PI / 64.0 * (position as f64 + 0.5) * frequency as f64).cos();
        }
    }
    let mut coefficients = [0.0f64; 64];
    for row_frequency in 0..8 {
        for column_frequency in 0..8 {
            let mut value = 0.0;
            for row in 0..64 {
                for column in 0..64 {
                    value += basis[row_frequency][row]
                        * pixels[row * 64 + column]
                        * basis[column_frequency][column];
                }
            }
            coefficients[row_frequency * 8 + column_frequency] = value;
        }
    }
    let mut tail = coefficients[1..].to_vec();
    tail.sort_by(f64::total_cmp);
    let threshold = tail[tail.len() / 2];
    let mut output = [0u8; 8];
    for (index, value) in coefficients.into_iter().enumerate().skip(1) {
        if value > threshold {
            output[index / 8] |= 1 << (7 - index % 8);
        }
    }
    Ok(output)
}

pub fn volume_inertia_ratios(triangles: &[Triangle]) -> Result<(f64, f64, f64)> {
    if triangles.is_empty() || triangles.len() > MAX_FACES {
        return Err(Error::InvalidInertiaGeometry);
    }
    let mut volume = 0.0;
    let mut centroid_sum = Vector3::zeros();
    let mut second_sum = Matrix3::zeros();
    for triangle in triangles {
        if !triangle.iter().flatten().all(|value| value.is_finite()) {
            return Err(Error::NumericRange);
        }
        let points = triangle.map(|point| Vector3::new(point[0], point[1], point[2]));
        let signed = points[0].dot(&points[1].cross(&points[2])) / 6.0;
        let sum = points[0] + points[1] + points[2];
        volume += signed;
        centroid_sum += sum * signed;
        let mut vertex_products = Matrix3::zeros();
        for point in points {
            vertex_products += point * point.transpose();
        }
        second_sum += (vertex_products + sum * sum.transpose()) * signed;
    }
    if !volume.is_finite() || volume.abs() <= f64::EPSILON {
        return Err(Error::NumericRange);
    }
    let centroid = centroid_sum / (4.0 * volume);
    let covariance = second_sum / (20.0 * volume) - centroid * centroid.transpose();
    let inertia = Matrix3::identity() * covariance.trace() - covariance;
    let mut eigenvalues = SymmetricEigen::new(inertia).eigenvalues.as_slice().to_vec();
    eigenvalues.sort_by(f64::total_cmp);
    let largest = eigenvalues[2];
    if !largest.is_finite() || largest <= 0.0 {
        return Err(Error::NumericRange);
    }
    Ok((eigenvalues[0] / largest, eigenvalues[1] / largest, 1.0))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cube() -> Mesh {
        Mesh::try_new(
            vec![
                [-1.0, -1.0, -1.0],
                [1.0, -1.0, -1.0],
                [1.0, 1.0, -1.0],
                [-1.0, 1.0, -1.0],
                [-1.0, -1.0, 1.0],
                [1.0, -1.0, 1.0],
                [1.0, 1.0, 1.0],
                [-1.0, 1.0, 1.0],
            ],
            vec![
                [0, 2, 1],
                [0, 3, 2],
                [4, 5, 6],
                [4, 6, 7],
                [0, 1, 5],
                [0, 5, 4],
                [1, 2, 6],
                [1, 6, 5],
                [2, 3, 7],
                [2, 7, 6],
                [3, 0, 4],
                [3, 4, 7],
            ],
        )
        .unwrap()
    }

    #[test]
    fn fills_the_center_of_a_closed_cube() {
        let mesh = cube();
        let grid = voxelize(&mesh, 2.0, 64, true).unwrap();
        assert_eq!(grid[grid_index([32, 32, 32], 64)], 1);
        assert_eq!(grid[grid_index([0, 0, 0], 64)], 0);
        assert_eq!(
            grid.iter().filter(|&&value| value != 0).count(),
            32 * 32 * 33
        );
    }

    #[test]
    fn returns_nearest_distance_and_index() {
        assert_eq!(
            nearest_neighbors(&[[0.0, 0.0, 0.0]], &[[2.0, 0.0, 0.0], [1.0, 0.0, 0.0]]).unwrap(),
            vec![(1.0, 1)]
        );
    }

    #[test]
    fn proves_equivalent_triangle_incidence() {
        let mesh = cube();
        assert!(equivalent_triangles(
            &mesh,
            &mesh,
            Transform {
                rotation: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                scale: 1.0,
                translation: [0.0; 3],
            },
            1e-9,
        )
        .unwrap());
    }

    #[test]
    fn rejects_an_invalid_view_image() {
        assert_eq!(dct_hash(&[0.0; 63]), Err(Error::InvalidViewImage));
    }

    #[test]
    fn rejects_excessive_projected_intersections() {
        let mesh = cube();
        let repeated = mesh.faces().iter().copied().cycle().take(12_000).collect();
        let repeated = Mesh::try_new(mesh.vertices().to_vec(), repeated).unwrap();
        assert_eq!(
            voxelize(&repeated, 1.01, 64, true),
            Err(Error::VoxelResourceLimit)
        );
    }
}
