import { type FormEvent, useEffect, useRef, useState } from "react"
import type { components } from "@/api/schema"
import { call, client, WidgetError } from "./api"
import { B } from "./booking-strings"

type Booking = components["schemas"]["CustomerBooking"]
type Slot = components["schemas"]["CustomerSlot"]
const input = "min-h-11 w-full rounded border bg-background p-2"
const button = "min-h-11 rounded border px-4 py-2 disabled:opacity-50"

function failure(error: unknown) {
  if (error instanceof WidgetError) {
    if (error.status === 429) return B.rateLimited
    if (error.status === 409) return B.changed
    if (error.code === "self_service.fee_acknowledgment_required") return B.ackRequired
    if (error.status === 403) return B.expired
  }
  return B.error
}

export function Bookings({ slug }: { slug: string }) {
  const path = { business_slug: slug }
  const boot = useRef<Promise<Booking[] | null> | null>(null)
  const [items, setItems] = useState<Booking[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [email, setEmail] = useState("")
  const [notice, setNotice] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const load = async () => (await call(client.GET("/api/v1/public/{business_slug}/self-service/bookings", { params: { path } }))).items

  useEffect(() => {
    let alive = true
    const begin = () => {
      if (!boot.current) {
        const token = new URLSearchParams(location.hash.slice(1)).get("t")
        if (location.hash) history.replaceState(history.state, "", location.pathname + location.search)
        boot.current = (async () => {
          if (token) await call(client.POST("/api/v1/public/{business_slug}/self-service/exchange", { params: { path: { business_slug: slug } }, body: { token } }))
          try { return await call(client.GET("/api/v1/public/{business_slug}/self-service/bookings", { params: { path: { business_slug: slug } } })).then((r) => r.items) }
          catch (e) { if (!token && e instanceof WidgetError && e.status === 403) return null; throw e }
        })()
      }
      const pending = boot.current
      void pending.then((rows) => { if (alive && boot.current === pending) { setItems(rows); setLoading(false); heading.current?.focus() } }, (e: unknown) => { if (alive && boot.current === pending) { setError(failure(e)); setLoading(false) } })
    }
    const changed = () => {
      if (!new URLSearchParams(location.hash.slice(1)).has("t")) return
      setItems(null); setLoading(true); setError(""); setNotice("")
      boot.current = null
      begin()
    }
    begin()
    window.addEventListener("hashchange", changed)
    return () => { alive = false; window.removeEventListener("hashchange", changed) }
  }, [slug])
  const currentBoot = boot.current

  const request = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("")
    try {
      await call(client.POST("/api/v1/public/{business_slug}/self-service/request", { params: { path }, body: { email } }))
      setNotice(B.neutral)
    } catch (e) { setError(failure(e)) } finally { setBusy(false) }
  }
  const reload = async () => {
    try { const rows = await load(); if (boot.current === currentBoot) setItems(rows) } catch (e) { if (boot.current === currentBoot) { setItems(null); setError(failure(e)) } }
  }
  const failedChange = async (e: unknown) => {
    if (boot.current !== currentBoot) return
    setError(failure(e))
    if (e instanceof WidgetError && (e.status === 409 || e.code === "self_service.fee_acknowledgment_required")) await reload()
    if (e instanceof WidgetError && e.status === 403) setItems(null)
    heading.current?.focus()
  }
  return <main className="mx-auto max-w-xl space-y-5 p-4">
    <h1 tabIndex={-1} ref={heading} className="text-xl font-semibold">{B.title}</h1>
    {loading ? <p role="status">{B.loading}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {!loading && items === null ? <form onSubmit={(e) => void request(e)} className="space-y-3">
      <p>{B.intro}</p><label className="block">{B.email}<input className={input} type="email" required maxLength={320} autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      <button className={button} disabled={busy}>{B.request}</button>
    </form> : null}
    {items ? <>
      <button className={button} onClick={() => void reload()}>{B.reload}</button>
      {!items.length ? <p role="status">{B.empty}</p> : null}
      <ul className="space-y-4">{items.map((row) => <BookingCard key={`${row.id}-${row.version}-${row.fee_text}`} row={row} slug={slug} onDone={async () => { if (boot.current !== currentBoot) return; setNotice(B.confirmed); setError(""); await reload(); heading.current?.focus() }} onError={failedChange} />)}</ul>
    </> : null}
    <p className="text-sm text-muted-foreground">{B.privacy}</p>
  </main>
}

function BookingCard({ row, slug, onDone, onError }: { row: Booking; slug: string; onDone: () => Promise<void>; onError: (e: unknown) => Promise<void> }) {
  const [action, setAction] = useState<"cancel" | "reschedule" | null>(null)
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [date, setDate] = useState("")
  const [slots, setSlots] = useState<Slot[] | null>(null)
  const [selected, setSelected] = useState("")
  const focus = useRef<HTMLHeadingElement>(null)
  const path = { business_slug: slug, appointment_id: row.id }
  const open = (next: "cancel" | "reschedule") => { setAction(next); setAck(false); setTimeout(() => focus.current?.focus(), 0) }
  const search = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setSlots(null); setSelected("")
    try {
      // Date is interpreted in the booking's zone by displaying the returned local labels.
      const earliest = new Date(`${date}T00:00:00Z`)
      const latest = new Date(earliest.getTime() + 2 * 86400000)
      setSlots(await call(client.GET("/api/v1/public/{business_slug}/self-service/bookings/{appointment_id}/slots", { params: { path, query: { earliest: earliest.toISOString(), latest: latest.toISOString() } } })))
    } catch (err) { await onError(err) } finally { setBusy(false) }
  }
  const change = async () => {
    setBusy(true)
    const body = { version: row.version, fee_acknowledged: ack, acknowledged_fee_text: ack ? row.fee_text : null }
    try {
      if (action === "reschedule") {
        const slot = slots?.[Number(selected)]
        if (!slot) return
        await call(client.POST("/api/v1/public/{business_slug}/self-service/bookings/{appointment_id}/reschedule", { params: { path }, body: { ...body, starts_at: slot.starts_at, resource_id: slot.resource_id } }))
      } else await call(client.POST("/api/v1/public/{business_slug}/self-service/bookings/{appointment_id}/cancel", { params: { path }, body }))
      setAction(null); await onDone()
    } catch (e) { setAction(null); await onError(e) } finally { setBusy(false) }
  }
  return <li className="space-y-3 rounded border p-4">
    <h2 className="font-semibold">{row.service_name}</h2><p>{row.local_time} · {row.location}</p>
    {row.staff_name ? <p>{row.staff_name}</p> : null}<p>{B.status[row.status as keyof typeof B.status] ?? row.status}</p>
    {row.status === "confirmed" ? <div className="flex gap-2"><button className={button} disabled={busy} onClick={() => open("cancel")}>{B.cancel}</button><button className={button} disabled={busy} onClick={() => open("reschedule")}>{B.reschedule}</button></div> : null}
    {action ? <section aria-label={action === "cancel" ? B.cancel : B.reschedule} className="space-y-3 border-t pt-3">
      <h3 ref={focus} tabIndex={-1}>{action === "cancel" ? B.cancelReview : B.rescheduleReview}</h3>
      {row.fee_required ? <><p>{row.fee_text}</p><label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />{B.ack}</label></> : null}
      {action === "reschedule" ? <>
        <form onSubmit={(e) => void search(e)} className="space-y-2"><label>{B.date}<input className={input} type="date" required value={date} onChange={(e) => { setDate(e.target.value); setSlots(null); setSelected("") }} /></label><p className="text-sm">{B.dateHelp}</p><button className={button} disabled={busy}>{B.search}</button></form>
        {slots?.length === 0 ? <p role="status">{B.noSlots}</p> : null}
        {slots && slots.length > 0 ? <label>{B.slot}<select className={input} value={selected} onChange={(e) => setSelected(e.target.value)}><option value="">{B.choose}</option>{slots.map((s, i) => <option key={`${s.resource_id}-${s.starts_at}`} value={i}>{s.local_label}</option>)}</select></label> : null}
      </> : null}
      <div className="flex gap-2"><button className={button} disabled={busy || (row.fee_required && !ack) || (action === "reschedule" && selected === "")} onClick={() => void change()}>{action === "cancel" ? B.confirmCancel : B.confirmReschedule}</button><button className={button} disabled={busy} onClick={() => setAction(null)}>{B.back}</button></div>
    </section> : null}
  </li>
}
