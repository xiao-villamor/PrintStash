import { queryOptions } from "@tanstack/react-query";
import { getAuthProviders } from "@/lib/api/auth";

/** Login methods are server state; a mounted entry owns request admission. */
export function loginProvidersOptions() {
  return queryOptions({
    queryKey: ["auth-entry", "providers"],
    queryFn: ({ signal }) => getAuthProviders({ signal }),
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}
