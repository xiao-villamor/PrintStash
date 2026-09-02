"use client";

/**
 * First-run setup wizard.
 *
 * Two steps:
 *   1. Create the admin account (username, password + confirm, optional email).
 *   2. Confirm storage paths (pre-filled from the backend's current defaults).
 *
 * On submit we POST /api/v1/setup, store the returned JWT, and redirect to `/`.
 * If the install is already configured we redirect to `/login` instead — the
 * wizard is a one-shot endpoint and re-running it is intentionally blocked
 * server-side.
 */

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  HardDrive,
  Loader2,
  RefreshCw,
  ShieldCheck,
  UserPlus,
} from "lucide-react";

import { completeSetup, getSetupStatus, getStorageProviders } from "@/lib/api";
import {
  defaultProviderValues,
  StorageProviderPicker,
  type ProviderValues,
} from "@/components/storage-provider-picker";
import { ThemeToggle } from "@/components/theme-toggle";
import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { storeLogin, type StoredUser } from "@/lib/auth";
import { cn } from "@/lib/utils";
import type { SetupStatus, StorageProvider } from "@/types";

type Step = 1 | 2;

const SETUP_ERROR_MESSAGES = {
  already_configured: "This vault has already been set up. Redirecting to sign in.",
  users_already_exist:
    "A user already exists in this vault. Sign in with an existing account instead.",
  invalid_data_dir_path: "The data directory path is not valid.",
  data_dir_not_creatable:
    "The backend could not create the data directory. Check the path and container permissions.",
  data_dir_not_writable:
    "The backend cannot write to the data directory. Check filesystem permissions.",
  data_dir_not_readable:
    "The backend cannot inspect the data directory. Check filesystem permissions.",
  data_dir_not_empty:
    "The data directory must be a dedicated empty directory. To index existing files without copying them into Vault storage, finish setup with the default path, then add a mounted folder or remote connection under Settings → Library sources.",
  invalid_thumb_dir_path: "The thumbnail directory path is not valid.",
  thumb_dir_not_creatable:
    "The backend could not create the thumbnail directory. Check the path and container permissions.",
  thumb_dir_not_writable:
    "The backend cannot write to the thumbnail directory. Check filesystem permissions.",
  thumb_dir_not_readable:
    "The backend cannot inspect the thumbnail directory. Check filesystem permissions.",
  thumb_dir_not_empty: "The thumbnail directory must be a dedicated empty directory.",
  invalid_storage_backend: "Choose either local disk or S3/R2 storage.",
  s3_bucket_required: "S3/R2 storage needs a bucket name.",
  invalid_setup_token:
    "That setup token is not valid. Copy the current token from the API container logs.",
} satisfies Record<string, string>;

/** A setup failure code the backend can return that we have prose for. */
type SetupErrorCode = keyof typeof SETUP_ERROR_MESSAGES;

function isSetupErrorCode(code: string): code is SetupErrorCode {
  return Object.hasOwn(SETUP_ERROR_MESSAGES, code);
}

function humanizeError(raw: string): string {
  // api.ts wraps errors as "HTTP <code>: <body>" where the body for FastAPI
  // HTTPException is typically '{"detail":"<code>"}'. Extract the code.
  const match = raw.match(/"detail"\s*:\s*"([^"]+)"/);
  const code = match?.[1];
  if (code && isSetupErrorCode(code)) return SETUP_ERROR_MESSAGES[code];
  if (code) return code.replace(/_/g, " ");
  return raw;
}

/**
 * The setup endpoints and the login store the wizard writes to. Injectable so a test
 * can drive the wizard with fakes instead of replacing the modules underneath it.
 */
export interface SetupPageDeps {
  getSetupStatus: typeof getSetupStatus;
  getStorageProviders: typeof getStorageProviders;
  completeSetup: typeof completeSetup;
  storeLogin: typeof storeLogin;
}

const LIVE_DEPS: SetupPageDeps = {
  getSetupStatus,
  getStorageProviders,
  completeSetup,
  storeLogin,
};

