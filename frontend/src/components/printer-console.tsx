import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getJson, sendJson } from "@/lib/api/request";
import { currentLocale, uiText } from "@/lib/locale";
import { getSessionVersion } from "@/lib/session-transport";

interface ConsoleEntry {
  type: "command" | "response";
  time: number;
  message: string;
}

export function PrinterConsole({
  printerId,
  live,
  busy,
}: {
  printerId: number;
  live: boolean;
  busy: boolean;
}) {
  const base = `/api/v1/printers/${printerId}`;
  const scope = ["printer-console", getSessionVersion(), printerId];
  const input = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState("");
  const history = useQuery({
    queryKey: [...scope, "history"],
    queryFn: ({ signal }) => getJson<ConsoleEntry[]>(base + "/console", { signal }),
    enabled: live,
    refetchInterval: live ? 3000 : false,
    retry: false,
  });
  const macros = useQuery({
    queryKey: [...scope, "macros"],
    queryFn: ({ signal }) => getJson<string[]>(base + "/macros", { signal }),
    enabled: live,
    retry: false,
    staleTime: 30000,
  });
  const send = useMutation({
    mutationFn: (command: string) => sendJson<void>(base + "/console", "POST", { command }),
    retry: false,
    onSuccess: (_, command) => setDraft((current) => (current.trim() === command ? "" : current)),
    onSettled: () => {
      void history.refetch();
    },
  });
  function prepare(command: string) {
    setDraft(command);
    send.reset();
    input.current?.focus();
  }
  return (
    <>
      <section className="printer-desk-section" aria-label={uiText("Console")}>
        <div className="printer-desk-heading">
          <h2>{uiText("Console")}</h2>
        </div>
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (live && !busy && !send.isPending && draft.trim()) send.mutate(draft.trim());
          }}
        >
          <Input
            ref={input}
            aria-label={uiText("G-code command")}
            placeholder={uiText("G-code command")}
            className="min-w-0 flex-1 font-mono"
            disabled={!live}
            autoComplete="off"
            spellCheck={false}
            maxLength={2048}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              if (!send.isPending) send.reset();
            }}
          />
          <Button
            type="submit"
            variant="outline"
            size="sm"
            disabled={!live || busy || send.isPending || !draft.trim()}
          >
            {uiText("Send")}
          </Button>
        </form>
        {send.isError && (
          <p role="alert" className="text-xs text-destructive">
            {uiText("Command unconfirmed. Check the console before sending again.")}
          </p>
        )}
        {send.isSuccess && (
          <p role="status" className="text-xs text-muted-foreground">
            {uiText("Command acknowledged")}
          </p>
        )}
        {!live ? null : history.isError ? (
          <p role="alert">{uiText("Console history unavailable")}</p>
        ) : !history.data?.length ? (
          <p className="text-xs text-muted-foreground">
            {uiText(history.isPending ? "Loading…" : "No console messages yet")}
          </p>
        ) : (
          <ol
            aria-label={uiText("Recent console messages")}
            tabIndex={0}
            className="max-h-56 overflow-auto rounded border bg-muted/30 p-2 font-mono text-xs"
          >
            {history.data
              .slice()
              .reverse()
              .map((entry, index) => (
                <li key={`${entry.time}-${index}`} className="flex items-baseline gap-2 py-1">
                  <time className="shrink-0 text-muted-foreground">
                    {new Date(entry.time * 1000).toLocaleTimeString(currentLocale())}
                  </time>
                  {entry.type === "command" ? (
                    <button
                      type="button"
                      disabled={send.isPending}
                      className="min-w-0 wrap-anywhere text-left font-semibold hover:underline"
                      onClick={() => prepare(entry.message)}
                    >
                      {entry.message}
                    </button>
                  ) : (
                    <span className="min-w-0 whitespace-pre-wrap wrap-anywhere">
                      {entry.message}
                    </span>
                  )}
                </li>
              ))}
          </ol>
        )}
      </section>
      <section className="printer-desk-section" aria-label={uiText("Macros")}>
        <div className="printer-desk-heading">
          <h2>{uiText("Macros")}</h2>
          <Button
            variant="ghost"
            size="xs"
            disabled={!live || macros.isFetching}
            onClick={() => void macros.refetch()}
          >
            {uiText("Refresh macros")}
          </Button>
        </div>
        {!live ? null : macros.isError ? (
          <p role="alert">{uiText("Macros unavailable")}</p>
        ) : macros.data?.length ? (
          <>
            <p className="text-xs text-muted-foreground">
              {uiText("Select a macro to edit its parameters in the console.")}
            </p>
            <div className="flex flex-wrap gap-2">
              {macros.data.map((name) => (
                <Button
                  key={name}
                  type="button"
                  variant="outline"
                  size="xs"
                  disabled={send.isPending}
                  onClick={() => prepare(name)}
                >
                  {name}
                </Button>
              ))}
            </div>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">
            {uiText(macros.isPending ? "Loading…" : "No loaded macros")}
          </p>
        )}
      </section>
    </>
  );
}
