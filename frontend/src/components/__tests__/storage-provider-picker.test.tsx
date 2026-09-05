/**
 * StorageProviderPicker presents the provider catalogue without overstating its guarantees.
 * It keeps category filtering, expected tiers, unavailable-provider reasons, and write-only
 * secret handling visible before an operator commits a storage configuration.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StorageProviderPicker } from "@/components/storage-provider-picker";
import type { ProviderCategory, StorageProvider } from "@/types";

function provider(
  id: string,
  label: string,
  category: ProviderCategory,
  options: Partial<StorageProvider> = {},
): StorageProvider {
  return {
    id,
    label,
    category,
    description: `${label} description`,
    expected_tier: "unguarded",
    expected_tier_note: `${label} expected tier note`,
    consequences: ["Permanent removal needs confirmation."],
    documentation_url: `/docs/storage-providers.md#${id}`,
    available: true,
    selectable: true,
    fields: [],
    ...options,
  };
}

const providers = [
  provider("local", "Local disk", "this_machine", {
    expected_tier: "verified",
    fields: [
      {
        name: "data_dir",
        label: "Data directory",
        help: "Private data directory.",
        input_type: "path",
        required: true,
        secret: false,
      },
    ],
  }),
  provider("s3", "Amazon S3", "s3_compatible", {
    expected_tier: "guarded",
  }),
  provider("webdav", "WebDAV", "nextcloud_webdav", {
    expected_tier: "guarded",
    fields: [
      {
        name: "password",
        label: "Password",
        help: "Write-only password.",
        input_type: "password",
        required: true,
        secret: true,
      },
    ],
  }),
  provider("sftp", "SFTP", "nas_sftp", {
    available: false,
    selectable: false,
    disabled_reason: "Requires the full image",
    fields: [
      {
        name: "host",
        label: "Host",
        help: "SFTP hostname.",
        input_type: "text",
        required: true,
        secret: false,
      },
      {
        name: "host_key",
        label: "Host key",
        help: "OpenSSH known-host entry.",
        input_type: "text",
        required: true,
        secret: false,
      },
    ],
  }),
];

describe("StorageProviderPicker", () => {
  it("filters providers by the selected category", async () => {
    const onProviderChange = vi.fn<(provider: StorageProvider) => void>();
    render(
      <StorageProviderPicker
        providers={providers}
        providerId="local"
        values={{ data_dir: "/data/files" }}
        onProviderChange={onProviderChange}
        onValueChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );

    expect(screen.getByRole("button", { name: /Local disk/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Amazon S3/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "S3-compatible object storage" }));
    expect(screen.getByRole("button", { name: /Amazon S3/ })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /Amazon S3/ }));
    expect(onProviderChange).toHaveBeenCalledWith(expect.objectContaining({ id: "s3" }));
  });

  it("shows the expected tier before provider fields", () => {
    render(
      <StorageProviderPicker
        providers={providers}
        providerId="local"
        values={{ data_dir: "/data/files" }}
        onProviderChange={vi.fn<(provider: StorageProvider) => void>()}
        onValueChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );

    const tier = screen.getByText(/Expected:\s*Verified/);
    const field = screen.getByLabelText("Data directory");
    expect(tier.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows support maturity separately from storage safety", () => {
    render(
      <StorageProviderPicker
        providers={providers}
        providerId="webdav"
        values={{}}
        onProviderChange={vi.fn<(provider: StorageProvider) => void>()}
        onValueChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );

    expect(screen.getByText("Support: Stable")).toBeInTheDocument();
    expect(screen.getByText("Expected: Guarded")).toBeInTheDocument();
  });

  it("labels beta support without changing the expected tier", () => {
    render(
      <StorageProviderPicker
        providers={providers.map((item) =>
          item.id === "webdav" ? { ...item, support_level: "beta" } : item,
        )}
        providerId="webdav"
        values={{}}
        onProviderChange={vi.fn<(provider: StorageProvider) => void>()}
        onValueChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );

    expect(screen.getByText("Support: Beta")).toBeInTheDocument();
    expect(screen.getByText("Expected: Guarded")).toBeInTheDocument();
  });

  it("disables unavailable providers with an actionable reason", async () => {
    render(
      <StorageProviderPicker
        providers={providers}
        providerId="local"
        values={{}}
        onProviderChange={vi.fn<(provider: StorageProvider) => void>()}
        onValueChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "NAS over SFTP" }));
    const unavailable = screen.getByRole("button", { name: /SFTP.*Requires the full image/ });
    expect(unavailable).toBeDisabled();
  });

  it("renders stored secrets as empty write-only fields", () => {
    render(
      <StorageProviderPicker
        providers={providers}
        providerId="webdav"
        values={{ secret_fields_set: ["password"] }}
        onProviderChange={vi.fn<(provider: StorageProvider) => void>()}
        onValueChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );

    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveValue("");
    expect(password).toHaveAttribute("placeholder", "Stored — leave blank to keep");
    expect(screen.getByText(/A value is currently stored/)).toBeVisible();
  });

  it("explains guarded deletion consequences", () => {
    render(
      <StorageProviderPicker
        providers={providers}
        providerId="s3"
        values={{}}
        onProviderChange={vi.fn<(provider: StorageProvider) => void>()}
        onValueChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );

    expect(screen.getByText(/Expected:\s*Guarded/)).toBeVisible();
    expect(screen.getByText("Guarded storage consequences")).toBeVisible();
    expect(screen.getByText(/confirmed catalog removal retains stored bytes/i)).toBeVisible();
    expect(screen.getByText(/automatic physical deletion is unavailable/i)).toBeVisible();
  });

  it("reports the required SFTP host key field", async () => {
    const onValueChange = vi.fn<(name: string, value: string | number) => void>();
    const sftp = providers.find((item) => item.id === "sftp");
    if (!sftp) throw new Error("SFTP fixture missing");
    const selectableSftp: StorageProvider = {
      ...sftp,
      available: true,
      selectable: true,
      disabled_reason: null,
    };

    render(
      <StorageProviderPicker
        providers={providers.map((item) => (item.id === "sftp" ? selectableSftp : item))}
        providerId="sftp"
        values={{ host: "nas.example.test" }}
        onProviderChange={vi.fn<(provider: StorageProvider) => void>()}
        onValueChange={onValueChange}
      />,
    );

    const hostKey = screen.getByLabelText("Host key");
    expect(hostKey).toBeRequired();
    expect(screen.getByRole("button", { name: /^SFTP/ })).not.toBeDisabled();
    await userEvent.type(hostKey, "nas.example.test ssh-ed25519 AAAA");
    expect(onValueChange).toHaveBeenLastCalledWith("host_key", "A");
  });
});