export default function SetupPage({ deps = LIVE_DEPS }: { deps?: SetupPageDeps }) {
  const router = useRouter();

  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [step, setStep] = useState<Step>(1);

  // Step 1 — account
  const [setupToken, setSetupToken] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  // Step 2 — storage
  const [providers, setProviders] = useState<StorageProvider[]>([]);
  const [storageProvider, setStorageProvider] = useState("local");
  const [providerValues, setProviderValues] = useState<ProviderValues>({});
  const [backupDays, setBackupDays] = useState(30);
  const [backupS3Bucket, setBackupS3Bucket] = useState("");
  const [backupS3Endpoint, setBackupS3Endpoint] = useState("");
  const [backupS3Region, setBackupS3Region] = useState("auto");
  const [backupS3AccessKey, setBackupS3AccessKey] = useState("");
  const [backupS3SecretKey, setBackupS3SecretKey] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([deps.getSetupStatus(), deps.getStorageProviders()])
      .then(([s, providerCatalogue]) => {
        if (cancelled) return;
        if (s.configured) {
          router.replace("/login");
          return;
        }
        setStatus(s);
        setProviders(providerCatalogue);
        const providerId =
          s.current_storage_provider ?? (s.current_storage_backend === "s3" ? "s3" : "local");
        const descriptor = providerCatalogue.find((provider) => provider.id === providerId);
        const legacyValues =
          providerId === "local"
            ? {
                data_dir: s.current_data_dir ?? s.default_data_dir ?? "",
                thumb_dir: s.current_thumb_dir ?? s.default_thumb_dir ?? "",
              }
            : {
                bucket: s.current_s3_bucket ?? "",
                endpoint_url: s.current_s3_endpoint_url ?? "",
                region: s.current_s3_region ?? "auto",
              };
        setStorageProvider(providerId);
        const initialValues = descriptor ? defaultProviderValues(descriptor) : {};
        Object.assign(initialValues, legacyValues, s.current_storage_provider_config);
        setProviderValues(initialValues);
        setBackupDays(s.current_backup_retention_days ?? 30);
        setBackupS3Bucket(s.current_backup_s3_bucket ?? "");
        setBackupS3Endpoint(s.current_backup_s3_endpoint_url ?? "");
        setBackupS3Region(s.current_backup_s3_region || "auto");
      })
      .catch((err) => {
        if (cancelled) return;
        setBootError(err?.message ?? "Could not reach the backend. Make sure the API is running.");
      });
    return () => {
      cancelled = true;
    };
  }, [deps, router]);

  function validateStep1(): string | null {
    if (setupToken.trim().length < 16)
      return "Enter the setup token shown in the API container logs.";
    if (username.trim().length < 3) return "Username must be at least 3 characters.";
    if (password.length < 8) return "Password must be at least 8 characters.";
    if (password !== confirm) return "Passwords do not match.";
    if (email && !email.includes("@")) return "Email looks invalid (or leave blank).";
    return null;
  }

  function handleNext() {
    const v = validateStep1();
    if (v) {
      setError(v);
      return;
    }
    setError(null);
    setStep(2);
  }

  function validateStep2(): string | null {
    const provider = providers.find((item) => item.id === storageProvider);
    if (!provider?.selectable) return provider?.disabled_reason ?? "Choose an available provider.";
    const missing = provider.fields.find(
      (field) =>
        field.required &&
        !field.secret &&
        String(providerValues[field.name] ?? "").trim().length === 0,
    );
    if (missing) {
      return `${missing.label} is required.`;
    }
    if (!Number.isFinite(backupDays) || backupDays < 0) {
      return "Backup retention must be 0 or more days.";
    }
    return null;
  }

  async function handleSubmit() {
    if (busy) return;
    setError(null);
    const v = validateStep2();
    if (v) {
      setError(v);
      return;
    }
    setBusy(true);
    try {
      const config = Object.fromEntries(
        Object.entries(providerValues).filter(
          ([name, value]) => name !== "secret_fields_set" && value !== "",
        ),
      );
      const res = await deps.completeSetup({
        setup_token: setupToken.trim(),
        username: username.trim(),
        password,
        email: email.trim() || undefined,
        storage_provider: storageProvider,
        storage_provider_config: { provider: storageProvider, ...config },
        backup_retention_days: backupDays,
        backup_s3_bucket: backupS3Bucket.trim() || undefined,
        backup_s3_endpoint_url: backupS3Endpoint.trim() || undefined,
        backup_s3_region: backupS3Region.trim() || "auto",
        backup_s3_access_key: backupS3AccessKey.trim() || undefined,
        backup_s3_secret_key: backupS3SecretKey || undefined,
      });
      const stored: StoredUser = {
        id: res.user_id,
        username: res.username,
        email: email.trim() || null,
        is_superuser: true,
      };
      deps.storeLogin(res.access_token, stored);
      router.replace("/");
    } catch (err: any) {
      setError(humanizeError(err?.message ?? "Setup failed."));
      setBusy(false);
    }
  }

  // ---- Render ---------------------------------------------------------------

  if (bootError) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background px-4">
        <Card className="w-full max-w-md space-y-4 border-outline-variant bg-surface-container-low p-6">
          <h1 className="text-lg font-semibold text-on-surface">Cannot reach the vault</h1>
          <p className="text-sm text-on-surface-variant font-mono break-words">{bootError}</p>
          <Button type="button" onClick={() => window.location.reload()} className="w-fit">
            Retry
          </Button>
        </Card>
      </main>
    );
  }

  if (!status) {
    return (
      <main
        className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background"
        role="status"
        aria-live="polite"
      >
        <Loader2 className="h-6 w-6 animate-spin text-on-surface-variant" aria-hidden />
        <p className="text-sm text-on-surface-variant">Checking vault setup…</p>
      </main>
    );
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background px-4 py-8 sm:px-6 lg:py-12">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-0 top-0 h-80 w-80 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/5 blur-3xl"
      />
      <div className="absolute right-4 top-4 sm:right-6 sm:top-6">
        <ThemeToggle />
      </div>

      <Card className="relative grid w-full max-w-5xl overflow-hidden border-outline-variant bg-card shadow-lg lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <aside className="flex flex-col border-b border-outline-variant bg-surface-container-low p-6 sm:p-8 lg:border-b-0 lg:border-r lg:p-10">
          <div className="flex items-center gap-3 lg:block">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm lg:mb-6 lg:h-14 lg:w-14">
              <BrandMark className="h-8 w-8 lg:h-9 lg:w-9" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-on-surface">
                Welcome to PrintStash
              </h1>
              <p className="mt-1 text-sm text-on-surface-variant">
                Two quick steps to prepare your self-hosted vault.
              </p>
            </div>
          </div>

          <ol
            className="mt-6 grid grid-cols-2 gap-2 lg:mt-10 lg:grid-cols-1 lg:gap-3"
            aria-label="Setup progress"
          >
            <StepIndicator
              active={step === 1}
              done={step > 1}
              label="Account"
              description="Secure vault access"
              icon={UserPlus}
            />
            <StepIndicator
              active={step === 2}
              done={false}
              label="Storage"
              description="Choose where files live"
              icon={HardDrive}
            />
          </ol>

          <p className="mt-6 hidden text-xs leading-relaxed text-on-surface-variant lg:mt-auto lg:block">
            Setup runs once. Additional administrators and storage settings can be managed later.
          </p>
        </aside>

        <form
          noValidate
          className="flex min-w-0 flex-col p-6 sm:p-8 lg:p-10"
          onSubmit={(event) => {
            event.preventDefault();
            if (step === 1) handleNext();
            else void handleSubmit();
          }}
        >
          <div key={step} className="animate-panel-in">
            {step === 1 ? (
              <AccountStep
                setupToken={setupToken}
                setSetupToken={setSetupToken}
                username={username}
                setUsername={setUsername}
                email={email}
                setEmail={setEmail}
                password={password}
                setPassword={setPassword}
                confirm={confirm}
                setConfirm={setConfirm}
              />
            ) : (
              <StorageStep
                providers={providers}
                storageProvider={storageProvider}
                providerValues={providerValues}
                setStorageProvider={(provider) => {
                  setStorageProvider(provider.id);
                  setProviderValues(defaultProviderValues(provider));
                }}
                setProviderValue={(name, value) =>
                  setProviderValues((current) => ({ ...current, [name]: value }))
                }
                backupDays={backupDays}
                setBackupDays={setBackupDays}
                backupS3Bucket={backupS3Bucket}
                setBackupS3Bucket={setBackupS3Bucket}
                backupS3Endpoint={backupS3Endpoint}
                setBackupS3Endpoint={setBackupS3Endpoint}
                backupS3Region={backupS3Region}
                setBackupS3Region={setBackupS3Region}
                backupS3AccessKey={backupS3AccessKey}
                setBackupS3AccessKey={setBackupS3AccessKey}
                backupS3SecretKey={backupS3SecretKey}
                setBackupS3SecretKey={setBackupS3SecretKey}
              />
            )}
          </div>

          {error && (
            <div
              role="alert"
              className="mt-5 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
            >
              {error}
            </div>
          )}

          <div className="mt-6 flex items-center justify-between gap-3 border-t border-outline-variant pt-5">
            {step === 2 ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setError(null);
                  setStep(1);
                }}
                disabled={busy}
              >
                <ChevronLeft className="h-4 w-4" />
                Back
              </Button>
            ) : (
              <span />
            )}

            {step === 1 ? (
              <Button type="submit">
                Next
                <ChevronRight className="h-4 w-4" />
              </Button>
            ) : (
              <Button type="submit" loading={busy}>
                {!busy && <ShieldCheck className="h-4 w-4" />}
                Complete setup
              </Button>
            )}
          </div>
        </form>
      </Card>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Step components
