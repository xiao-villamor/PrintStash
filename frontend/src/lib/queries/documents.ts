import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  updateDocument,
  uploadDocument,
} from "@/lib/api/documents";
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
  const publish = async (document: DocumentRead) => {
    await cancel();
    client.setQueryData(documentKeys.detail(document.id), document);
    client.setQueriesData<DocumentListItem[]>({ queryKey: documentKeys.lists }, (items) =>
      items?.map((item) => (item.id === document.id ? document : item)),
    );
  };
  const refreshCollections = () => {
    void client.invalidateQueries({ queryKey: ["collections"] });
    void client.resetQueries({ queryKey: ["outliner"] });
  };
  const refreshLists = () => {
    void client.invalidateQueries({ queryKey: documentKeys.lists });
    refreshCollections();
  };
  return {
    create: useMutation({
      mutationFn: (payload: Parameters<typeof createDocument>[0]) => createDocument(payload),
      onMutate: cancel,
      onSuccess: async (document) => {
        await publish(document);
        refreshLists();
      },
    }),
    upload: useMutation({
      mutationFn: ({ file, collectionId }: { file: File; collectionId: number | null }) =>
        uploadDocument(file, collectionId),
      onMutate: cancel,
      onSuccess: async (document) => {
        await publish(document);
        refreshLists();
      },
    }),
    update: useMutation({
      mutationFn: ({
        id,
        payload,
      }: {
        id: number;
        payload: Parameters<typeof updateDocument>[1];
      }) => updateDocument(id, payload),
      onMutate: cancel,
      onSuccess: publish,
    }),
    remove: useMutation({
      mutationFn: (id: number) => deleteDocument(id),
      onMutate: cancel,
      onSuccess: async (_, id) => {
        await cancel();
        client.removeQueries({ queryKey: documentKeys.detail(id) });
        client.setQueriesData<DocumentListItem[]>({ queryKey: documentKeys.lists }, (items) =>
          items?.filter((item) => item.id !== id),
        );
        refreshCollections();
      },
    }),
  };
}
