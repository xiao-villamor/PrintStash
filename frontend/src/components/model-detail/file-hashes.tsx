import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type { FileRead } from "@/types";

export function HashValue({
  sha,
  label,
  filename,
}: {
  sha: string;
  label: string;
  filename: string;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(sha);
      setCopied(true);
    } catch {
      setCopied(false);
      toast.error(t("modelHashes.copyFailed"));
    }
  }
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 gap-2 px-2 font-mono text-xs"
        title={sha}
        aria-label={t("modelHashes.copy", { kind: label, filename })}
        onClick={() => void copy()}
        onPointerLeave={() => setCopied(false)}
        onBlur={() => setCopied(false)}
      >
        {sha.slice(0, 7)}
        {copied ? (
          <Check className="h-3.5 w-3.5 text-success" aria-hidden="true" />
        ) : (
          <Copy className="h-3.5 w-3.5" aria-hidden="true" />
        )}
      </Button>
      <span role="status" className="sr-only">
        {copied ? t("modelHashes.copied") : ""}
      </span>
    </>
  );
}

export function FileHashes({ files }: { files: FileRead[] }) {
  const { t } = useI18n();
  if (files.length === 0) return null;
  return (
    <section
      aria-label={t("modelHashes.title")}
      className="space-y-3 border-t border-outline-variant pt-4"
    >
      <h3 className="text-sm font-medium text-on-surface">{t("modelHashes.title")}</h3>
      <div className="divide-y divide-outline-variant">
        {files.map((file) => (
          <div key={file.id} className="min-w-0 space-y-2 py-3 first:pt-0 last:pb-0">
            <p className="break-words text-sm text-on-surface">{file.original_filename}</p>
            <dl className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <dt className="text-xs text-on-surface-variant">{t("modelHashes.file")}</dt>
                <dd>
                  <HashValue
                    key={file.sha256}
                    sha={file.sha256}
                    label={t("modelHashes.file")}
                    filename={file.original_filename}
                  />
                </dd>
              </div>
              {file.geometric_sha256 !== null && (
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <dt
                    className="text-xs text-on-surface-variant"
                    title={t("modelHashes.geometryHelp")}
                  >
                    {t("modelHashes.geometry")}
                  </dt>
                  <dd>
                    <HashValue
                      key={file.geometric_sha256}
                      sha={file.geometric_sha256}
                      label={t("modelHashes.geometry")}
                      filename={file.original_filename}
                    />
                  </dd>
                </div>
              )}
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}
