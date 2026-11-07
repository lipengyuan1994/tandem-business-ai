declare module "virtual:booking-copy" {
  const copy: typeof import("@/locales/en.json")["bookings"]
  export default copy
}
