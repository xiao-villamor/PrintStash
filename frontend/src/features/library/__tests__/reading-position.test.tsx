/** A Library history entry owns its nested reading offsets for one session. */
import "@testing-library/jest-dom/vitest";
import { useRef } from "react";
import { Link, Route, Routes, useNavigate } from "react-router-dom";
import { act, fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { renderApp } from "@/test-support/render";
import { useLibraryEntry, type LibraryLayout } from "../navigation-state";
import { useLibraryReadingPosition } from "../reading-position";

function Library({ layout }: { layout: LibraryLayout }) {
  const entry = useLibraryEntry("/?type=all", true);
  const main = useRef<HTMLElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useLibraryReadingPosition(entry, layout, main, list, true);
  return (
    <main ref={main} aria-label="Library">
      <div ref={list} role="region" aria-label="Rows">
        <Link to="/detail">Open Model</Link>
      </div>
    </main>
  );
}
function Detail() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(-1)}>Back</button>;
}
function Browser({ layout = "grid" }: { layout?: LibraryLayout }) {
  return (
    <Routes>
      <Route path="/" element={<Library layout={layout} />} />
      <Route path="/detail" element={<Detail />} />
    </Routes>
  );
}

describe("Library reading position", () => {
  it.each(["grid", "list"] as const)("restores a cached %s entry's own containers", (layout) => {
    renderApp(<Browser layout={layout} />);
    const main = screen.getByRole("main");
    const list = screen.getByRole("region", { name: "Rows" });
    main.scrollTop = 240;
    list.scrollTop = 510;
    fireEvent.scroll(list);
    fireEvent.click(screen.getByRole("link", { name: "Open Model" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("main").scrollTop).toBe(240);
    expect(screen.getByRole("region", { name: "Rows" }).scrollTop).toBe(510);
  });

  it("rejects late scroll writes from a retired private view", () => {
    renderApp(<Browser />);
    const main = screen.getByRole("main");
    main.scrollTop = 240;
    fireEvent.scroll(main);
    act(() => clearLogin());
    main.scrollTop = 900;
    fireEvent.scroll(main);
    fireEvent.click(screen.getByRole("link", { name: "Open Model" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("main").scrollTop).toBe(0);
    expect(screen.getByRole("region", { name: "Rows" }).scrollTop).toBe(0);
  });
});
