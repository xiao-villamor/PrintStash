"use client";

import { Archive, CircleCheck, CircleX, FlaskConical, type LucideIcon } from "lucide-react";
import type { FileRevisionStatus } from "@/types";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import "./revision-status-badge.css";

function statusWord(status: FileRevisionStatus) {
  switch (status) {
    case "known_good":
      return uiText("Ready");
    case "needs_test":
      return uiText("Needs test");
    case "failed":
      return uiText("Failed");
    case "archived":
      return uiText("Archived");
  }
}

function statusIcon(status: FileRevisionStatus): LucideIcon {
  switch (status) {
    case "known_good":
      return CircleCheck;
    case "needs_test":
      return FlaskConical;
    case "failed":
      return CircleX;
    case "archived":
      return Archive;
  }
}

/** The revision's status as one badge, shared by library cards and the inspector. */
export function RevisionStatusBadge({
  status,
  label,
}: {
  status: FileRevisionStatus;
  /** The revision's own label, spoken after the status. */
  label?: string | null;
}) {
  useUiLocale();
  const word = statusWord(status);
  const Icon = statusIcon(status);
  const accessibleLabel = label
    ? uiText("Revision status: {status}; label: {label}", { status: word, label })
    : uiText("Revision status: {status}", { status: word });
  return (
    <span
      data-revision-status={status}
      aria-label={accessibleLabel}
      title={accessibleLabel}
      className="revision-status-badge"
    >
      <Icon aria-hidden className="size-3 shrink-0" />
      {word}
    </span>
  );
}
