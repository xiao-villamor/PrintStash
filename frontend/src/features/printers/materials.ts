/** Manual material editing uses its existing observation timestamp contract. */
import { useEffect, useRef, useState } from "react";
import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { getPrinterMaterialState, updatePrinterManualMaterialState } from "@/lib/api/printers";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, withSessionRequest } from "@/lib/session-transport";
import type { ManualMaterialStateUpdate, PrinterMaterialStateRead } from "@/types";

export function printerMaterialsOptions(id: number) {
  return queryOptions({
    queryKey: ["printers", id, "material-state"],
    queryFn: ({ signal }) => getPrinterMaterialState(id, { signal }),
    retry: false,
  });
}
export function usePrinterMaterials(id: number) {
  const client = useQueryClient();
  const options = printerMaterialsOptions(id);
  const [session] = useState(getSessionVersion);
  const [retired, setRetired] = useState(false);
  const live = useRef(true);
  const command = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      command.current?.abort();
      setRetired(true);
    });
    return () => {
      live.current = false;
      command.current?.abort();
      release();
    };
  }, []);
  const current = () => live.current && session === getSessionVersion();
  const query = useQuery({ ...options, enabled: !retired });
  async function save(payload: ManualMaterialStateUpdate) {
    if (!current()) throw new DOMException("Material editor was retired", "AbortError");
    if (command.current) throw new Error("A material save is already pending");
    const controller = new AbortController();
    command.current = controller;
    try {
      return await withSessionRequest(async (request) => {
        await client.cancelQueries({ queryKey: options.queryKey, exact: true });
        request.assertCurrent();
        const receipt = await updatePrinterManualMaterialState(id, payload, {
          signal: request.signal,
        });
        request.assertCurrent();
        await client.cancelQueries({ queryKey: options.queryKey, exact: true });
        request.assertCurrent();
        client.setQueryData<PrinterMaterialStateRead>(options.queryKey, (previous) =>
          previous?.updated_at && receipt.updated_at && previous.updated_at > receipt.updated_at
            ? previous
            : receipt,
        );
        return receipt;
      }, controller.signal);
    } finally {
      if (command.current === controller) command.current = null;
    }
  }
  return { query, save, current, retired };
}
