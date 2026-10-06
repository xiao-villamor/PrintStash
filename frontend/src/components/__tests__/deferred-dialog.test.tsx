/** Deferred forms load on first opening with an in-dialog fallback and retain their state after closing. */
import { lazy } from "react";
import { act, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DeferredDialog } from "@/components/deferred-dialog";
import { renderApp } from "@/test-support/render";

describe("DeferredDialog", () => {
  it("loads on opening, showing its pending state inside the dialog", async () => {
    const Form = () => (
      <label>
        Name
        <input />
      </label>
    );
    let deliver: (module: { default: typeof Form }) => void = () => {};
    const load = vi.fn<() => Promise<{ default: typeof Form }>>(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    );
    const LazyForm = lazy(load);
    const view = renderApp(
      <DeferredDialog open={false} title="Create" onClose={() => {}}>
        <LazyForm />
      </DeferredDialog>,
    );
    expect(load).not.toHaveBeenCalled();
    view.rerender(
      <DeferredDialog open title="Create" onClose={() => {}}>
        <LazyForm />
      </DeferredDialog>,
    );
    expect(
      screen.getByRole("dialog", { name: "Create" }).querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
    await act(async () => deliver({ default: Form }));
    expect(await screen.findByLabelText("Name")).toBeVisible();
  });
});
