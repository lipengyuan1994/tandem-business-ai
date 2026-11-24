import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, unwrap } from "@/api/client"
import type { Schemas } from "@/api/types"
import { dayBoundsUtc } from "./grid"

export const schedulingKeys = {
  all: ["scheduling"] as const,
  resources: ["scheduling", "resources"] as const,
  allResources: ["scheduling", "resources", "all"] as const,
  exceptions: ["scheduling", "exceptions"] as const,
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

export function useReschedule() {
  return useSchedulingMutation(async ({ id, version, starts_at, resource_id }: { id: string; version: number; starts_at: string; resource_id: string }) =>
    unwrap(await api.POST("/api/v1/scheduling/appointments/{appointment_id}/reschedule", { params: { path: { appointment_id: id } }, body: { version, starts_at, resource_id, override_hours: false } })))
}

// ------------------------------------------------------------------ setup (Settings → Resources)

/** Active and archived resources, for the setup screen. */
export function useAllResources() {
  return useQuery({ queryKey: schedulingKeys.allResources, queryFn: async () => unwrap(await api.GET("/api/v1/scheduling/resources", { params: { query: { include_archived: true } } })) })
}

export function useExceptions() {
  return useQuery({ queryKey: schedulingKeys.exceptions, queryFn: async () => unwrap(await api.GET("/api/v1/scheduling/exceptions")) })
}

export function useCreateResource() {
  return useSchedulingMutation(async (body: Schemas["ResourceIn"]) => unwrap(await api.POST("/api/v1/scheduling/resources", { body })))
}

export function useUpdateResource(id: string) {
  return useSchedulingMutation(async (body: Schemas["ResourceUpdate"]) =>
    unwrap(await api.PATCH("/api/v1/scheduling/resources/{resource_id}", { params: { path: { resource_id: id } }, body })))
}

export function useReplaceAvailability(id: string) {
  return useSchedulingMutation(async (rules: Schemas["RuleIn"][]) =>
    unwrap(await api.PUT("/api/v1/scheduling/resources/{resource_id}/availability", { params: { path: { resource_id: id } }, body: rules })))
}

export function useReplaceResourceServices(id: string) {
  return useSchedulingMutation(async (serviceIds: string[]) =>
    unwrap(await api.PUT("/api/v1/scheduling/resources/{resource_id}/services", { params: { path: { resource_id: id } }, body: serviceIds })))
}

export function useAddException() {
  return useSchedulingMutation(async (body: Schemas["ExceptionIn"]) => unwrap(await api.POST("/api/v1/scheduling/exceptions", { body })))
}

export function useDeleteException() {
  return useSchedulingMutation(async (id: string) => unwrap(await api.DELETE("/api/v1/scheduling/exceptions/{exception_id}", { params: { path: { exception_id: id } } })))
}
