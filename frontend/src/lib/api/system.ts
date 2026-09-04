import { sendAction } from "@/lib/api/request";

export function restartPrintStash(): Promise<void> {
  return sendAction("/api/v1/system/restart", "POST");
}
