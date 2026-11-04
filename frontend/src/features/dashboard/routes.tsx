import { createRoute } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"
import { TodayPage } from "./TodayPage"

export const todayRoute = createRoute({ getParentRoute: () => appRoute, path: "/today", component: TodayPage })
