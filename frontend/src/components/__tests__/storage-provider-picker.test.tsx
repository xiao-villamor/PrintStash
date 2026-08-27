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
  provider("s3", "Amazon S3", "s3_compatible"),
  provider("webdav", "WebDAV", "nextcloud_webdav", {
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

    const tier = screen.getByText("Expected: verified");
    const field = screen.getByLabelText("Data directory");
    expect(tier.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
});
