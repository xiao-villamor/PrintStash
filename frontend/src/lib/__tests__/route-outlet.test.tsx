/** An outgoing screen cannot write a lazy destination's URL while it is still loading. */
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { RouteOutlet } from "@/lib/route-outlet";
import { useRouter } from "@/lib/navigation";

function Frame() {
  const navigation = useRouter();
  return (
    <>
      <button onClick={() => navigation.push("/next")}>Open next</button>
      <RouteOutlet />
    </>
  );
}

describe("RouteOutlet", () => {
  it("retires the outgoing screen before a delayed destination is ready", async () => {
    const ready = Promise.withResolvers<void>();
    const root = createRootRoute({ component: Frame });
    const source = createRoute({
      getParentRoute: () => root,
      path: "/",
      component: () => <p>Source screen</p>,
    });
    const destination = createRoute({
      getParentRoute: () => root,
      path: "/next",
      beforeLoad: () => ready.promise,
      component: () => <p>Destination screen</p>,
    });
    const router = createRouter({
      history: createMemoryHistory(),
      routeTree: root.addChildren([source, destination]),
    });
    await router.load();
    render(<RouterProvider router={router} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Open next" }));

    expect(screen.queryByText("Source screen")).not.toBeInTheDocument();
    expect(screen.queryByText("Destination screen")).not.toBeInTheDocument();
    await act(async () => ready.resolve());
    expect(await screen.findByText("Destination screen")).toBeVisible();
  });
});
