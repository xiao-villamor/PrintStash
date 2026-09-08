/** Storage forms may omit irrelevant fields without hiding editable connection settings. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StorageProviderFields } from "@/components/storage-provider-fields";
import type { StorageProvider } from "@/types";

const provider: StorageProvider = {
  id: "local",
  label: "Local disk",
  category: "this_machine",
  description: "Local directories",
  expected_tier: "verified",
  expected_tier_note: "Local storage",
  consequences: [],
  documentation_url: "/docs/storage-providers.md#local",
  available: true,
  selectable: true,
  fields: [
    {
      name: "root",
      label: "Root",
      help: "Dedicated prefix",
      input_type: "path",
      required: false,
      secret: false,
    },
    {
      name: "data_dir",
      label: "Data directory",
      help: "Model files",
      input_type: "path",
      required: true,
      secret: false,
    },
  ],
};

describe("StorageProviderFields", () => {
  it("omits fields excluded from the current workflow", () => {
    render(
      <StorageProviderFields
        provider={provider}
        values={{ root: "vault-data", data_dir: "/data/files" }}
        omitFields={["root"]}
        onChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );
    expect(screen.queryByLabelText(/Root/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Data directory")).toHaveValue("/data/files");
  });
  it("shows the complete form by default", () => {
    render(
      <StorageProviderFields
        provider={provider}
        values={{ root: "vault-data" }}
        onChange={vi.fn<(name: string, value: string | number) => void>()}
      />,
    );
    expect(screen.getByLabelText(/Root/)).toHaveValue("vault-data");
  });
});
