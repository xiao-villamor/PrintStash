"use client";

import { useEffect, useState } from "react";
import { FilamentProfileRead } from "@/types";
import {
  createFilamentProfile,
  deleteFilamentProfile,
  listFilamentProfiles,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { DollarSign, Plus, X } from "lucide-react";

function formatCost(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(2)}/kg`;
}

export function FilamentProfilesCard() {
  const auth = useRequireAuth();
  const [profiles, setProfiles] = useState<FilamentProfileRead[]>([]);
  const [name, setName] = useState("");
  const [materialType, setMaterialType] = useState("");
  const [materialBrand, setMaterialBrand] = useState("");
  const [costPerKg, setCostPerKg] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setProfiles(await listFilamentProfiles());
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function handleCreate() {
    const trimmedName = name.trim();
    if (!trimmedName) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }

    const parsedCost = costPerKg.trim() ? Number(costPerKg) : null;
    if (parsedCost !== null && (!Number.isFinite(parsedCost) || parsedCost < 0)) {
      toast.error("Invalid filament cost");
      return;
    }

    try {
      await createFilamentProfile({
        name: trimmedName,
        material_type: materialType.trim() || null,
        material_brand: materialBrand.trim() || null,
        cost_per_kg: parsedCost,
      });
      setName("");
      setMaterialType("");
      setMaterialBrand("");
      setCostPerKg("");
      toast.success(`Filament preset "${trimmedName}" saved`);
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleDelete(id: number) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await deleteFilamentProfile(id);
      toast.success("Filament preset removed");
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  return (
    <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
      <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-[var(--outline-variant)] flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <DollarSign className="h-4 w-4 text-[var(--on-surface-variant)] flex-shrink-0" />
          <h3 className="text-sm font-semibold text-[var(--on-surface)]">
            Filament presets
          </h3>
          <span className="font-mono text-xs text-[var(--on-surface-variant)]">
            ({profiles.length})
          </span>
        </div>
        <form
          onSubmit={(e) => { e.preventDefault(); handleCreate(); }}
          className="grid grid-cols-1 sm:grid-cols-[1.2fr_0.7fr_0.9fr_0.7fr_auto] gap-2"
        >
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!auth.isAuthenticated}
            placeholder={auth.isAuthenticated ? "Preset name..." : "Sign in to add"}
            className="bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-3 py-[6px] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent disabled:opacity-50"
          />
          <input
            value={materialType}
            onChange={(e) => setMaterialType(e.target.value)}
            disabled={!auth.isAuthenticated}
            placeholder="PLA"
            className="bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-3 py-[6px] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent disabled:opacity-50"
          />
          <input
            value={materialBrand}
            onChange={(e) => setMaterialBrand(e.target.value)}
            disabled={!auth.isAuthenticated}
            placeholder="Brand"
            className="bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-3 py-[6px] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent disabled:opacity-50"
          />
          <input
            value={costPerKg}
            onChange={(e) => setCostPerKg(e.target.value)}
            disabled={!auth.isAuthenticated}
            inputMode="decimal"
            placeholder="Cost/kg"
            className="bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-3 py-[6px] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!name.trim() || !auth.isAuthenticated}
            className="p-1.5 rounded bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </form>
      </div>

      <div className="p-3 sm:p-4">
        {error && (
          <div className="mb-3 rounded border border-[var(--error)]/30 bg-[var(--error-container)]/20 p-2 text-xs text-[var(--error)] font-mono">
            {error}
          </div>
        )}
        {loading ? (
          <p className="text-xs text-[var(--on-surface-variant)] font-mono">Loading...</p>
        ) : profiles.length === 0 ? (
          <p className="text-xs text-[var(--on-surface-variant)] font-mono">
            No filament presets saved yet.
          </p>
        ) : (
          <div className="space-y-1">
            {profiles.map((profile) => (
              <div
                key={profile.id}
                className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-[var(--surface-container-low)] group gap-2"
              >
                <div className="min-w-0">
                  <p className="text-sm text-[var(--on-surface)] truncate">
                    {profile.name}
                  </p>
                  <p className="font-mono text-[10px] text-[var(--on-surface-variant)] truncate">
                    {[profile.material_type, profile.material_brand].filter(Boolean).join(" · ") || "No material data"}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-[var(--on-surface)]">
                    {formatCost(profile.cost_per_kg)}
                  </span>
                  <button
                    onClick={() => handleDelete(profile.id)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-[var(--error-container)]/30 text-[var(--error)]"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
