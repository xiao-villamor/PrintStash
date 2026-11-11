/*
 * The storage payload and error wording both first-run flows share.
 *
 * Browser registration and the owner provisioned from VAULT_SETUP_ADMIN_* send the
 * same storage choice to different endpoints. A blank field has to reach the server
 * as absent, not as an empty string, or it overrides a deployment default with
 * nothing. And a refused setup step has to say what to change: "try again" on a
 * populated directory is advice that can never work.
 */
import { describe, expect, it } from "vitest";

import { uiText, type MessageKey } from "@/lib/locale";
import { setupErrorFields, setupErrorMessage, setupStorageBody } from "@/lib/setup-storage";

describe("setupStorageBody", () => {
  it("names the provider in both places the server reads it", () => {
    expect(setupStorageBody("local", {})).toEqual({
      storage_provider: "local",
      storage_provider_config: { provider: "local" },
    });
  });

  it("omits a blank field so the server applies its default", () => {
    const body = setupStorageBody("local", { data_dir: "", thumb_dir: "/data/thumbs" });

    expect(body.storage_provider_config).toEqual({
      provider: "local",
      thumb_dir: "/data/thumbs",
    });
  });

  it("omits the stored-secret marker", () => {
    const body = setupStorageBody("sftp", { host: "nas", secret_fields_set: ["password"] });

    expect(body.storage_provider_config).toEqual({ provider: "sftp", host: "nas" });
  });
});

describe("setupErrorMessage", () => {
  const english = (key: MessageKey) => uiText(key);

  it.each<{ label: string; code: string; key: MessageKey }>([
    { label: "an existing owner", code: "users_already_exist", key: "setup.exists" },
    { label: "a completed setup", code: "already_configured", key: "setup.exists" },
    { label: "a populated directory", code: "data_dir_not_empty", key: "setup.populated" },
    { label: "an expired session", code: "setup_session_expired", key: "setup.session" },
    { label: "a refused origin", code: "setup_origin_not_allowed", key: "setup.origin" },
    { label: "disabled registration", code: "setup_disabled", key: "setup.disabled" },
    {
      label: "unreachable remote storage",
      code: "setup_remote_storage_unavailable",
      key: "setup.remoteFailure",
    },
    { label: "an unwritable directory", code: "thumb_dir_not_writable", key: "setup.paths" },
  ])("explains $label", ({ code, key }) => {
    expect(setupErrorMessage(new Error(`[400] ${code}`), english)).toBe(uiText(key));
  });

  it("falls back to the generic failure for an unknown code", () => {
    expect(setupErrorMessage(new Error("[500] 500"), english)).toBe(uiText("setup.failed"));
  });
});

describe("setupErrorFields", () => {
  it("marks only the folder the failure names", () => {
    expect(setupErrorFields(new Error("thumb_dir_not_writable"))).toEqual(["thumb_dir"]);
  });

  it("marks both folders for a path failure that names neither", () => {
    expect(setupErrorFields(new Error("storage_paths_overlap"))).toEqual(["data_dir", "thumb_dir"]);
  });

  it("marks no folder for a session failure", () => {
    expect(setupErrorFields(new Error("setup_session_expired"))).toEqual([]);
  });

  it("marks no folder for a remote storage failure", () => {
    expect(setupErrorFields(new Error("setup_remote_storage_unreachable"))).toEqual([]);
  });
});
