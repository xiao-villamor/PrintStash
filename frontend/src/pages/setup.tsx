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
  Box,
  ChevronLeft,
  ChevronRight,
  Cloud,
  HardDrive,
  Key,
  Loader2,
  RefreshCw,
  ShieldCheck,
  UserPlus,
} from "lucide-react";

import { completeSetup, getSetupStatus } from "@/lib/api";
import { ThemeToggle } from "@/components/theme-toggle";
import { storeLogin, type StoredUser } from "@/lib/auth";
import type { SetupStatus } from "@/types";

type Step = 1 | 2;

const SETUP_ERROR_MESSAGES: Record<string, string> = {
  already_configured: "This vault has already been set up. Redirecting to sign in.",
  users_already_exist:
    "A user already exists in this vault. Sign in with an existing account instead.",
  invalid_data_dir_path: "The data directory path is not valid.",
  data_dir_not_creatable:
    "The backend could not create the data directory. Check the path and container permissions.",
  data_dir_not_writable:
    "The backend cannot write to the data directory. Check filesystem permissions.",
  invalid_thumb_dir_path: "The thumbnail directory path is not valid.",
  thumb_dir_not_creatable:
    "The backend could not create the thumbnail directory. Check the path and container permissions.",
  thumb_dir_not_writable:
    "The backend cannot write to the thumbnail directory. Check filesystem permissions.",
  invalid_storage_backend: "Choose either local disk or S3/R2 storage.",
  s3_bucket_required: "S3/R2 storage needs a bucket name.",
};

function humanizeError(raw: string): string {
  // api.ts wraps errors as "HTTP <code>: <body>" where the body for FastAPI
  // HTTPException is typically '{"detail":"<code>"}'. Extract the code.
  const match = raw.match(/"detail"\s*:\s*"([^"]+)"/);
  const code = match?.[1];
  if (code && SETUP_ERROR_MESSAGES[code]) return SETUP_ERROR_MESSAGES[code];
  if (code) return code.replace(/_/g, " ");
  return raw;
}

