import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  updateDocument,
  uploadDocument,
} from "@/lib/api/documents";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
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
  const prepare = async () => {
    const version = getSessionVersion();
    await cancel();
    requireSessionVersion(version);
    return version;
  };
  const assertSession = (version: number | undefined) => {
    if (version === undefined) throw new Error("Document mutation session context is required");
    requireSessionVersion(version);
  };
  const publish = async (document: DocumentRead, version: number | undefined) => {
    await cancel();
    assertSession(version);
    client.setQueryData(documentKeys.detail(document.id), document);
    for (const [key, items] of client.getQueriesData<DocumentListItem[]>({
      queryKey: documentKeys.lists,
    })) {
      assertSession(version);
      if (!items) continue;
      const collection = key[2];
      client.setQueryData(
        key,
        items.flatMap((item) => {
          if (item.id !== document.id) return [item];
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
      mutationFn: (payload: Parameters<typeof createDocument>[0]) => createDocument(payload),
      onMutate: prepare,
      onSuccess: async (document, _, version) => {
        await publish(document, version);
        refreshLists(version);
      },
    }),
    upload: useMutation({
      mutationFn: ({ file, collectionId }: { file: File; collectionId: number | null }) =>
        uploadDocument(file, collectionId),
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
      }: {
        id: number;
        editVersion: number;
        payload: Parameters<typeof updateDocument>[1];
      }) => updateDocument(id, payload, editVersion),
      onMutate: prepare,
      onSuccess: async (document, _, version) => {
        await publish(document, version);
        refreshLists(version);
      },
    }),
    remove: useMutation({
      mutationFn: (id: number) => deleteDocument(id),
      onMutate: prepare,
      onSuccess: async (_, id, version) => {
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