// ---------------------------------------------------------------------------

function AccountStep(props: {
  setupToken: string;
  setSetupToken: (v: string) => void;
  username: string;
  setUsername: (v: string) => void;
  email: string;
  setEmail: (v: string) => void;
  password: string;
  setPassword: (v: string) => void;
  confirm: string;
  setConfirm: (v: string) => void;
}) {
  return (
    <div className="space-y-5">
      <StepHeader
        eyebrow="Step 1 of 2"
        title="Create your admin account"
        description="Use this account to manage your library and invite other administrators later."
      />
      <Field
        label="Setup token"
        id="setup-token"
        value={props.setupToken}
        onChange={props.setSetupToken}
        type="password"
        autoComplete="off"
        required
        hint="Copy it from the API container logs"
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Username"
          id="setup-username"
          value={props.username}
          onChange={props.setUsername}
          autoFocus
          autoComplete="username"
          required
        />
        <Field
          label="Email"
          optional
          id="setup-email"
          value={props.email}
          onChange={props.setEmail}
          autoComplete="email"
          type="email"
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Password"
          id="setup-password"
          value={props.password}
          onChange={props.setPassword}
          type="password"
          autoComplete="new-password"
          required
          hint="At least 8 characters"
        />
        <Field
          label="Confirm password"
          id="setup-confirm"
          value={props.confirm}
          onChange={props.setConfirm}
          type="password"
          autoComplete="new-password"
          required
        />
      </div>
      <div className="flex gap-3 rounded-md bg-muted p-3 text-sm text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
        <p>Create the first administrator account. You can add more users later.</p>
      </div>
    </div>
  );
}

