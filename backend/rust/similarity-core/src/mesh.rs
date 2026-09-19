use crate::{Error, Result};

pub type Point = [f64; 3];
pub type Face = [usize; 3];
pub type Triangle = [Point; 3];

const MAX_FACES: usize = 2_000_000;
const MAX_VERTICES: usize = 6_000_000;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Transform {
    pub rotation: [f64; 9],
    pub scale: f64,
    pub translation: Point,
}

impl Transform {
    pub fn validate(self, tolerance: f64) -> Result<Self> {
        if !self.rotation.iter().all(|value| value.is_finite())
            || !self.translation.iter().all(|value| value.is_finite())
            || !self.scale.is_finite()
            || !tolerance.is_finite()
            || tolerance <= 0.0
        {
            return Err(Error::InvalidEquivalenceTransform);
        }
        Ok(self)
    }

    pub(crate) fn apply(self, point: Point) -> Point {
        std::array::from_fn(|axis| {
            (point[0] * self.rotation[axis]
                + point[1] * self.rotation[3 + axis]
                + point[2] * self.rotation[6 + axis])
                * self.scale
                + self.translation[axis]
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Mesh {
    vertices: Vec<Point>,
    faces: Vec<Face>,
}

impl Mesh {
    pub fn try_new(vertices: Vec<Point>, faces: Vec<Face>) -> Result<Self> {
        if vertices.len() > MAX_VERTICES {
            return Err(Error::InvalidVertices);
        }
        if vertices.iter().flatten().any(|value| !value.is_finite()) {
            return Err(Error::NonfiniteGeometry);
        }
        if faces.len() > MAX_FACES || faces.iter().flatten().any(|&index| index >= vertices.len()) {
            return Err(Error::InvalidFaces);
        }
        Ok(Self { vertices, faces })
    }

    pub fn vertices(&self) -> &[Point] {
        &self.vertices
    }

    pub fn faces(&self) -> &[Face] {
        &self.faces
    }

    pub fn triangles(&self) -> impl ExactSizeIterator<Item = Triangle> + '_ {
        self.faces.iter().map(|face| {
            [
                self.vertices[face[0]],
                self.vertices[face[1]],
                self.vertices[face[2]],
            ]
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_faces_outside_the_vertex_buffer() {
        assert_eq!(
            Mesh::try_new(vec![[0.0, 0.0, 0.0]], vec![[0, 1, 0]]),
            Err(Error::InvalidFaces)
        );
    }

    #[test]
    fn rejects_nonfinite_vertices() {
        assert_eq!(
            Mesh::try_new(vec![[f64::NAN, 0.0, 0.0]], Vec::new()),
            Err(Error::NonfiniteGeometry)
        );
    }
}
