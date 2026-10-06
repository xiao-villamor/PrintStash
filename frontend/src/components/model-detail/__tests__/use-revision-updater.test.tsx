/** Revision success belongs to the command's session and accepted Model publication. */
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useRevisionUpdater } from "../use-revision-updater";
import { clearLogin } from "@/lib/auth-store";
import { aModel, aRevision } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

function Probe({ publication }: { publication: Promise<boolean> }) {
  const [published, setPublished] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [finished, setFinished] = useState(false);
  const updater = useRevisionUpdater(1, async () => {
    setPublishing(true);
    const accepted = await publication;
    setPublished(accepted);
    return accepted;
  });
  return (
    <>
      <button
        onClick={async () => {
          await updater.update(aRevision(), { revision_status: "failed" });
          setFinished(true);
        }}
      >
        Revise
      </button>
      <output>{published ? "Published" : "Unpublished"}</output>
      {publishing && <p>Publishing</p>}
      {finished && <p>Finished</p>}
    </>
  );
}
describe("useRevisionUpdater", () => {
  it("waits for confirmed Model publication before revision success", async () => {
    const publication = Promise.withResolvers<boolean>();
    const view = renderApp(<Probe publication={publication.promise} />, {
      routes: {
        "PATCH /api/v1/models/1/files/1/revision": json(aModel()),
      },
    });

    await userEvent.click(screen.getByRole("button", { name: "Revise" }));
    await waitFor(() => expect(view.requestsWithMethod("PATCH")).toHaveLength(1));
    expect(screen.queryByText("Revision updated")).toBeNull();
    await act(async () => publication.resolve(true));

    expect(await screen.findByText("Published")).toBeVisible();
    expect(await screen.findByText("Revision updated")).toBeVisible();
  });
  it("suppresses revision success if publication crosses a session change", async () => {
    const publication = Promise.withResolvers<boolean>();
    renderApp(<Probe publication={publication.promise} />, {
      routes: {
        "PATCH /api/v1/models/1/files/1/revision": json(aModel()),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Revise" }));
    await screen.findByText("Publishing");

    act(() => clearLogin());
    await act(async () => publication.resolve(true));

    await screen.findByText("Finished");
    expect(screen.queryByText("Revision updated")).toBeNull();
  });

  it("suppresses revision feedback after the session changes", async () => {
    const response = Promise.withResolvers<Response>();
    const view = renderApp(<Probe publication={Promise.resolve(true)} />, {
      routes: {
        "PATCH /api/v1/models/1/files/1/revision": () => response.promise,
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Revise" }));
    await waitFor(() => expect(view.requestsWithMethod("PATCH")).toHaveLength(1));

    act(() => clearLogin());
    await act(async () => response.resolve(json(aModel())));

    expect(screen.getByText("Unpublished")).toBeVisible();
    expect(screen.queryByText("Revision updated")).toBeNull();
  });
});
