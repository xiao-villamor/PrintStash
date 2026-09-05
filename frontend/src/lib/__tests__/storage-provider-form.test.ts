/* Shared forms enforce catalogue requirements and keep omitted secrets out of edits. */
import { describe, expect, it } from "vitest";
import {
  providerDefaults,
  providerFormError,
  splitProviderValues,
} from "@/lib/storage-provider-form";
import { storageProviderCatalogue } from "@/test-support/storage-provider-catalogue";

function provider(id: string) {
  const value = storageProviderCatalogue.find((row) => row.id === id);
  if (!value) throw new Error("fixture provider missing");
  return value;
}

describe("storage provider form contracts", () => {
  it("shares remote preset defaults across uses", () => {
    for (const entry of storageProviderCatalogue)
      expect(providerDefaults(entry, "library")).toEqual(providerDefaults(entry, "backup"));
  });
  it.each([
    { password: "", private_key_path: "" },
    { password: "password", private_key_path: "/key" },
  ])("refuses invalid SFTP authentication choices %j", (auth) => {
    const selected = provider("sftp");
    expect(
      providerFormError(
        selected,
        { root: "root", host: "nas", host_key: "pinned", username: "owner", ...auth },
        "backup",
      ),
    ).toBe("Use either a password or a private key path.");
  });
  it("requires a private key when a passphrase is entered", () => {
    expect(
      providerFormError(provider("sftp"), {
        root: "root",
        host: "nas",
        host_key: "pinned",
        username: "owner",
        password: "password",
        passphrase: "extra",
      }),
    ).toBe("A key passphrase requires a private key path.");
  });
  it("requires an explicit Wasabi region", () => {
    expect(
      providerFormError(provider("wasabi"), {
        root: "root",
        bucket: "bucket",
        access_key: "access",
        secret_key: "secret",
        region: "auto",
      }),
    ).toBe("Enter the provider region.");
  });
  it("keeps missing secrets omitted during credential editing", () => {
    const selected = provider("s3");
    const values = { bucket: "bucket", root: "root" };
    expect(providerFormError(selected, values, "backup", ["access_key", "secret_key"])).toBeNull();
    expect(splitProviderValues(selected, values, "backup").secrets).toEqual({});
  });
  it("preserves an explicit optional credential clear", () => {
    expect(splitProviderValues(provider("sftp"), { password: "" }, "backup").secrets).toEqual({
      password: "",
    });
  });
});
