import type { GcodePlateIdentity } from "@/lib/api/plates";
import type { FilePlate } from "@/types";

/** Match explicit unique slicer plate names and object counts; numbers need not agree. */
export function matchSlicedPlate(
  source: FilePlate,
  sourcePlates: FilePlate[],
  identities: GcodePlateIdentity[],
): GcodePlateIdentity | null {
  if (source.name === null || source.object_count < 1) return null;
  if (sourcePlates.filter((plate) => plate.name === source.name).length !== 1) return null;
  const matches = identities.filter(
    (identity) => identity.name === source.name && identity.object_count === source.object_count,
  );
  return matches.length === 1 ? matches[0]! : null;
}
