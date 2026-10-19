/** Write guards block anonymous dispatch and explain how an expired session recovers. */
import { useState } from "react";
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useRequireAuth } from "../use-require-auth";
import { adminSession, renderApp } from "@/test-support/render";

function Guard({ operation }: { operation: () => Promise<number> }) {
  const auth = useRequireAuth();
  const [result, setResult] = useState<number | null>(null);
  return (
    <>
      <p>{auth.blockReason}</p>
      <output>{result}</output>
      <button
        onClick={() => void auth.guardWrite(operation).then((value) => setResult(value ?? null))}
      >
        Write
      </button>
      <button onClick={auth.showSessionExpiredToast}>Expired</button>
    </>
  );
}

describe("useRequireAuth", () => {
  it("returns the authenticated operation result", async () => {
    const operation = vi.fn<() => Promise<number>>().mockResolvedValue(25);
    renderApp(<Guard operation={operation} />);

    fireEvent.click(screen.getByRole("button", { name: "Write" }));

    expect(await screen.findByText("25")).toBeVisible();
  });
  it("blocks an anonymous write with sign-in guidance", async () => {
    const operation = vi.fn<() => Promise<number>>();
    renderApp(<Guard operation={operation} />, { auth: adminSession({ user: null }) });

    fireEvent.click(screen.getByRole("button", { name: "Write" }));

    expect(await screen.findByText("Authentication required")).toBeVisible();
    expect(screen.getByText("Sign in required")).toBeVisible();
    expect(operation).not.toHaveBeenCalled();
  });
  it("explains how an expired session recovers", async () => {
    renderApp(<Guard operation={vi.fn<() => Promise<number>>()} />);

    fireEvent.click(screen.getByRole("button", { name: "Expired" }));

    expect(await screen.findByText("Session expired")).toBeVisible();
    expect(screen.getByText("Please sign in again to continue.")).toBeVisible();
  });
});
