import type { FamilyMemberItem } from "@/types/families";
import type { MultipartModelCandidate } from "@/types/multipart-models";

/** Only explicitly selected live members become new Choices, in selection order. */
export function newFamilyChoices(
  members: readonly FamilyMemberItem[],
  usedIds: ReadonlySet<number>,
): MultipartModelCandidate[] {
  const seen = new Set(usedIds);
  const choices: MultipartModelCandidate[] = [];
  for (const member of members) {
    if (seen.has(member.model_id)) continue;
    seen.add(member.model_id);
    choices.push({
      id: member.model_id,
      name: member.model.name,
      slug: member.model.slug,
      thumbnail_url: member.model.thumbnail_url,
      source_file_count: member.source_file_count,
      gcode_revision_count: member.gcode_revision_count,
      available: true,
    });
  }
  return choices;
}
