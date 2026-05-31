import { getApiKey } from "@/lib/auth-store";

export {
  getToken as getStoredToken,
  getUser as getStoredUser,
  setApiKey as setStoredApiKey,
  isLoggedIn,
  storeLogin,
  clearLogin,
  clearLogin as clearStoredApiKey,
  onAuthChange,
  emitUnauthorized,
  onUnauthorized,
  type StoredUser,
} from "@/lib/auth-store";

export function getStoredApiKey(): string | null {
  return getApiKey();
}

export function hasStoredApiKey(): boolean {
  return !!getApiKey();
}
