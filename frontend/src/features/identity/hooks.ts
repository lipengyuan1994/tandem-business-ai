import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, setCsrfToken, unwrap } from "@/api/client"
import type { Me, Role } from "@/api/types"
import { ApiProblem } from "@/lib/problem"

export const meKey = ["identity", "me"] as const

export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: async (): Promise<Me | null> => {
      const result = await api.GET("/api/v1/auth/me")
      if (result.response.status === 401) return null
      const me = unwrap(result)
      setCsrfToken(me.csrf_token)
      return me
    },
    staleTime: 60_000,
    retry: (count, error) => !(error instanceof ApiProblem) && count < 2,
  })
}

export function useSwitchBusiness() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (businessId: string) =>
      unwrap(await api.POST("/api/v1/auth/active-business", { body: { business_id: businessId } })),
    onSuccess: (me) => {
      setCsrfToken(me.csrf_token)
      qc.clear() // every cached query belonged to the previous business
      qc.setQueryData(meKey, me)
    },
  })
}

export function useLogout(everywhere = false) {
  return useMutation({
    mutationFn: async () => {
      const result = everywhere ? await api.POST("/api/v1/auth/logout-all") : await api.POST("/api/v1/auth/logout")
      return unwrap(result) as { logout_url?: string }
    },
    onSuccess: (data) => {
      setCsrfToken(null)
      window.location.assign(data.logout_url ?? "/")
    },
  })
}

export const membersKey = ["identity", "members"] as const

export function useMembers() {
  return useQuery({ queryKey: membersKey, queryFn: async () => unwrap(await api.GET("/api/v1/identity/members")) })
}

export function useInvite() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { email: string; display_name: string; roles: Role[] }) =>
      unwrap(await api.POST("/api/v1/identity/invitations", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: membersKey }),
  })
}

export function useSetRoles() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ membershipId, roles }: { membershipId: string; roles: Role[] }) =>
      unwrap(await api.PUT("/api/v1/identity/members/{membership_id}/roles", { params: { path: { membership_id: membershipId } }, body: { roles } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: membersKey }),
  })
}

export function useUpdateMember() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ membershipId, version, status }: { membershipId: string; version: number; status: "active" | "suspended" | "removed" }) =>
      unwrap(await api.PATCH("/api/v1/identity/members/{membership_id}", { params: { path: { membership_id: membershipId } }, body: { version, status } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: membersKey }),
  })
}

export const ROLES: Role[] = ["owner", "admin", "demo_reviewer", "operator", "inventory_manager", "marketing_manager", "auditor"]
