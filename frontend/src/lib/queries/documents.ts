import { acceptsEditingSnapshot } from "@/features/library/editing";
import type { EditingBase } from "@/types/editing";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  updateDocument,
  uploadDocument,
} from "@/lib/api/documents";
import { requireSessionVersion } from "@/lib/session-transport";
import type { DocumentListItem, DocumentRead } from "@/types";

export const documentKeys = {
  all: ["documents"] as const,
  lists: ["documents", "list"] as const,
  list: (collection: string | null) => ["documents", "list", collection] as const,
  detail: (id: number | null) => ["documents", "detail", id] as const,
};

export function useDocuments(collection: string | null) {
  return useQuery({
    queryKey: documentKeys.list(collection),
    queryFn: ({ signal }) => listDocuments(collection, { fresh: true, signal }),
  });
}

export function useDocument(id: number | null) {
  return useQuery({
    queryKey: documentKeys.detail(id),
    queryFn: ({ signal }) => {
      if (id === null) throw new Error("Document id is required");
      return getDocument(id, signal);
    },
    enabled: id !== null,
  });
}

/** Confirmed writes publish only after obsolete reads have been cancelled. */
export function useDocumentMutations() {
  const client = useQueryClient();
  const cancel = () => client.cancelQueries({ queryKey: documentKeys.all });
  const prepare = async ({ session }: { session: number }) => {
    const version = session;
    requireSessionVersion(version);
    await cancel();
    requireSessionVersion(version);
    return version;
  };
  const assertSession = (version: number | undefined) => {
    if (version === undefined) throw new Error("Document mutation session context is required");
    requireSessionVersion(version);
  };
  const publish = async (
    document: DocumentRead,
    version: number | undefined,
    observedEpoch: string | null = null,
  ) => {
    assertSession(version);
    await cancel();
    assertSession(version);
    const current = client.getQueryData<DocumentRead>(documentKeys.detail(document.id));
    if (
      current &&
      current.edit_epoch !== document.edit_epoch &&
      !acceptsEditingSnapshot(current, document, observedEpoch)
    )
      throw new Error("Editing history changed during publication");
    client.setQueryData<DocumentRead>(documentKeys.detail(document.id), (current) =>
      acceptsEditingSnapshot(current, document, observedEpoch) ? document : current,
    );
    for (const [key, items] of client.getQueriesData<DocumentListItem[]>({
      queryKey: documentKeys.lists,
    })) {
      assertSession(version);
      if (!items) continue;
      const collection = key[2];
      client.setQueryData(
        key,
        items.flatMap((item) => {
          if (item.id !== document.id || !acceptsEditingSnapshot(item, document, observedEpoch))
            return [item];
          return collection === null || collection === document.collection ? [document] : [];
        }),
      );
    }
  };
  const refreshCollections = (version: number | undefined) => {
    assertSession(version);
    void client.invalidateQueries({ queryKey: ["collections"] });
    assertSession(version);
    void client.resetQueries({ queryKey: ["outliner"] });
  };
  const refreshLists = (version: number | undefined) => {
    assertSession(version);
    void client.invalidateQueries({ queryKey: documentKeys.lists });
    refreshCollections(version);
  };
  return {
    create: useMutation({
      mutationFn: ({
        payload,
        session,
      }: {
        payload: Parameters<typeof createDocument>[0];
        session: number;
      }) => {
        requireSessionVersion(session);
        return createDocument(payload);
      },
      onMutate: prepare,
      onSuccess: async (document, _, version) => {
        await publish(document, version);
        refreshLists(version);
      },
    }),
    upload: useMutation({
      mutationFn: ({
        file,
        collectionId,
        session,
      }: {
        file: File;
        collectionId: number | null;
        session: number;
      }) => {
        requireSessionVersion(session);
        return uploadDocument(file, collectionId);
      },
      onMutate: prepare,
      onSuccess: async (document, _, version) => {
        await publish(document, version);
        refreshLists(version);
      },
    }),
    update: useMutation({
      mutationFn: ({
        id,
        payload,
        editVersion,
        session,
      }: {
        id: number;
        editVersion: EditingBase;
        session: number;
        payload: Parameters<typeof updateDocument>[1];
      }) => {
        requireSessionVersion(session);
        return updateDocument(id, payload, editVersion);
      },
      onMutate: async (input) => {
        const observedEpoch =
          client.getQueryData<DocumentRead>(documentKeys.detail(input.id))?.edit_epoch ?? null;
        const session = await prepare(input);
        return { session, observedEpoch };
      },
      onSuccess: async (document, _, context) => {
        if (!context) throw new Error("Document mutation context is required");
        await publish(document, context.session, context.observedEpoch);
        refreshLists(context.session);
      },
    }),
    remove: useMutation({
      mutationFn: ({ id, session }: { id: number; session: number }) => {
        requireSessionVersion(session);
        return deleteDocument(id);
      },
      onMutate: prepare,
      onSuccess: async (_, { id }, version) => {
        assertSession(version);
        await cancel();
        assertSession(version);
        client.removeQueries({ queryKey: documentKeys.detail(id) });
        assertSession(version);
        client.setQueriesData<DocumentListItem[]>({ queryKey: documentKeys.lists }, (items) => {
          assertSession(version);
          return items?.filter((item) => item.id !== id);
        });
        refreshCollections(version);
      },
    }),
  };
}
