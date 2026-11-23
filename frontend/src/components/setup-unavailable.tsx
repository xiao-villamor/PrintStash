import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import type { MessageKey } from "@/lib/locale";
import type { SetupStatus } from "@/types";

type Reason = NonNullable<SetupStatus["unavailable_reason"]>;
type Exit = { key: MessageKey; code?: string };
type Screen = { title: MessageKey; help: MessageKey; exits: (host: string) => Exit[] };

const ENV_ADMIN =
  "VAULT_SETUP_MODE=environment\nVAULT_SETUP_ADMIN_USERNAME=admin\nVAULT_SETUP_ADMIN_PASSWORD=<your password>";
const USE_BROWSER = "VAULT_SETUP_MODE=trusted_network";
const FIX_CREDENTIALS: Exit[] = [
  { key: "setup.misconfiguredSetCredentials", code: ENV_ADMIN },
  { key: "setup.misconfiguredUseBrowser", code: USE_BROWSER },
];

/** One screen per reason the server gives, so a new reason is one entry here. */
const SCREENS = {
  untrusted_host: {
    title: "setup.unavailableHostTitle",
    help: "setup.unavailableHostHelp",
    exits: (host) => [
      { key: "setup.unavailableLan" },
      { key: "setup.unavailableAllowHost", code: `VAULT_SETUP_ALLOWED_HOSTS=${host}` },
      { key: "setup.unavailableEnvAdmin", code: ENV_ADMIN },
    ],
  },
  disabled: {
    title: "setup.unavailableDisabledTitle",
    help: "setup.disabled",
    exits: () => [
      { key: "setup.unavailableEnable", code: USE_BROWSER },
      { key: "setup.unavailableEnvAdmin", code: ENV_ADMIN },
    ],
  },
  environment: {
    title: "setup.unavailableEnvironmentTitle",
    help: "setup.unavailableEnvironmentHelp",
    exits: () => [],
  },
  admin_credentials_missing: {
    title: "setup.misconfiguredTitle",
    help: "setup.misconfiguredMissing",
    exits: () => FIX_CREDENTIALS,
  },
  admin_credentials_invalid: {
    title: "setup.misconfiguredTitle",
    help: "setup.misconfiguredInvalid",
    exits: () => FIX_CREDENTIALS,
  },
  admin_credentials_without_environment_mode: {
    title: "setup.misconfiguredTitle",
    help: "setup.misconfiguredWrongMode",
    exits: () => [
      { key: "setup.misconfiguredSetMode", code: "VAULT_SETUP_MODE=environment" },
      { key: "setup.misconfiguredRemove" },
    ],
  },
} satisfies Record<Reason, Screen>;

/**
 * Why this browser cannot claim the installation, and every way out.
 *
 * Browser registration refuses a host it cannot place on a private network
 * (that check is what stops a malicious page from claiming a LAN install
 * through DNS rebinding), runs with registration turned off, or waits for the
 * administrator the deployment provisions. When the first-run settings
 * contradict each other, nothing can claim the installation until they are
 * fixed, and this names the variables to change — never their values.
 */
export function SetupUnavailable({
  reason,
  host,
  variables,
  onRetry,
}: {
  reason: Reason;
  host: string;
  variables: string[];
  onRetry: () => void;
}) {
  const { t } = useI18n();
  const screen = SCREENS[reason];
  const exits = screen.exits(host);
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">{t(screen.title)}</h2>
        <p role="alert" className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
          {t(screen.help, { host, variables: variables.join(", ") })}
        </p>
      </div>
      {exits.length > 0 && (
        <ol
          aria-label={t("setup.unavailableExits")}
          className="list-decimal space-y-3 pl-5 text-sm leading-relaxed"
        >
          {exits.map((exit) => (
            <li key={exit.key}>
              {t(exit.key)}
              {exit.code && (
                <code className="mt-2 block select-all whitespace-pre-wrap break-all rounded bg-background px-3 py-2 font-mono text-xs text-foreground">
                  {exit.code}
                </code>
              )}
            </li>
          ))}
        </ol>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("setup.unavailableRestart")}
      </p>
      <Button variant="outline" onClick={onRetry}>
        {t("setup.unavailableRetry")}
      </Button>
    </div>
  );
}
