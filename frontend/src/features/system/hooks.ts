import { useQuery } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"

export function useSystemStatus() {
  return useQuery({ queryKey: ["admin", "system"], queryFn: async () => unwrap(await api.GET("/api/v1/admin/system")), refetchInterval: 30_000 })
}

/** "3 h 12 min", "45 s": ages on the System page. */
export function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `${hours} h ${minutes % 60} min`
  return `${Math.floor(hours / 24)} d ${hours % 24} h`
}

/** Backups run every 6 hours; later than 7 hours (or an error, or no status at all) needs attention. */
export const BACKUP_WARN_AFTER_SECONDS = 7 * 3600
