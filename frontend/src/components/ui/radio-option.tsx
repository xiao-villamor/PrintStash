import type { ComponentProps, ReactNode } from "react";

type RadioOptionProps = Omit<ComponentProps<"input">, "type" | "children" | "className"> & {
  children: ReactNode;
};

export function RadioOption({ children, ...input }: RadioOptionProps) {
  return (
    <label className="flex min-h-9 min-w-0 cursor-action items-start gap-2 rounded px-2.5 py-2 text-sm/5 hover:bg-muted has-checked:bg-accent has-checked:text-accent-foreground has-[:disabled]:cursor-disabled has-[:disabled]:opacity-50">
      <input
        {...input}
        type="radio"
        className="mt-0.5 size-4 shrink-0 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      />
      <span className="min-w-0 wrap-anywhere">{children}</span>
    </label>
  );
}
