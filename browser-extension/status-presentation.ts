export type StatusKind = "" | "error" | "success";

export interface StatusPresentation {
  title?: string;
  message: string;
  kind: StatusKind;
  technicalCode?: string;
}

export function buildStatusPresentation({
  message,
  kind,
  diagnosticCode,
  providerCode,
}: {
  message: string;
  kind: StatusKind;
  diagnosticCode?: string;
  providerCode?: string;
}): StatusPresentation {
  const userMessage = message.replace(/^user_file_required:\s*/i, "").trim();
  const technicalCode = diagnosticCode
    ? [diagnosticCode, providerCode].filter(Boolean).join(" · ")
    : undefined;
  const title =
    kind === "error"
      ? providerCode === "challenge"
        ? "Automatic download blocked"
        : "Import needs attention"
      : kind === "success"
        ? "Sent to Pending Imports"
        : undefined;

  return {
    ...(title ? { title } : {}),
    message: userMessage,
    kind,
    ...(technicalCode ? { technicalCode } : {}),
  };
}
