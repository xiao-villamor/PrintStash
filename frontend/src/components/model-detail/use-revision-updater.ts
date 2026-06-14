"use client";

import { useState } from "react";

import { deleteFileRevision, updateFileRevision } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { FileRead, FileRevisionUpdate, ModelRead } from "@/types";

/**
 * Shared revision mutation: auth gating, per-file saving indicator, toasts.
 * Used by the Overview card quick actions and the Revisions tab editor.
 */
export function useRevisionUpdater(
  modelId: number,
  onModel: (model: ModelRead) => void,
) {
  const auth = useRequireAuth();
  const [saving, setSaving] = useState<number | null>(null);

  async function update(
    file: FileRead,
    patch: FileRevisionUpdate,
  ): Promise<boolean> {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return false;
    }
    setSaving(file.id);
    try {
      onModel(await updateFileRevision(modelId, file.id, patch));
      toast.success("Revision updated");
      return true;
    } catch (e) {
      toast.error(e);
      return false;
    } finally {
      setSaving(null);
    }
  }

  async function remove(file: FileRead): Promise<boolean> {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return false;
    }
    setSaving(file.id);
    try {
      onModel(await deleteFileRevision(modelId, file.id));
      toast.success("Revision deleted");
      return true;
    } catch (e) {
      toast.error(e);
      return false;
    } finally {
      setSaving(null);
    }
  }

  return { auth, saving, update, remove };
}
