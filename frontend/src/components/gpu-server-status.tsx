import type { UseQueryResult } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { gpuAccessible, gpuIdle } from "@/lib/gpu-status";
import type { ComputeCapability, ComputeStatus } from "@/types/system";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm">{children}</dd>
    </div>
  );
}

function hex(value: number): string {
  return `0x${value.toString(16).padStart(4, "0")}`;
}

/** Body of the Server GPU card; the surrounding card owns the title and refresh. */
export function GpuServerStatus({ read }: { read: UseQueryResult<ComputeStatus> }) {
  const { t } = useI18n();
  if (read.isPending) {
    return (
      <div className="space-y-3 p-4 sm:p-5" role="status" aria-label={t("gpu.server.loading")}>
        <Skeleton className="h-5 w-56" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }
  if (read.isError) {
    return (
      <p role="alert" className="p-4 text-sm text-destructive sm:p-5">
        {t("gpu.server.failed")}
      </p>
    );
  }
  const status = read.data;
  const accessible = gpuAccessible(status);
  const blocked = status.capabilities.find((capability) => !capability.available);
  const idle = gpuIdle(status);
  return (
    <div className="space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={accessible ? "success" : "warning"}>
          {accessible ? t("gpu.server.accessible") : t("gpu.server.inaccessible")}
        </Badge>
        {(!accessible || idle) && blocked && (
          <span className="text-xs text-muted-foreground">
            {t(`gpu.reason.${blocked.reason}`)} · {t("gpu.server.cpuNote")}
          </span>
        )}
      </div>

      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        {status.device && (
          <>
            <Fact label={t("gpu.server.device")}>
              <span title={status.device.name}>{status.device.name}</span>
            </Fact>
            <Fact label={t("gpu.server.backend")}>{status.device.backend}</Fact>
            <Fact label={t("gpu.server.driver")}>
              <span title={status.device.driver}>{status.device.driver}</span>
            </Fact>
            <Fact label={t("gpu.server.ids")}>
              {hex(status.device.vendor_id)} / {hex(status.device.device_id)}
            </Fact>
          </>
        )}
        <Fact label={t("gpu.server.mode")}>{status.mode}</Fact>
        <Fact label={t("gpu.server.memory")}>
          {t("gpu.server.memoryValue", {
            reserved: formatBytes(status.reserved_bytes),
            budget: formatBytes(status.budget_bytes),
          })}
        </Fact>
        <Fact label={t("gpu.server.activity")}>
          {t("gpu.server.activityValue", {
            completed: status.completed,
            queued: status.queue_depth,
            fallbacks: status.fallbacks,
          })}
        </Fact>
      </dl>

      <div className="space-y-1.5">
        <p className="text-xs text-muted-foreground">{t("gpu.server.capabilities")}</p>
        <ul className="flex flex-wrap gap-2">
          {status.capabilities.map((capability: ComputeCapability) => (
            <li key={capability.operation}>
              <Badge variant="outline" className="gap-1.5 font-normal">
                {t(`gpu.server.op.${capability.operation}`)}
                <span className="text-muted-foreground">
                  {capability.available
                    ? t("gpu.server.available")
                    : t(`gpu.reason.${capability.reason}`)}
                </span>
              </Badge>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
