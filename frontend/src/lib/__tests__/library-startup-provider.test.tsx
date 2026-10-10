/** Secondary reads wait for visible primary content, except explicit interaction or recovery; each session owns its startup state. */
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useNavigate } from "react-router-dom";
import { useState } from "react";
import { LibraryStartupProvider } from "@/lib/library-startup-provider";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { renderApp } from "@/test-support/render";

function Probe() {
  const startup = useLibraryStartup();
  const navigate = useNavigate();
  const [previous, setPrevious] = useState(() => startup.settle);
  return (
    <>
      <output>{startup.canLoad("filters") ? "filters enabled" : "filters deferred"}</output>
      <output>{startup.canLoad("activity") ? "activity enabled" : "activity deferred"}</output>
      <output>{startup.complete ? "view complete" : "view pending"}</output>
      <button
        onClick={() => {
          setPrevious(() => startup.settle);
          navigate("/?c=next");
        }}
      >
        navigate
      </button>
      <button onClick={() => previous("media", "ready")}>old media</button>
      <button onClick={() => startup.settle("media", "ready")}>media</button>
      <button onClick={() => startup.settle("cards", "ready")}>cards</button>
      <button onClick={() => startup.settle("tree", "ready")}>tree</button>
      <button onClick={() => startup.settle("cards", "failed")}>fail</button>
      <button onClick={() => startup.request("filters")}>open filters</button>
    </>
  );
}

describe("LibraryStartupProvider", () => {
  it("waits for decoded media before completing the view", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
    );
    await userEvent.click(screen.getByText("cards"));
    await userEvent.click(screen.getByText("tree"));
    expect(screen.getByText("view pending")).toBeVisible();
    await userEvent.click(screen.getByText("media"));
    expect(await screen.findByText("view complete")).toBeVisible();
  });
  it("rejects the previous navigation's late completion", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
    );
    await userEvent.click(screen.getByText("cards"));
    await userEvent.click(screen.getByText("tree"));
    await userEvent.click(screen.getByText("navigate"));
    await userEvent.click(screen.getByText("cards"));
    await userEvent.click(screen.getByText("tree"));
    await userEvent.click(screen.getByText("old media"));
    expect(screen.getByText("view pending")).toBeVisible();
    await userEvent.click(screen.getByText("media"));
    expect(await screen.findByText("view complete")).toBeVisible();
  });
  it("keeps a failed destination incomplete", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
    );
    await userEvent.click(screen.getByText("fail"));
    await userEvent.click(screen.getByText("tree"));
    await userEvent.click(screen.getByText("media"));
    expect(screen.getByText("view pending")).toBeVisible();
  });
  it("releases secondary reads after the complete view paints", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
    );
    await userEvent.click(screen.getByText("cards"));
    expect(screen.getByText("filters deferred")).toBeVisible();
    await userEvent.click(screen.getByText("tree"));
    expect(screen.getByText("filters deferred")).toBeVisible();
    await userEvent.click(screen.getByText("media"));
    await screen.findByText("filters enabled");
    expect(screen.getByText("activity enabled")).toBeVisible();
  });
  it("loads an explicitly opened control during startup", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
    );
    await userEvent.click(screen.getByText("open filters"));
    expect(screen.getByText("filters enabled")).toBeVisible();
    expect(screen.getByText("activity deferred")).toBeVisible();
  });
  it("releases recovery controls when primary content fails", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
    );
    await userEvent.click(screen.getByText("fail"));
    await screen.findByText("filters enabled");
    expect(screen.getByText("activity enabled")).toBeVisible();
  });
  it("does not starve mobile controls while the tree is closed", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
      { matchesMedia: () => false },
    );
    await userEvent.click(screen.getByText("cards"));
    await userEvent.click(screen.getByText("media"));
    expect(await screen.findByText("filters enabled")).toBeVisible();
    expect(screen.getByText("view pending")).toBeVisible();
  });
  it("includes the opened mobile tree in completion", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
      { matchesMedia: () => false },
    );
    await userEvent.click(screen.getByText("cards"));
    await userEvent.click(screen.getByText("media"));
    expect(screen.getByText("view pending")).toBeVisible();
    await userEvent.click(screen.getByText("tree"));
    expect(await screen.findByText("view complete")).toBeVisible();
  });
  it("starts readiness when returning to the library", () => {
    const view = renderApp(
      <LibraryStartupProvider active={false}>
        <Probe />
      </LibraryStartupProvider>,
    );
    expect(screen.getByText("activity enabled")).toBeVisible();
    view.rerender(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
    );
    expect(screen.getByText("activity deferred")).toBeVisible();
  });
  it("starts the next session with independent pending surfaces", async () => {
    const view = renderApp(
      <LibraryStartupProvider key="owner-a" active>
        <Probe />
      </LibraryStartupProvider>,
    );
    await userEvent.click(screen.getByText("open filters"));
    act(() =>
      view.rerender(
        <LibraryStartupProvider key="owner-b" active>
          <Probe />
        </LibraryStartupProvider>,
      ),
    );
    await waitFor(() => expect(screen.getByText("filters deferred")).toBeVisible());
  });
});
