// Pages load on demand (route-level code splitting, 09 performance budget).
import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export interface ScheduleSearch {
  date?: string
  appointment?: string
}

const DATE = /^\d{4}-\d{2}-\d{2}$/

export const scheduleRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/schedule",
  validateSearch: (s: Record<string, unknown>): ScheduleSearch => ({
    date: typeof s.date === "string" && DATE.test(s.date) ? s.date : undefined,
    appointment: typeof s.appointment === "string" ? s.appointment : undefined,
  }),
  component: lazyRouteComponent(() => import("./SchedulePage"), "SchedulePage"),
})
