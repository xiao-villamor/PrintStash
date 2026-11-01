import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { saveModelCover, type ModelCoverCommand } from "@/lib/api/model-covers";
import { getModel } from "@/lib/api/models";
import { ApiError } from "@/lib/errors";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
import type { ModelRead } from "@/types";
import type { PublishModel } from "./model-detail";

type CoverState =
  | { phase: "ready"; base: ModelRead; rejected: boolean }
  | { phase: "saving"; base: ModelRead; command: ModelCoverCommand }
  | {
      phase: "blocked";
      base: ModelRead;
      command: ModelCoverCommand;
      problem: "conflict" | "unconfirmed";
      reviewed: ModelRead | null;
    };

/** The editor owns its original base and never replays an uncertain write automatically. */
export function useModelCoverEditing(model: ModelRead, publish: PublishModel, finish: () => void) {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const [state, setState] = useState<CoverState>({ phase: "ready", base: model, rejected: false });
  const [reviewing, setReviewing] = useState(false);
  const [reviewFailed, setReviewFailed] = useState(false);
  const mounted = useRef(true);
  const working = useRef(false);
  const reviewRequest = useRef<AbortController | null>(null);
  const active = session === currentSession;
  const current = () => mounted.current && session === getSessionVersion();
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      reviewRequest.current?.abort();
    };
  }, []);

  async function execute(command: ModelCoverCommand, base: ModelRead) {
    if (working.current || !current()) return;
    working.current = true;
    setState({ phase: "saving", command, base });
    void client.invalidateQueries({
      queryKey: queryKeys.model(model.id),
      exact: true,
      refetchType: "none",
    });
    try {
      const saved = await saveModelCover(model.id, command, base);
      if (!current()) return;
      if (!(await publish(saved))) throw new Error("Model changed before cover publication");
      if (current()) finish();
    } catch (error) {
      if (!current()) return;
      if (error instanceof ApiError && error.status < 500 && ![409, 412].includes(error.status)) {
        setState({ phase: "ready", base, rejected: true });
      } else {
        setState({
          phase: "blocked",
          command,
          base,
          reviewed: null,
          problem:
            error instanceof ApiError && [409, 412].includes(error.status)
              ? "conflict"
              : "unconfirmed",
        });
      }
    } finally {
      working.current = false;
    }
  }

  async function review() {
    if (state.phase !== "blocked" || reviewing || !current()) return;
    const controller = new AbortController();
    reviewRequest.current = controller;
    setReviewing(true);
    setReviewFailed(false);
    try {
      const reviewed = await getModel(model.id, { signal: controller.signal });
      if (current()) setState({ ...state, reviewed });
    } catch {
      if (current()) setReviewFailed(true);
    } finally {
      if (current()) setReviewing(false);
    }
  }

  return {
    state,
    active,
    reviewing,
    reviewFailed,
    submit: (command: ModelCoverCommand) => {
      if (state.phase === "ready") void execute(command, state.base);
    },
    review,
    applyReviewed: () => {
      if (
        state.phase === "blocked" &&
        state.reviewed &&
        (state.reviewed.effective_role === "edit" || state.reviewed.effective_role === "admin")
      )
        void execute(state.command, state.reviewed);
    },
  };
}
