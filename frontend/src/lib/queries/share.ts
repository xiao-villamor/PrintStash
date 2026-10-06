/** Public capability data belongs to its token, independently of private sessions. */
import { queryOptions } from "@tanstack/react-query";
import { getSharedModel } from "@/lib/api/share";

export function sharedModelOptions(token: string) {
  return queryOptions({
    queryKey: ["public-share", token],
    queryFn: ({ signal }) => getSharedModel(token, { signal }),
    retry: false,
    gcTime: 0,
  });
}