function StorageStep(props: {
  providers: StorageProvider[];
  storageProvider: string;
  providerValues: ProviderValues;
  setStorageProvider: (provider: StorageProvider) => void;
  setProviderValue: (name: string, value: string | number) => void;
  backupDays: number;
  setBackupDays: (v: number) => void;
  backupS3Bucket: string;
  setBackupS3Bucket: (v: string) => void;
  backupS3Endpoint: string;
  setBackupS3Endpoint: (v: string) => void;
  backupS3Region: string;
  setBackupS3Region: (v: string) => void;
  backupS3AccessKey: string;
  setBackupS3AccessKey: (v: string) => void;
  backupS3SecretKey: string;
  setBackupS3SecretKey: (v: string) => void;
}) {
  return (
    <div className="space-y-5">
      <StepHeader
        eyebrow="Step 2 of 2"
        title="Choose your storage"
        description="Choose a provider, review its safety guarantees, then enter the connection details."
      />

      <StorageProviderPicker
        providers={props.providers}
        providerId={props.storageProvider}
        values={props.providerValues}
        onProviderChange={props.setStorageProvider}
        onValueChange={props.setProviderValue}
      />

      <section
        className="space-y-4 border-t border-outline-variant pt-5"
        aria-labelledby="backup-heading"
      >
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <RefreshCw className="h-4 w-4" aria-hidden />
          </div>
          <div>
            <h3 id="backup-heading" className="text-sm font-semibold text-on-surface">
              Backup retention
            </h3>
            <p className="text-xs text-on-surface-variant">
              Local backups are kept for this many days.
            </p>
          </div>
        </div>
        <div className="max-w-48">
          <Field
            label="Retention days"
            id="setup-backup-days"
            value={String(props.backupDays)}
            onChange={(v) => props.setBackupDays(Number(v))}
            type="number"
            hint="Use 0 to keep forever"
            mono
          />
        </div>

        <details className="group rounded-md border border-outline-variant bg-surface-container-low">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-md px-4 py-3 text-sm font-medium text-on-surface transition-colors duration-press hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 [&::-webkit-details-marker]:hidden">
            <span>
              Off-site backup <span className="font-normal text-muted-foreground">(optional)</span>
            </span>
            <ChevronDown
              className="h-4 w-4 text-muted-foreground transition-transform duration-fast group-open:rotate-180"
              aria-hidden
            />
          </summary>
          <div className="grid gap-4 border-t border-outline-variant p-4 sm:grid-cols-2">
            <Field
              label="Backup bucket"
              id="setup-backup-bucket"
              value={props.backupS3Bucket}
              onChange={props.setBackupS3Bucket}
              mono
            />
            <Field
              label="Backup endpoint"
              id="setup-backup-endpoint"
              value={props.backupS3Endpoint}
              onChange={props.setBackupS3Endpoint}
              mono
            />
            <Field
              label="Backup region"
              id="setup-backup-region"
              value={props.backupS3Region}
              onChange={props.setBackupS3Region}
              mono
            />
            <div className="hidden sm:block" aria-hidden />
            <Field
              label="Backup access key"
              optional
              id="setup-backup-access-key"
              value={props.backupS3AccessKey}
              onChange={props.setBackupS3AccessKey}
              mono
            />
            <Field
              label="Backup secret key"
              optional
              id="setup-backup-secret-key"
              value={props.backupS3SecretKey}
              onChange={props.setBackupS3SecretKey}
              type="password"
              mono
            />
            <p className="text-xs text-muted-foreground sm:col-span-2">
              Credentials can stay empty when your runtime provides them.
            </p>
          </div>
        </details>
      </section>
    </div>
  );
}

