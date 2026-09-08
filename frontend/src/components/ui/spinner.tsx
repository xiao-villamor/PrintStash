import { Spinner as SharedSpinner, type SpinnerProps } from "@printstash/ui";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
export type { SpinnerProps } from "@printstash/ui";

export function Spinner(props: SpinnerProps) {
  useUiLocale();
  return <SharedSpinner {...props} label={props.label ?? uiText("Loading")} />;
}
