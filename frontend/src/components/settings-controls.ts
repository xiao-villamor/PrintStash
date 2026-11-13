/**
 * The one set of control classes every Settings surface uses. A Settings file
 * imports these instead of declaring its own button or field classes, so a
 * button, a destructive action or a text field reads the same in every tab.
 */
import { buttonVariants } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Filled primary action: one per group at most. */
export const SETTINGS_BUTTON_PRIMARY = buttonVariants({ size: "sm" });
/** Every other action. */
export const SETTINGS_BUTTON = buttonVariants({ variant: "outline", size: "sm" });
/** Irreversible actions beside ordinary ones (Delete, Remove…, Revoke…). */
export const SETTINGS_BUTTON_DANGER = buttonVariants({ variant: "danger", size: "sm" });
/** Text fields and selects. */
export const SETTINGS_FIELD = cn(inputClasses, "h-[34px] rounded-none px-2.5 py-1.5 text-[13px]");