function Field(props: {
  label: string;
  id: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  autoFocus?: boolean;
  autoComplete?: string;
  required?: boolean;
  hint?: string;
  mono?: boolean;
  optional?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <label
        htmlFor={props.id}
        className="flex items-center justify-between gap-2 text-xs font-mono uppercase tracking-wider text-on-surface-variant"
      >
        <span>{props.label}</span>
        {props.optional && (
          <span className="font-sans normal-case tracking-normal text-muted-foreground">
            Optional
          </span>
        )}
      </label>
      <Input
        id={props.id}
        type={props.type ?? "text"}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        autoComplete={props.autoComplete}
        autoFocus={props.autoFocus}
        required={props.required}
        className={cn("bg-surface-container-lowest text-on-surface", props.mono && "font-mono")}
      />
      {props.hint && <p className="text-3xs font-mono text-on-surface-variant">{props.hint}</p>}
    </div>
  );
}

function StepIndicator(props: {
  active: boolean;
  done: boolean;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  const Icon = props.icon;
  return (
    <li
      aria-current={props.active ? "step" : undefined}
      className={cn(
        "flex min-w-0 items-center gap-3 rounded-md px-3 py-3 text-on-surface-variant",
        props.active && "bg-accent text-accent-foreground",
      )}
    >
      <span
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-outline-variant bg-background",
          props.active && "border-transparent bg-accent text-accent-foreground",
          props.done && "border-success/30 bg-success/10 text-success",
        )}
      >
        {props.done ? (
          <Check className="h-4 w-4" aria-hidden />
        ) : (
          <Icon className="h-4 w-4" aria-hidden />
        )}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{props.label}</span>
        <span className="hidden truncate text-xs opacity-70 sm:block">{props.description}</span>
      </span>
    </li>
  );
}

function StepHeader(props: { eyebrow: string; title: string; description: string }) {
  return (
    <header className="space-y-2">
      <p className="text-xs font-mono uppercase tracking-wider text-primary">{props.eyebrow}</p>
      <div>
        <h2 className="text-xl font-semibold tracking-tight text-on-surface sm:text-2xl">
          {props.title}
        </h2>
        <p className="mt-1 text-sm leading-relaxed text-on-surface-variant">{props.description}</p>
      </div>
    </header>
  );
}
