import type { ApiKeyRead, UserRead } from "@/types";

export function aUser(overrides: Partial<UserRead> = {}): UserRead {
  return {
    id: 2,
    username: "maker",
    email: null,
    is_superuser: false,
    is_active: true,
    oidc_managed: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}
export function aApiKey(overrides: Partial<ApiKeyRead> = {}): ApiKeyRead {
  return {
    id: 9,
    name: "Slicer",
    prefix: "ps_test",
    created_at: "2026-01-01T00:00:00Z",
    last_used_at: null,
    ...overrides,
  };
}
