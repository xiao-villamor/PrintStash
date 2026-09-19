use crate::{Error, Result};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EvidenceClass {
    IdenticalGeometry,
    Rescaled,
    Mirrored,
    RescaledMirrored,
    Remeshed,
    Repaired,
    SimilarShape,
}

impl EvidenceClass {
    pub const fn code(self) -> &'static str {
        match self {
            Self::IdenticalGeometry => "identical_geometry",
            Self::Rescaled => "rescaled",
            Self::Mirrored => "mirrored",
            Self::RescaledMirrored => "rescaled_mirrored",
            Self::Remeshed => "remeshed",
            Self::Repaired => "repaired",
            Self::SimilarShape => "similar_shape",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VerificationMetrics {
    pub exact: bool,
    pub reflected: bool,
    pub mirror_ambiguous: bool,
    pub factor: f64,
    pub surface_chamfer: f64,
    pub surface_hausdorff: f64,
    pub voxel_iou: Option<f64>,
    pub left_watertight: bool,
    pub right_watertight: bool,
    pub left_euler: i64,
    pub right_euler: i64,
    pub left_faces: usize,
    pub right_faces: usize,
    pub sampled_chamfer: f64,
    pub sampled_hausdorff: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VerificationDecision {
    pub evidence: Option<EvidenceClass>,
    pub confidence: f64,
}

pub fn verification_decision(metrics: VerificationMetrics) -> Result<VerificationDecision> {
    if ![
        metrics.factor,
        metrics.surface_chamfer,
        metrics.surface_hausdorff,
        metrics.sampled_chamfer,
        metrics.sampled_hausdorff,
    ]
    .iter()
    .all(|value| value.is_finite())
        || metrics
            .voxel_iou
            .is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value))
    {
        return Err(Error::InvalidVerificationMetrics);
    }
    let scaled = (metrics.factor - 1.0).abs() > 1e-6;
    if metrics.exact {
        if metrics.reflected && metrics.mirror_ambiguous {
            return Ok(VerificationDecision {
                evidence: Some(EvidenceClass::Remeshed),
                confidence: 0.99,
            });
        }
        let evidence = if metrics.reflected {
            if scaled {
                EvidenceClass::RescaledMirrored
            } else {
                EvidenceClass::Mirrored
            }
        } else if scaled {
            EvidenceClass::Rescaled
        } else {
            EvidenceClass::IdenticalGeometry
        };
        return Ok(VerificationDecision {
            evidence: Some(evidence),
            confidence: 1.0,
        });
    }
    if metrics.surface_chamfer < 0.004
        && metrics.surface_hausdorff < 0.025
        && metrics.voxel_iou.is_some_and(|value| value >= 0.94)
    {
        let evidence = if metrics.left_watertight != metrics.right_watertight
            || metrics.left_euler != metrics.right_euler
        {
            EvidenceClass::Repaired
        } else if metrics.left_faces != metrics.right_faces {
            EvidenceClass::Remeshed
        } else {
            EvidenceClass::SimilarShape
        };
        return Ok(VerificationDecision {
            evidence: Some(evidence),
            confidence: (0.8 + 0.19 * metrics.voxel_iou.expect("checked above")).min(0.99),
        });
    }
    if metrics.sampled_chamfer < 0.03
        && metrics.sampled_hausdorff < 0.08
        && metrics.voxel_iou.is_some_and(|value| value > 0.9)
    {
        return Ok(VerificationDecision {
            evidence: Some(EvidenceClass::SimilarShape),
            confidence: metrics.voxel_iou.expect("checked above").min(0.95),
        });
    }
    Ok(VerificationDecision {
        evidence: None,
        confidence: 0.0,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn metrics() -> VerificationMetrics {
        VerificationMetrics {
            exact: true,
            reflected: true,
            mirror_ambiguous: false,
            factor: 2.0,
            surface_chamfer: 0.0,
            surface_hausdorff: 0.0,
            voxel_iou: Some(1.0),
            left_watertight: true,
            right_watertight: true,
            left_euler: 2,
            right_euler: 2,
            left_faces: 4,
            right_faces: 4,
            sampled_chamfer: 0.0,
            sampled_hausdorff: 0.0,
        }
    }

    #[test]
    fn exact_reflection_keeps_the_mirror_class() {
        assert_eq!(
            verification_decision(metrics()).unwrap(),
            VerificationDecision {
                evidence: Some(EvidenceClass::RescaledMirrored),
                confidence: 1.0,
            }
        );
    }

    #[test]
    fn rejects_nonfinite_metrics() {
        let mut input = metrics();
        input.factor = f64::NAN;
        assert_eq!(
            verification_decision(input),
            Err(Error::InvalidVerificationMetrics)
        );
    }
}