export default function SetupPage() {
  const router = useRouter();

  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [step, setStep] = useState<Step>(1);

  // Step 1 — account
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  // Step 2 — storage
  const [storageBackend, setStorageBackend] = useState("local");
  const [dataDir, setDataDir] = useState("");
  const [thumbDir, setThumbDir] = useState("");
  const [s3Bucket, setS3Bucket] = useState("");
  const [s3Endpoint, setS3Endpoint] = useState("");
  const [s3Region, setS3Region] = useState("auto");
  const [s3AccessKey, setS3AccessKey] = useState("");
  const [s3SecretKey, setS3SecretKey] = useState("");
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
    getSetupStatus()
      .then((s) => {
        if (cancelled) return;
        if (s.configured) {
          router.replace("/login");
          return;
        }
        setStatus(s);
        setStorageBackend(s.current_storage_backend || "local");
        setDataDir(s.current_data_dir);
        setThumbDir(s.current_thumb_dir);
        setS3Bucket(s.current_s3_bucket);
        setS3Endpoint(s.current_s3_endpoint_url);
        setS3Region(s.current_s3_region || "auto");
        setBackupDays(s.current_backup_retention_days ?? 30);
        setBackupS3Bucket(s.current_backup_s3_bucket);
        setBackupS3Endpoint(s.current_backup_s3_endpoint_url);
        setBackupS3Region(s.current_backup_s3_region || "auto");
      })
      .catch((err) => {
        if (cancelled) return;
        setBootError(
          err?.message ??
            "Could not reach the backend. Make sure the API is running.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  function validateStep1(): string | null {
    if (username.trim().length < 3)
      return "Username must be at least 3 characters.";
    if (password.length < 8) return "Password must be at least 8 characters.";
    if (password !== confirm) return "Passwords do not match.";
    if (email && !email.includes("@"))
      return "Email looks invalid (or leave blank).";
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
    if (storageBackend === "s3" && !s3Bucket.trim()) {
      return "S3/R2 storage needs a bucket name.";
    }
    if (storageBackend === "local" && (!dataDir.trim() || !thumbDir.trim())) {
      return "Local storage needs both data and thumbnail directories.";
    }
    if (!Number.isFinite(backupDays) || backupDays < 0) {
      return "Backup retention must be 0 or more days.";
    }
    return null;
  }

  async function handleSubmit() {
    setError(null);
    const v = validateStep2();
    if (v) {
      setError(v);
      return;
    }
    setBusy(true);
    try {
      const trimmedData = dataDir.trim();
      const trimmedThumb = thumbDir.trim();
      const res = await completeSetup({
        username: username.trim(),
        password,
        email: email.trim() || undefined,
        storage_backend: storageBackend,
        // Only send path overrides if the user actually changed the value.
        data_dir:
          storageBackend === "local" &&
          trimmedData &&
          trimmedData !== status?.current_data_dir
            ? trimmedData
            : undefined,
        thumb_dir:
          storageBackend === "local" &&
          trimmedThumb &&
          trimmedThumb !== status?.current_thumb_dir
            ? trimmedThumb
            : undefined,
        s3_bucket: s3Bucket.trim() || undefined,
        s3_endpoint_url: s3Endpoint.trim() || undefined,
        s3_region: s3Region.trim() || "auto",
        s3_access_key: s3AccessKey.trim() || undefined,
        s3_secret_key: s3SecretKey || undefined,
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
      storeLogin(res.access_token, stored);
      router.replace("/");
    } catch (err: any) {
      setError(humanizeError(err?.message ?? "Setup failed."));
      setBusy(false);
    }
  }

  // ---- Render ---------------------------------------------------------------

  if (bootError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--surface-container-lowest)] px-4">
        <div className="max-w-md w-full bg-[var(--surface-container-low)] border border-[var(--outline-variant)] rounded p-6 space-y-3">
          <h1 className="text-lg font-semibold text-[var(--on-surface)]">
            Cannot reach the vault
          </h1>
          <p className="text-sm text-[var(--on-surface-variant)] font-mono break-words">
            {bootError}
          </p>
          <button
            onClick={() => window.location.reload()}
            className="h-9 px-4 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--surface-container-lowest)]">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--on-surface-variant)]" />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--surface-container-lowest)] px-4 py-10 relative">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-lg mx-auto space-y-6">
        {/* Header */}
        <div className="text-center">
          <div className="w-14 h-14 mx-auto rounded bg-blue-600 dark:bg-orange-600 flex items-center justify-center text-white mb-4">
            <Box className="h-7 w-7" />
          </div>
          <h1 className="text-2xl font-bold text-[var(--on-surface)]">
            Welcome to PrintStash
          </h1>
          <p className="text-sm text-[var(--on-surface-variant)] mt-1">
            Let&apos;s get your self-hosted vault configured.
          </p>
        </div>

        {/* Stepper */}
        <div className="flex items-center justify-center gap-3 text-xs font-mono uppercase tracking-wider">
          <StepIndicator active={step === 1} done={step > 1} label="Account" icon={UserPlus} />
          <div className="h-px w-10 bg-[var(--outline-variant)]" />
          <StepIndicator active={step === 2} done={false} label="Storage" icon={HardDrive} />
        </div>

        {/* Card */}
        <div className="bg-[var(--surface-container-low)] border border-[var(--outline-variant)] rounded p-6 space-y-4">
          {step === 1 ? (
            <AccountStep
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
              storageBackend={storageBackend}
              setStorageBackend={setStorageBackend}
              dataDir={dataDir}
              setDataDir={setDataDir}
              thumbDir={thumbDir}
              setThumbDir={setThumbDir}
              defaultDataDir={status.default_data_dir}
              defaultThumbDir={status.default_thumb_dir}
              s3Bucket={s3Bucket}
              setS3Bucket={setS3Bucket}
              s3Endpoint={s3Endpoint}
              setS3Endpoint={setS3Endpoint}
              s3Region={s3Region}
              setS3Region={setS3Region}
              s3AccessKey={s3AccessKey}
              setS3AccessKey={setS3AccessKey}
              s3SecretKey={s3SecretKey}
              setS3SecretKey={setS3SecretKey}
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

          {error && (
            <div className="text-xs text-[var(--error)] font-mono">{error}</div>
          )}

          <div className="flex items-center justify-between gap-2 pt-2">
            {step === 2 ? (
              <button
                type="button"
                onClick={() => {
                  setError(null);
                  setStep(1);
                }}
                disabled={busy}
                className="h-10 px-4 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-xs uppercase tracking-wider hover:bg-[var(--surface-container)] disabled:opacity-50 flex items-center gap-1.5"
              >
                <ChevronLeft className="h-4 w-4" />
                Back
              </button>
            ) : (
              <span />
            )}

            {step === 1 ? (
              <button
                type="button"
                onClick={handleNext}
                className="h-10 px-4 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 flex items-center gap-1.5"
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </button>
            ) : (
              <button
                type="button"
                onClick={handleSubmit}
                disabled={busy}
                className="h-10 px-4 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
              >
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ShieldCheck className="h-4 w-4" />
                )}
                Complete setup
              </button>
            )}
          </div>
        </div>

        <p className="text-center text-xs text-[var(--on-surface-variant)] font-mono">
          This wizard only runs once. Subsequent admins can be added later.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step components
// ---------------------------------------------------------------------------

function AccountStep(props: {
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
    <>
      <p className="text-sm text-[var(--on-surface-variant)]">
        Create the first administrator account. You can add more users later.
      </p>
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
        label="Email (optional)"
        id="setup-email"
        value={props.email}
        onChange={props.setEmail}
        autoComplete="email"
        type="email"
      />
      <Field
        label="Password"
        id="setup-password"
        value={props.password}
        onChange={props.setPassword}
        type="password"
        autoComplete="new-password"
        required
        hint="Minimum 8 characters."
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
    </>
  );
}

function StorageStep(props: {
  storageBackend: string;
  setStorageBackend: (v: string) => void;
  dataDir: string;
  setDataDir: (v: string) => void;
  thumbDir: string;
  setThumbDir: (v: string) => void;
  defaultDataDir: string;
  defaultThumbDir: string;
  s3Bucket: string;
  setS3Bucket: (v: string) => void;
  s3Endpoint: string;
  setS3Endpoint: (v: string) => void;
  s3Region: string;
  setS3Region: (v: string) => void;
  s3AccessKey: string;
  setS3AccessKey: (v: string) => void;
  s3SecretKey: string;
  setS3SecretKey: (v: string) => void;
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
    <>
      <p className="text-sm text-[var(--on-surface-variant)]">
        Choose where the vault stores files and how long local backups should
        be retained.
      </p>

      <div>
        <label className="block text-xs font-mono uppercase tracking-wider text-[var(--on-surface-variant)] mb-1.5">
          Storage backend
        </label>
        <div className="grid grid-cols-2 gap-2">
          <ChoiceButton
            active={props.storageBackend === "local"}
            icon={HardDrive}
            label="Local disk"
            onClick={() => props.setStorageBackend("local")}
          />
          <ChoiceButton
            active={props.storageBackend === "s3"}
            icon={Cloud}
            label="S3 / R2"
            onClick={() => props.setStorageBackend("s3")}
          />
        </div>
      </div>

      {props.storageBackend === "local" ? (
        <div className="space-y-3">
          <Field
            label="Data directory"
            id="setup-data-dir"
            value={props.dataDir}
            onChange={props.setDataDir}
            required
            hint={`Default: ${props.defaultDataDir}`}
            mono
          />
          <Field
            label="Thumbnail directory"
            id="setup-thumb-dir"
            value={props.thumbDir}
            onChange={props.setThumbDir}
            required
            hint={`Default: ${props.defaultThumbDir}`}
            mono
          />
          <div className="text-xs text-[var(--on-surface-variant)] font-mono bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded p-3">
            The backend will create these directories and probe them for
            writability before completing setup.
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <Field
            label="Bucket"
            id="setup-s3-bucket"
            value={props.s3Bucket}
            onChange={props.setS3Bucket}
            required
            mono
          />
          <Field
            label="Endpoint URL"
            id="setup-s3-endpoint"
            value={props.s3Endpoint}
            onChange={props.setS3Endpoint}
            hint="Leave empty for AWS S3."
            mono
          />
          <Field
            label="Region"
            id="setup-s3-region"
            value={props.s3Region}
            onChange={props.setS3Region}
            mono
          />
          <div className="grid sm:grid-cols-2 gap-3">
            <Field
              label="Access key"
              id="setup-s3-access-key"
              value={props.s3AccessKey}
              onChange={props.setS3AccessKey}
              mono
            />
            <Field
              label="Secret key"
              id="setup-s3-secret-key"
              value={props.s3SecretKey}
              onChange={props.setS3SecretKey}
              type="password"
              mono
            />
          </div>
        </div>
      )}

      <div className="space-y-3 pt-2 border-t border-[var(--outline-variant)]">
        <p className="text-xs font-mono uppercase tracking-wider text-[var(--on-surface-variant)] flex items-center gap-1.5">
          <RefreshCw className="h-3.5 w-3.5" />
          Backups
        </p>
        <Field
          label="Retention days"
          id="setup-backup-days"
          value={String(props.backupDays)}
          onChange={(v) => props.setBackupDays(Number(v))}
          type="number"
          hint="Use 0 to keep backups forever."
          mono
        />
        <div className="grid sm:grid-cols-2 gap-3">
          <Field
            label="Backup bucket"
            id="setup-backup-bucket"
            value={props.backupS3Bucket}
            onChange={props.setBackupS3Bucket}
            hint="Optional off-site S3/R2 copy."
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
          <div className="flex items-end text-[10px] text-[var(--on-surface-variant)] font-mono gap-1.5 pb-1">
            <Key className="h-3.5 w-3.5" />
            Credentials are optional when the runtime provides them.
          </div>
          <Field
            label="Backup access key"
            id="setup-backup-access-key"
            value={props.backupS3AccessKey}
            onChange={props.setBackupS3AccessKey}
            mono
          />
          <Field
            label="Backup secret key"
            id="setup-backup-secret-key"
            value={props.backupS3SecretKey}
            onChange={props.setBackupS3SecretKey}
            type="password"
            mono
          />
        </div>
      </div>
    </>
  );
}

function ChoiceButton(props: {
  active: boolean;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
}) {
  const Icon = props.icon;
  return (
    <button
      type="button"
      onClick={props.onClick}
      className={`h-10 rounded border px-3 text-sm flex items-center justify-center gap-2 transition-colors ${
        props.active
          ? "bg-[var(--primary)]/10 border-[var(--primary)] text-[var(--primary)]"
          : "border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:border-[var(--outline)]"
      }`}
    >
      <Icon className="h-4 w-4" />
      {props.label}
    </button>
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
}) {
  return (
    <div>
      <label
        htmlFor={props.id}
        className="block text-xs font-mono uppercase tracking-wider text-[var(--on-surface-variant)] mb-1.5"
      >
        {props.label}
      </label>
      <input
        id={props.id}
        type={props.type ?? "text"}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        autoComplete={props.autoComplete}
        autoFocus={props.autoFocus}
        required={props.required}
        className={`w-full h-10 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] ${
          props.mono ? "font-mono" : ""
        } text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent`}
      />
      {props.hint && (
        <p className="text-[10px] text-[var(--on-surface-variant)] font-mono mt-1">
          {props.hint}
        </p>
      )}
    </div>
  );
}

function StepIndicator(props: {
  active: boolean;
  done: boolean;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  const Icon = props.icon;
  const tone = props.done
    ? "bg-[var(--primary)] text-[var(--primary-foreground)] border-transparent"
    : props.active
      ? "bg-[var(--primary-container)] text-[var(--on-primary-container)] border-[var(--primary)]"
      : "bg-transparent text-[var(--on-surface-variant)] border-[var(--outline-variant)]";
  return (
    <div className={`flex items-center gap-1.5 px-2.5 h-7 rounded border ${tone}`}>
      <Icon className="h-3.5 w-3.5" />
      <span>{props.label}</span>
    </div>
  );
}
