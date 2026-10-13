import { useDeferredValue, useState } from "react";
import { useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ScanSearch } from "lucide-react";
import { SimilarityComparison } from "@/components/similarity-comparison";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import {
  similarityCandidateOptions,
  similarityModelOptions,
  similarityTargetsOptions,
  useSimilarityCommands,
} from "@/lib/queries/similarity";
import { getSessionVersion } from "@/lib/session-transport";
import { useI18n } from "@/lib/i18n";
import { parseApiError } from "@/lib/errors";
import { Link } from "@/lib/link";
import { evidenceDescription, evidenceLabel } from "@/lib/similarity";
import { toast } from "@/lib/toast";
import type { SimilarityAction, SimilarityCandidate, SimilarityDecision } from "@/types/similarity";

function CandidateReview({
  candidate,
  readOnly,
  onRetry,
}: {
  candidate: SimilarityCandidate;
  readOnly: boolean;
  onRetry: () => void;
}) {
  const { t, locale } = useI18n();
  const a = useQuery(similarityModelOptions(candidate.model_a_id));
  const b = useQuery(similarityModelOptions(candidate.model_b_id));
  const [confirmation, setConfirmation] = useState<SimilarityDecision | null>(null);
  const [reviewChanged, setReviewChanged] = useState(false);
  const [multipartName, setMultipartName] = useState("");
  const [targetId, setTargetId] = useState<number | undefined>();
  const [targetName, setTargetName] = useState("");
  const [targetSearch, setTargetSearch] = useState("");
  const targetQuery = useDeferredValue(targetSearch.trim());
  const targets = useInfiniteQuery(
    similarityTargetsOptions(targetQuery, confirmation?.action === "create_multipart"),
  );
  const availableTargets = targets.data?.pages.flat() ?? [];
  const { decide: decision } = useSimilarityCommands();
  const submitDecision = (payload: SimilarityDecision) => {
    if (readOnly) return;
    const session = getSessionVersion();
    decision.mutate(
      { id: candidate.id, payload, session },
      {
        onSuccess: () => {
          if (session !== getSessionVersion()) return;
          setConfirmation(null);
          setReviewChanged(false);
          toast.success(t("similarity.decisionSaved"));
        },
        onError: (error) => {
          if (session !== getSessionVersion()) return;
          const code = parseApiError(error).code;
          if (code === "similarity_version_conflict" || code === "similarity_evidence_stale") {
            setConfirmation(null);
            setReviewChanged(true);
          } else toast.error(error);
        },
      },
    );
  };
  const request = (action: SimilarityAction): SimilarityDecision => {
    const proposal: SimilarityDecision = {
      action,
      request_id: crypto.randomUUID(),
      version: candidate.version,
    };
    if (action === "create_multipart")
      proposal.parts = candidate.summary.composition?.map((part) => ({
        name: (part.model_id === candidate.model_a_id
          ? candidate.model_a.name
          : candidate.model_b.name
        ).slice(0, 128),
        model_ids: [part.model_id],
        quantity: part.quantity,
      }));
    return proposal;
  };
  const allowed = (action: SimilarityAction) => candidate.allowed_actions.includes(action);
  const proof = candidate.summary;
  const number = (value: number | null | undefined, suffix = "") =>
    value == null
      ? t("similarity.missingMeasurement")
      : new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(value) + suffix;
  const stateLabels = {
    open: "similarity.open",
    confirmed: "similarity.confirmed",
    rejected: "similarity.rejected",
    later: "similarity.later",
  } as const;
  return (
    <>
      <PageHeader
        title={t("similarity.compareTitle")}
        description={
          <span className="break-words">
            {candidate.model_a.name} · {candidate.model_b.name}
          </span>
        }
      />
      <div className="space-y-5">
        {reviewChanged && (
          <p role="alert" className="text-sm text-warning">
            {t("similarity.reviewChanged")}
          </p>
        )}
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">{evidenceLabel(candidate.evidence_class)}</Badge>
            <Badge variant="outline">{number(candidate.confidence * 100, "%")}</Badge>
            <Badge variant="outline">{t(stateLabels[candidate.review_state])}</Badge>
            <Badge variant="outline">
              {t(candidate.freshness === "current" ? "similarity.current" : "similarity.stale")}
            </Badge>
          </div>
          <p className="text-sm">{evidenceDescription(candidate)}</p>
          {candidate.resolution_kind && (
            <p className="text-sm text-muted-foreground">
              {t(
                candidate.resolution_kind === "evidence_only"
                  ? "similarity.evidenceResolution"
                  : "similarity.multipartResolution",
              )}
            </p>
          )}
          {candidate.reconsidered_candidate_id && (
            <p className="text-sm text-muted-foreground">{t("similarity.newAlgorithm")}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {allowed("confirm_evidence") && candidate.review_state !== "confirmed" && (
            <Button
              disabled={decision.isPending || readOnly}
              onClick={() => setConfirmation(request("confirm_evidence"))}
            >
              {t("similarity.confirmEvidence")}
            </Button>
          )}
          {allowed("create_multipart") && (
            <Button
              variant="outline"
              disabled={decision.isPending || readOnly}
              onClick={() => {
                setTargetId(undefined);
                setTargetSearch("");
                setMultipartName(
                  `${candidate.model_a.name} / ${candidate.model_b.name}`.slice(0, 255),
                );
                setConfirmation(request("create_multipart"));
              }}
            >
              {t("similarity.createMultipart")}
            </Button>
          )}
          {allowed("reject") && candidate.review_state !== "rejected" && (
            <Button
              variant="outline"
              disabled={decision.isPending || readOnly}
              onClick={() => submitDecision(request("reject"))}
            >
              {t("similarity.reject")}
            </Button>
          )}
          {allowed("later") && candidate.review_state !== "later" && (
            <Button
              variant="outline"
              disabled={decision.isPending || readOnly}
              onClick={() => submitDecision(request("later"))}
            >
              {t("similarity.saveLater")}
            </Button>
          )}
          {allowed("reopen") && candidate.review_state !== "open" && (
            <Button
              variant="ghost"
              disabled={decision.isPending || readOnly}
              onClick={() => submitDecision(request("reopen"))}
            >
              {t("similarity.reopen")}
            </Button>
          )}
        </div>
        {[a, b].map(
          (model, index) =>
            model.isError && (
              <div key={index} role="alert" className="text-sm text-destructive">
                {t("similarity.loadError")}{" "}
                <Button variant="outline" onClick={() => void model.refetch()}>
                  {t("similarity.retry")}
                </Button>
              </div>
            ),
        )}
        <SimilarityComparison candidate={candidate} models={[a.data, b.data]} />
        <dl className="grid grid-cols-2 gap-4 rounded-lg border border-border p-4 text-sm sm:grid-cols-4">
          {[
            [t("similarity.chamfer"), number(proof.sampled_chamfer_mm, " mm")],
            [t("similarity.hausdorff"), number(proof.sampled_hausdorff_mm, " mm")],
            [
              t("similarity.iou"),
              number(proof.voxel_iou == null ? null : proof.voxel_iou * 100, "%"),
            ],
            [t("similarity.sampleCount"), number(proof.sample_points)],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="mt-1 font-mono tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        {proof.unavailable?.length ? (
          <p className="text-sm text-muted-foreground">{t("similarity.incompleteMeasurements")}</p>
        ) : null}
        <details className="rounded-lg border border-border p-4 text-sm">
          <summary className="cursor-pointer font-medium">{t("similarity.lineage")}</summary>
          <p className="mt-3 text-muted-foreground">{candidate.algorithm_version}</p>
          <ul className="mt-2 space-y-2">
            {candidate.observations?.map((item) => (
              <li key={item.id} className="break-all font-mono text-xs">
                {item.input_hash_a} → {item.input_hash_b}
              </li>
            ))}
          </ul>
          {candidate.observations_truncated && (
            <p className="mt-2 text-muted-foreground">{t("similarity.lineageTruncated")}</p>
          )}
        </details>
      </div>
      <Modal
        open={confirmation?.action === "confirm_evidence"}
        onClose={() => {
          if (!decision.isPending) setConfirmation(null);
        }}
        title={t("similarity.confirmEvidence")}
        className="max-w-md"
      >
        {readOnly && (
          <div role="alert" className="space-y-2 text-sm text-destructive">
            <p>{t("similarity.loadError")}</p>
            <Button variant="outline" onClick={onRetry}>
              {t("similarity.retry")}
            </Button>
          </div>
        )}
        <p className="text-sm text-muted-foreground">{t("similarity.confirmHelp")}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button
            variant="outline"
            disabled={decision.isPending}
            onClick={() => setConfirmation(null)}
          >
            {t("Cancel")}
          </Button>
          <Button
            loading={decision.isPending}
            disabled={readOnly}
            onClick={() => {
              if (confirmation) submitDecision(confirmation);
            }}
          >
            {t("similarity.confirmEvidence")}
          </Button>
        </div>
      </Modal>
      <Modal
        open={confirmation?.action === "create_multipart"}
        onClose={() => {
          if (!decision.isPending) setConfirmation(null);
        }}
        title={t("similarity.createMultipart")}
        className="max-h-[90dvh] overflow-y-auto"
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (confirmation) {
              const payload: SimilarityDecision = {
                action: confirmation.action,
                request_id: confirmation.request_id,
                version: confirmation.version,
                parts: confirmation.parts,
                ...(targetId === undefined
                  ? { name: multipartName.trim() }
                  : { target_id: targetId }),
              };
              setConfirmation(payload);
              submitDecision(payload);
            }
          }}
        >
          {readOnly && (
            <div role="alert" className="space-y-2 text-sm text-destructive">
              <p>{t("similarity.loadError")}</p>
              <Button type="button" variant="outline" onClick={onRetry}>
                {t("similarity.retry")}
              </Button>
            </div>
          )}
          <p className="text-sm text-muted-foreground">{t("similarity.multipartHelp")}</p>
          {Boolean(proof.unmatched_components) && (
            <p className="text-sm text-warning">{t("similarity.unmatchedParts")}</p>
          )}
          <ul className="divide-y divide-border rounded-md border border-border">
            {confirmation?.parts?.map((part) => (
              <li key={part.model_ids[0]} className="flex justify-between gap-4 p-3 text-sm">
                <span className="line-clamp-2 min-w-0 break-words" title={part.name}>
                  {part.name}
                </span>
                <span className="shrink-0">
                  {t("similarity.quantity")}: {part.quantity}
                </span>
              </li>
            ))}
          </ul>
          <label className="block space-y-1 text-sm">
            {t("similarity.searchMultipart")}
            <Input
              type="search"
              disabled={decision.isPending || readOnly}
              value={targetSearch}
              onChange={(event) => setTargetSearch(event.target.value)}
            />
          </label>
          <label className="block space-y-1 text-sm">
            {t("similarity.multipartTarget")}
            <select
              className="block w-full rounded-md border border-input bg-background p-2 text-sm"
              disabled={decision.isPending || readOnly}
              value={targetId ?? ""}
              onChange={(event) => {
                setTargetId(event.target.value ? Number(event.target.value) : undefined);
                setTargetName(event.target.selectedOptions[0].text);
                setConfirmation(
                  confirmation ? { ...confirmation, request_id: crypto.randomUUID() } : null,
                );
              }}
            >
              <option value="">{t("similarity.multipartNew")}</option>
              {targetId !== undefined &&
                !availableTargets.some((target) => target.id === targetId) && (
                  <option value={targetId}>{targetName}</option>
                )}
              {availableTargets
                .filter(
                  (target) => target.effective_role === "edit" || target.effective_role === "admin",
                )
                .map((target) => (
                  <option key={target.id} value={target.id}>
                    {target.name}
                  </option>
                ))}
            </select>
          </label>
          {targets.hasNextPage && (
            <Button
              type="button"
              variant="ghost"
              disabled={targets.isFetchingNextPage}
              onClick={() => void targets.fetchNextPage()}
            >
              {t("similarity.moreDestinations")}
            </Button>
          )}
          {targets.isError && (
            <p role="status" className="text-sm text-destructive">
              {t("similarity.destinationsFailed")}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() =>
                  void (targets.isFetchNextPageError ? targets.fetchNextPage() : targets.refetch())
                }
              >
                {t("similarity.retry")}
              </Button>
            </p>
          )}
          {targetId !== undefined && (
            <p className="text-sm text-muted-foreground">{t("similarity.multipartAppendHelp")}</p>
          )}
          {targetId === undefined && (
            <label className="block space-y-1 text-sm">
              {t("similarity.multipartName")}
              <Input
                autoFocus
                disabled={decision.isPending || readOnly}
                required
                maxLength={255}
                value={multipartName}
                onChange={(event) => {
                  setMultipartName(event.target.value);
                  setConfirmation(
                    confirmation ? { ...confirmation, request_id: crypto.randomUUID() } : null,
                  );
                }}
              />
            </label>
          )}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={decision.isPending}
              onClick={() => setConfirmation(null)}
            >
              {t("Cancel")}
            </Button>
            <Button
              type="submit"
              disabled={
                decision.isPending ||
                readOnly ||
                (targetId === undefined && !multipartName.trim()) ||
                !confirmation?.parts?.length
              }
            >
              {t(
                targetId === undefined
                  ? "similarity.createMultipart"
                  : "similarity.appendMultipart",
              )}
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
export default function SimilarModelComparisonPage() {
  const { id } = useParams();
  const candidateId = Number(id);
  const validId = Number.isSafeInteger(candidateId) && candidateId > 0;
  const { t } = useI18n();
  const candidate = useQuery(similarityCandidateOptions(candidateId, validId));
  const inaccessible =
    candidate.isError && [401, 403, 404].includes(parseApiError(candidate.error).status);
  return (
    <PageContainer>
      <Link
        href="/library/similar"
        className="mb-4 inline-flex items-center gap-2 text-sm text-primary hover:underline"
      >
        <ArrowLeft className="h-4 w-4" />
        {t("similarity.back")}
      </Link>
      {candidate.isError && candidate.data && !inaccessible && (
        <div role="alert" className="text-sm text-destructive">
          {t("similarity.loadError")}{" "}
          <Button variant="outline" onClick={() => void candidate.refetch()}>
            {t("similarity.retry")}
          </Button>
        </div>
      )}
      {candidate.data && !inaccessible ? (
        <CandidateReview
          key={candidate.data.id}
          candidate={candidate.data}
          readOnly={candidate.isError}
          onRetry={() => void candidate.refetch()}
        />
      ) : candidate.isError || !validId ? (
        <EmptyState
          icon={ScanSearch}
          title={t("similarity.loadError")}
          action={
            validId ? (
              <Button variant="outline" onClick={() => void candidate.refetch()}>
                {t("similarity.retry")}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <p role="status">{t("similarity.loading")}</p>
      )}
    </PageContainer>
  );
}
