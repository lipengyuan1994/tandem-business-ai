import { lazy } from "react"
export const LazyBookings = lazy(() => import("./Bookings").then((module) => ({ default: module.Bookings })))
export const LazySupplierQuote = lazy(() => import("./SupplierQuote").then((module) => ({ default: module.SupplierQuote })))
