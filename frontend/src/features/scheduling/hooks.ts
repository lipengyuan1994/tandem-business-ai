import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import { dayBoundsUtc } from "./grid"

export const schedulingKeys = {
  all: ["scheduling"] as const,
  resources: ["scheduling", "resources"] as const,
  day: (date: string) => ["scheduling", "appointments", date] as const,
  appointment: (id: string) => ["scheduling", "appointment", id] as const,
  slots: (serviceId: string, date: string) => ["scheduling", "slots", serviceId, date] as const,
}

export function useResources() {
  return useQuery({ queryKey: schedulingKeys.resources, queryFn: async () => unwrap(await api.GET("/api/v1/scheduling/resources")), staleTime: 60_000 })
}

export function useDayAppointments(localDate: string, timeZone: string) {
  return useQuery({
    queryKey: schedulingKeys.day(localDate),
    queryFn: async () => unwrap(await api.GET("/api/v1/scheduling/appointments", { params: { query: dayBoundsUtc(localDate, timeZone) } })),
    refetchInterval: 30_000, // holds expire; keep the grid honest
  })
}

export function useAppointment(id: string | undefined) {
  return useQuery({
    queryKey: schedulingKeys.appointment(id ?? ""),
    enabled: !!id,
    queryFn: async () => unwrap(await api.GET("/api/v1/scheduling/appointments/{appointment_id}", { params: { path: { appointment_id: id! } } })),
  })
}

/** Slots on one local day for a service (the server applies hours, holds, buffers and lead time). */
export function useSlots(serviceId: string | undefined, localDate: string, timeZone: string) {
  return useQuery({
    queryKey: schedulingKeys.slots(serviceId ?? "", localDate),
    enabled: !!serviceId,
    queryFn: async () => {
      const { start, end } = dayBoundsUtc(localDate, timeZone)
      return unwrap(await api.GET("/api/v1/scheduling/slots", { params: { query: { service_id: serviceId!, earliest: start, latest: end, limit: 50 } } }))
    },
  })
}

function useSchedulingMutation<TVars, TOut>(fn: (vars: TVars) => Promise<TOut>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: schedulingKeys.all }), qc.invalidateQueries({ queryKey: ["inventory"] })]),
  })
}

export function useBook() {
  return useSchedulingMutation(async (body: { customer_id: string; service_id: string; resource_id: string; starts_at: string }) =>
    unwrap(await api.POST("/api/v1/scheduling/appointments", { body: { ...body, hold_id: null, notes: null, override_hours: false, skip_parts: false } })))
}

export type AppointmentAction = "cancel" | "complete" | "no-show"

export function useAppointmentAction() {
  return useSchedulingMutation(async ({ id, action, version, reason }: { id: string; action: AppointmentAction; version: number; reason?: string }) => {
    const params = { params: { path: { appointment_id: id } } }
    if (action === "cancel") return unwrap(await api.POST("/api/v1/scheduling/appointments/{appointment_id}/cancel", { ...params, body: { version, reason: reason ?? "" } }))
    if (action === "complete") return unwrap(await api.POST("/api/v1/scheduling/appointments/{appointment_id}/complete", { ...params, body: { version } }))
    return unwrap(await api.POST("/api/v1/scheduling/appointments/{appointment_id}/no-show", { ...params, body: { version } }))
  })
}
