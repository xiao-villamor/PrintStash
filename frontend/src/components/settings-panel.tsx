"use client";

import { useEffect, useState } from "react";
import { Server, Database, HardDrive, Tag, Folder, User } from "lucide-react";
import { TaxonomyManager } from "@/components/taxonomy-manager";
import { ApiKeyCard } from "@/components/api-key-card";
import { StorageConfigCard } from "@/components/storage-config-card";
import { useAuth } from "@/lib/auth-context";

interface HealthResponse {
  status: string;
  name: string;
  version: string;
}

export function SettingsPanel() {
  const { user } = useAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [categoryCount, setCategoryCount] = useState<number | null>(null);
  const [tagCount, setTagCount] = useState<number | null>(null);

  useEffect(() => {
    fetch("/api/v1/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => {});
    fetch("/api/v1/categories")
      .then((r) => r.json())
      .then((d) => setCategoryCount(d.length))
      .catch(() => {});
    fetch("/api/v1/tags")
      .then((r) => r.json())
      .then((d) => setTagCount(d.length))
      .catch(() => {});
  }, []);

  const statItems = [
    { label: "Vault version", value: health ? `${health.name} v${health.version}` : "Loading...", desc: "API server status and version", icon: Server },
    { label: "Database", value: health?.status === "ok" ? "Connected" : "Unknown", desc: "SQLite by default, Postgres optional", icon: Database },
    { label: "Storage", value: "/data/files", desc: "Container-absolute model storage path", icon: HardDrive },
    { label: "Categories", value: categoryCount != null ? `${categoryCount}` : "...", desc: "Hierarchical category tree entries", icon: Folder },
    { label: "Tags", value: tagCount != null ? `${tagCount}` : "...", desc: "Flat tag vocabulary size", icon: Tag },
  ];

  return (
    <div className="w-full space-y-4 sm:space-y-6 lg:space-y-8">
      <h2 className="text-xl font-semibold text-[var(--on-surface)]">Settings</h2>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 sm:gap-6 lg:gap-8">
        {/* Left column — read-only system info */}
        <div className="md:col-span-1 lg:col-span-2 space-y-4 sm:space-y-6 lg:space-y-8">
          {user && (
            <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
              <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-[var(--outline-variant)] flex items-center gap-2 sm:gap-3">
                <div className="w-9 h-9 rounded bg-[var(--surface-container)] flex items-center justify-center text-[var(--on-surface-variant)] flex-shrink-0">
                  <User className="h-4 w-4" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-[var(--on-surface)]">
                    Signed in as {user.username}
                  </h3>
                  <p className="text-xs text-[var(--on-surface-variant)] mt-0.5">
                    {user.is_superuser ? "Administrator" : "User"}
                    {user.email ? ` — ${user.email}` : ""}
                  </p>
                </div>
              </div>
              <div className="p-3 sm:p-4 text-xs font-mono text-[var(--on-surface-variant)]">
                Write operations use your JWT token. The API key below is kept for OrcaSlicer hooks and scripts.
              </div>
            </div>
          )}

          <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
            <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-[var(--outline-variant)]">
              <h3 className="text-sm font-semibold text-[var(--on-surface)]">Vault status</h3>
              <p className="text-xs text-[var(--on-surface-variant)] mt-0.5">System overview and configuration</p>
            </div>
            <div className="p-3 sm:p-4 lg:p-6">
              {statItems.map((item) => (
                <div key={item.label} className="flex items-center gap-3 sm:gap-4 py-3 border-b border-[var(--surface-variant)] last:border-b-0">
                  <div className="w-9 h-9 rounded bg-[var(--surface-container)] flex items-center justify-center text-[var(--on-surface-variant)] flex-shrink-0">
                    <item.icon className="h-4 w-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-[var(--on-surface)]">{item.label}</p>
                    <p className="text-xs text-[var(--on-surface-variant)]">{item.desc}</p>
                  </div>
                  <span className="font-mono text-xs sm:text-sm text-[var(--on-surface)] text-right">{item.value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
            <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-[var(--outline-variant)]">
              <h3 className="text-sm font-semibold text-[var(--on-surface)]">About</h3>
            </div>
            <div className="p-3 sm:p-4 lg:p-6 space-y-4">
              <p className="text-sm text-[var(--on-surface-variant)] leading-relaxed">
                <strong className="text-[var(--on-surface)]">PrintStash</strong> is a self-hosted, Plex-style asset management platform for 3D printing workflows. It ingests source meshes (STL/3MF) and sliced jobs (G-Code), extracts technical metadata, deduplicates assets, and exposes everything via a clean REST API.
              </p>
            </div>
          </div>
        </div>

        {/* Right column — active configuration */}
        <div className="md:col-span-1 lg:col-span-3 space-y-4 sm:space-y-6 lg:space-y-8">
          <ApiKeyCard />
          <StorageConfigCard />
          <TaxonomyManager />
        </div>
      </div>
    </div>
  );
}
