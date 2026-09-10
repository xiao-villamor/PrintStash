import { uiText } from "@/lib/locale";
import type { MessageKey } from "@/lib/locale";
import type { EvidenceClass, SimilarityCandidate, SimilarityRun } from "@/types/similarity";

const LABELS = {
  identical_geometry: "similarity.identical",
  rescaled: "similarity.rescaled",
  mirrored: "similarity.mirrored",
  rescaled_mirrored: "similarity.rescaledMirrored",
  remeshed: "similarity.remeshed",
  repaired: "similarity.repaired",
  // eslint-disable-next-line anti-slop/no-shape-in-symbol-names -- Published geometric evidence class, not a structural placeholder.
  similar_shape: "similarity.similarShape",
  component_of: "similarity.componentOf",
  plate_of: "similarity.plateOf",
} satisfies Record<EvidenceClass, MessageKey>;
export function evidenceLabel(value: EvidenceClass) {
  return uiText(LABELS[value]);
}
export function evidenceDescription(candidate: SimilarityCandidate): string {
  const proof = candidate.summary;
  if (candidate.freshness === "stale") return uiText("similarity.staleHelp");
  if (proof.mirror_ambiguous) return uiText("similarity.mirrorAmbiguous");
  if (candidate.evidence_class === "plate_of")
    return uiText("similarity.copies", { count: proof.copies ?? 1 });
  if (candidate.evidence_class === "component_of") return uiText("similarity.componentHelp");
  if (candidate.evidence_class === "rescaled" || candidate.evidence_class === "rescaled_mirrored") {
    const scale = proof.scale_factor ?? 1;
    return Math.abs(scale - 25.4) < 0.01 || Math.abs(scale - 1 / 25.4) < 0.0001
      ? uiText("similarity.inchConversion")
      : uiText("similarity.scaleFactor", { factor: scale.toPrecision(4) });
  }
  if (candidate.exact_equivalence && candidate.evidence_class === "identical_geometry")
    return uiText("similarity.exactHelp");
  return uiText("similarity.sampledHelp");
}
export function isSimilarityRunActive(run: SimilarityRun) {
  return run.state === "queued" || run.state === "running" || run.state === "cancelling";
}
