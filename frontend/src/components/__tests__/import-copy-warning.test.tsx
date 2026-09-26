/*
 * The one place an operator learns that imports are copying every file.
 *
 * A layout that splits staging from the library (a second volume mapped onto a
 * subfolder of /data is enough) keeps imports working, just slower and needing
 * twice the space for each file. The backend's startup probe is the only thing
 * that notices, so if this warning stays hidden the regression is invisible.
 * Remote Vault storage still uses local staging: its filesystem warning must
 * remain visible without implying that remote storage can use local hard links.
 */

import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImportCopyWarning } from "@/components/import-copy-warning";
import { renderApp } from "@/test-support/render";
import type { StorageHealthRead } from "@/types";

const LOCAL_HEALTH: StorageHealthRead = { ok: true, provider: "local", tier: "verified" };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ImportCopyWarning", () => {
  it("reports unusable staging without promising successful copies", () => {
    renderApp(
      <ImportCopyWarning
        storageHealth={{
          ok: true,
          backend: "s3",
          diagnostics: {
            staging: {
              role: "staging",
              path: "/data/staging",
              fs_kind: "unknown",
              hardlink: false,
              exclusive_create: false,
              directory_fsync: false,
            },
          },
        }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Uploads cannot be staged");
    expect(screen.getByRole("status")).toHaveTextContent("write permissions");
    expect(screen.getByRole("status")).not.toHaveTextContent("Uploads still work");
  });

  it("explains hardlinkless staging with a remote vault", () => {
    renderApp(
      <ImportCopyWarning
        storageHealth={{
          ok: true,
          backend: "s3",
          diagnostics: {
            staging: {
              role: "staging",
              path: "/data/staging",
              fs_kind: "fuse",
              hardlink: false,
              exclusive_create: true,
              directory_fsync: true,
            },
          },
        }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("does not support hard links");
    expect(screen.getByRole("status")).toHaveTextContent("temporary space");
    expect(screen.getByRole("status")).not.toHaveTextContent("are on different mounts");
  });

  it("warns when staging cannot hard-link into the library", () => {
    renderApp(
      <ImportCopyWarning
        storageHealth={{ ...LOCAL_HEALTH, diagnostics: { staged_hardlink: false } }}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Imports are copied, not hard-linked");
  });

  it("links the storage layout guide", () => {
    renderApp(
      <ImportCopyWarning
        storageHealth={{ ...LOCAL_HEALTH, diagnostics: { staged_hardlink: false } }}
      />,
    );

    expect(screen.getByRole("link", { name: "Storage layout guide" })).toHaveAttribute(
      "href",
      "https://github.com/xiao-villamor/PrintStash/blob/main/docs/deployment.md#hard-linked-imports",
    );
  });

  it("stays quiet when imports hard-link", () => {
    renderApp(
      <ImportCopyWarning
        storageHealth={{ ...LOCAL_HEALTH, diagnostics: { staged_hardlink: true } }}
      />,
    );

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("stays quiet for storage that was never probed", () => {
    renderApp(
      <ImportCopyWarning
        storageHealth={{ ok: true, provider: "s3", tier: "verified", diagnostics: {} }}
      />,
    );

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("stays quiet before storage health has loaded", () => {
    renderApp(<ImportCopyWarning storageHealth={null} />);

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
