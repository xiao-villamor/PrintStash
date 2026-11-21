import type { BrowserCaptureMessage } from "./capture-adapter.ts";

type Candidate = BrowserCaptureMessage["candidates"][number];

export type CaptureSelection =
  | { kind: "selected"; files: readonly [Candidate, ...Candidate[]] }
  | { kind: "ambiguous"; message: string }
  | { kind: "empty" };

const modelFile = /\.(?:stl|3mf|obj|step|stp|dxf|glb|gltf|ply|3dm|fbx)$/i;
const printFile = /\.(?:gcode|gco|g|bgcode|ctb|goo)$/i;
const supplementalFile = /\.(?:pdf|txt|md|jpe?g|png|gif|webp)$/i;

export function defaultCaptureSelection(
  draft: BrowserCaptureMessage,
  pageUrl: string,
): CaptureSelection {
  const [first, ...others] = draft.candidates;
  if (!first) return { kind: "empty" };
  if (!others.length) return { kind: "selected", files: [first] };

  const page = new URL(pageUrl);
  const profile =
    page.searchParams.get("profileId") ??
    page.searchParams.get("printFileId") ??
    page.searchParams.get("fileId") ??
    page.hash.match(/^#(?:profileId|printFileId|printFile|fileId|file)[-=:](\w+)$/i)?.[1];
  if (profile) {
    const named = draft.candidates.find(
      (file) => file.id === profile || file.id.endsWith(`:${profile}`),
    );
    if (named) return { kind: "selected", files: [named] };
    return {
      kind: "ambiguous",
      message: "Choose a file. The file named by this page is unavailable.",
    };
  }

  if (draft.source.provider === "makerworld" || draft.source.provider === "crealitycloud") {
    const [packageFile, ...otherPackages] = draft.candidates.filter((file) =>
      /\.3mf$/i.test(file.filename),
    );
    if (packageFile && !otherPackages.length) return { kind: "selected", files: [packageFile] };
    return {
      kind: "ambiguous",
      message: "Choose a package. This page lists several print options.",
    };
  }

  const [model, ...models] = draft.candidates.filter((file) => modelFile.test(file.filename));
  if (model) return { kind: "selected", files: [model, ...models] };

  const [print, ...otherPrints] = draft.candidates.filter((file) => printFile.test(file.filename));
  if (print && !otherPrints.length) return { kind: "selected", files: [print] };

  const [packageFile, ...otherFiles] = draft.candidates.filter(
    (file) => !supplementalFile.test(file.filename),
  );
  if (packageFile && !otherFiles.length) return { kind: "selected", files: [packageFile] };
  return {
    kind: "ambiguous",
    message: "Choose which files to import. This page has no default selection.",
  };
}
