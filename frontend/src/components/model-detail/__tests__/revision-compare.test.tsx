/** Comparisons separate source/revision scope and highlight changed evidence without turning missing data into zero. */
import { useState } from "react";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { RevisionCompare } from "@/components/model-detail/revision-compare";
import { aFilePlate, aMetadata, aRevision } from "@/test-support/factories";
import { adminSession, json, renderApp } from "@/test-support/render";
import type { ArtifactOutcomeRead, FileRead } from "@/types";

const first = aRevision({
  id: 1,
  version: 1,
  original_filename: "one.gcode",
  metadata: aMetadata({ wall_loops: 0 }),
});
const second = aRevision({
  id: 2,
  version: 2,
  original_filename: "two.gcode",
  metadata: aMetadata({ wall_loops: 1 }),
});
const source = aRevision({
  id: 3,
  file_type: "stl",
  version: 1,
  original_filename: "original.stl",
});
const alternate = aRevision({
  id: 4,
  file_type: "3mf",
  version: 2,
  original_filename: "project.3mf",
});
const recordedZero: ArtifactOutcomeRead = {
  file_id: 1,
  print_count: 0,
  completed_count: 0,
  failed_count: 0,
  cancelled_count: 0,
  success_rate: null,
  average_duration_s: null,
  total_filament_g: null,
  total_cost: null,
};

function Controlled({ files = [first, second, source, alternate] }: { files?: FileRead[] }) {
  const [left, setLeft] = useState(first);
  const [right, setRight] = useState(second);
  return (
    <RevisionCompare
      left={left}
      right={right}
      files={files}
      onSelectLeft={(id) => {
        const file = files.find((file) => file.id === id);
        if (!file) throw new Error("Unknown comparison file");
        setLeft(file);
      }}
      onSelectRight={(id) => {
        const file = files.find((file) => file.id === id);
        if (!file) throw new Error("Unknown comparison file");
        setRight(file);
      }}
    />
  );
}

describe("RevisionCompare", () => {
  it("highlights a changed pair of recorded values", () => {
    renderApp(<RevisionCompare left={first} right={second} />);

    const walls = within(screen.getByRole("row", { name: /^Walls/ }));
    expect(walls.getAllByRole("mark")).toHaveLength(2);
    expect(walls.getByText("0")).toBeVisible();
  });
  it("leaves missing evidence outside the change highlights", () => {
    renderApp(<RevisionCompare left={first} right={{ ...second, metadata: aMetadata() }} />);

    const walls = within(screen.getByRole("row", { name: /^Walls/ }));
    expect(walls.queryAllByRole("mark")).toEqual([]);
    expect(walls.getByText("Not recorded")).toBeVisible();
  });
  it("distinguishes an absent outcome from zero recorded prints", () => {
    renderApp(<RevisionCompare left={first} right={second} outcomes={[recordedZero]} />);

    const count = within(screen.getByRole("row", { name: /^Print count/ }));
    expect(count.getByText("0")).toBeVisible();
    expect(count.getByText("Not recorded")).toBeVisible();
    expect(count.queryAllByRole("mark")).toEqual([]);
  });
  it("switches the visible comparison choices to source files", async () => {
    renderApp(<Controlled />);

    await userEvent.click(
      within(screen.getByRole("group", { name: "Comparison type" })).getByRole("button", {
        name: "Source files",
      }),
    );

    const choices = within(screen.getByRole("group", { name: "Inspecting" }));
    expect(choices.getByRole("button", { name: "STL v1" })).toHaveAttribute("aria-pressed", "true");
    expect(choices.getByRole("button", { name: "3MF v2" })).toBeVisible();
    expect(choices.queryByRole("button", { name: /Rev / })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Source file metadata" })).toBeVisible();
  });
  it("restores a source scope without rendering a stale G-code pair", () => {
    renderApp(
      <RevisionCompare
        left={first}
        right={second}
        files={[first, second, source, alternate]}
        initialMode="source"
      />,
    );

    const table = within(screen.getByRole("table"));
    expect(table.getByRole("columnheader", { name: "STL v1" })).toBeVisible();
    expect(table.getByRole("columnheader", { name: "3MF v2" })).toBeVisible();
    expect(table.queryByRole("columnheader", { name: /Rev / })).not.toBeInTheDocument();
    expect(screen.queryByText("one.gcode")).not.toBeInTheDocument();
  });
  it("offers all files in the mixed comparison scope", async () => {
    renderApp(<Controlled />);

    await userEvent.click(
      within(screen.getByRole("group", { name: "Comparison type" })).getByRole("button", {
        name: "All files",
      }),
    );

    expect(
      within(screen.getByRole("group", { name: "Inspecting" })).getAllByRole("button"),
    ).toHaveLength(4);
  });
  it("reads only the selected 3MF plate count in source comparison", async () => {
    const view = renderApp(
      <RevisionCompare left={source} right={alternate} initialMode="source" />,
      {
        routes: {
          "GET /api/v1/files/4/plates": json({
            state: "ready",
            plates: [aFilePlate(), aFilePlate({ number: 2 })],
          }),
        },
      },
    );

    expect(
      await within(screen.getByRole("row", { name: /^Plates/ })).findByText("2"),
    ).toBeVisible();
    expect(
      view
        .requests()
        .filter(({ url }) => url.endsWith("/plates"))
        .map(({ url }) => url),
    ).toEqual(["/api/v1/files/4/plates"]);
  });
  it.each([
    { state: "queued", values: ["Not recorded", "Not recorded"] },
    { state: "ready", values: ["Not recorded", "0"] },
  ])("distinguishes $state empty plate data from a missing count", async ({ state, values }) => {
    const view = renderApp(
      <RevisionCompare left={source} right={alternate} initialMode="source" />,
      { routes: { "GET /api/v1/files/4/plates": json({ state, plates: [] }) } },
    );

    await waitFor(() =>
      expect(view.requests().some(({ url }) => url.endsWith("/4/plates"))).toBe(true),
    );
    const plates = within(screen.getByRole("row", { name: /^Plates/ }));
    await waitFor(() =>
      expect(plates.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(values),
    );
    expect(plates.queryAllByRole("mark")).toEqual([]);
  });
  it.each([{ user: null, loading: false }, { loading: true }])(
    "waits for a verified private session before reading plates (%j)",
    async (auth) => {
      const view = renderApp(
        <RevisionCompare left={source} right={alternate} initialMode="source" />,
        { auth: adminSession(auth) },
      );

      await act(async () => {
        await Promise.resolve();
      });

      expect(view.requests().filter(({ url }) => url.endsWith("/plates"))).toEqual([]);
      expect(
        within(screen.getByRole("row", { name: /^Plates/ })).getAllByText("Not recorded"),
      ).toHaveLength(2);
    },
  );
  it("explains a scope that has fewer than two files", async () => {
    renderApp(<Controlled files={[first, second, source]} />);

    await userEvent.click(
      within(screen.getByRole("group", { name: "Comparison type" })).getByRole("button", {
        name: "Source files",
      }),
    );

    expect(screen.getByText("Add a second file to compare recorded information.")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("one.gcode")).not.toBeInTheDocument();
  });
  it("explains an unavailable mixed scope instead of displaying stale data", async () => {
    renderApp(<Controlled files={[source]} />);

    await userEvent.click(
      within(screen.getByRole("group", { name: "Comparison type" })).getByRole("button", {
        name: "All files",
      }),
    );

    expect(screen.getByText("Add a second file to compare recorded information.")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("one.gcode")).not.toBeInTheDocument();
  });
});
