import { useEffect, useState } from "react";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { cn } from "@/lib/utils";

interface TocItem {
  id: string;
  label: string;
}

function slug(label: string, index: number): string {
  const base = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `settings-${base || "group"}-${index}`;
}

/**
 * "On this page" for a long Settings section: it lists the section's groups
 * (every `SettingsCard`), jumps to one, and marks the group in view. It reads the
 * rendered groups, so a section never has to declare its own list.
 */
export function SettingsToc({ scope, section }: { scope: string; section: string }) {
  useUiLocale();
  const [items, setItems] = useState<TocItem[]>([]);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const root = document.querySelector(scope);
    if (!root) return;
    function collect() {
      const groups = Array.from(
        root!.querySelectorAll<HTMLElement>('section.settings-card[role="group"]'),
      ).filter(
        (group) =>
          !group.closest("[hidden]") && !group.parentElement?.closest("section.settings-card"),
      );
      const next = groups.flatMap((group, index) => {
        const label = group.getAttribute("aria-label");
        if (!label) return [];
        if (!group.id) group.id = slug(label, index);
        return [{ id: group.id, label }];
      });
      setItems((current) =>
        current.length === next.length &&
        current.every((item, i) => item.id === next[i]?.id && item.label === next[i]?.label)
          ? current
          : next,
      );
    }
    collect();
    const mutations = new MutationObserver(collect);
    mutations.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-label", "hidden"],
    });
    return () => mutations.disconnect();
  }, [scope, section]);

  useEffect(() => {
    const root = document.querySelector(scope);
    if (!root || items.length === 0) return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.add(entry.target.id);
          else visible.delete(entry.target.id);
        }
        const first = items.find((item) => visible.has(item.id));
        if (first) setActive(first.id);
      },
      { root, rootMargin: "0px 0px -60% 0px" },
    );
    for (const item of items) {
      const element = document.getElementById(item.id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [items, scope]);

  if (items.length < 3) return null;
  return (
    <aside aria-label={uiText("On this page")} className="sticky top-0 hidden self-start xl:block">
      <p className="mb-2.5 font-mono text-2xs font-medium uppercase tracking-wider text-muted-foreground">
        {uiText("On this page")}
      </p>
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              onClick={(event) => {
                event.preventDefault();
                document.getElementById(item.id)?.scrollIntoView({ block: "start" });
                setActive(item.id);
              }}
              aria-current={active === item.id ? "location" : undefined}
              className={cn(
                "block border-l py-1.5 pl-3 text-sm transition-colors duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active === item.id
                  ? "border-foreground font-medium text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}
