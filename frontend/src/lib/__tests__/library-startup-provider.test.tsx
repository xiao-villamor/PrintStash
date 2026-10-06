/** Secondary reads wait for visible primary content, except explicit interaction or recovery; each session owns its startup state. */
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LibraryStartupProvider } from "@/lib/library-startup-provider";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { renderApp } from "@/test-support/render";

function Probe() {
  const startup = useLibraryStartup();
  return (
    <>
      <output>{startup.canLoad("filters") ? "filters enabled" : "filters deferred"}</output>
      <output>{startup.canLoad("activity") ? "activity enabled" : "activity deferred"}</output>
      <button onClick={() => startup.settle("cards", "ready")}>cards</button>
      <button onClick={() => startup.settle("tree", "ready")}>tree</button>
      <button onClick={() => startup.settle("cards", "failed")}>fail</button>
      <button onClick={() => startup.request("filters")}>open filters</button>
    </>
  );
}

describe("LibraryStartupProvider", () => {
  it("releases secondary reads after both usable surfaces paint", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
    );
    await userEvent.click(screen.getByText("cards"));
    expect(screen.getByText("filters deferred")).toBeVisible();
    await userEvent.click(screen.getByText("tree"));
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
  it("does not wait for an absent mobile tree", async () => {
    renderApp(
      <LibraryStartupProvider active>
        <Probe />
      </LibraryStartupProvider>,
      { matchesMedia: () => false },
    );
    await userEvent.click(screen.getByText("cards"));
    expect(await screen.findByText("filters enabled")).toBeVisible();
  });
  it("keeps secondary reads released when navigation starts on another route", () => {
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
    expect(screen.getByText("activity enabled")).toBeVisible();
    expect(screen.getByText("filters enabled")).toBeVisible();
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
