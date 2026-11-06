import { type FormEvent, useEffect, useRef, useState } from "react"
import { type Profile, type PublicService, type PublicThread, WidgetError, call, client } from "./api"
import { S } from "./strings"

const input = "min-w-0 min-h-11 mt-1 block w-full rounded-md border bg-background px-3 py-2 text-sm"
const button = "min-h-11 shrink-0 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"

function message(e: unknown): string {
  if (e instanceof WidgetError) {
    if (e.status === 429) return S.rateLimited
    if (e.code === "conversations.public_chat_disabled") return S.chatDisabled
  }
  return S.errorGeneric
}

function ContactConsent({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return <>
    <label className="flex min-h-11 items-center gap-3 text-sm"><input aria-describedby="consent-help" className="size-6 shrink-0" type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />{S.consent}</label>
    <p id="consent-help" className="text-sm text-muted-foreground">{S.consentHelp}</p>
  </>
}

export function Widget({ slug, page, compact = false }: { slug: string; page: "chat" | "request"; compact?: boolean }) {
  const [profile, setProfile] = useState<Profile | null | undefined>(undefined)
  const [profileError, setProfileError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    call(client.GET("/api/v1/public/{business_slug}/profile", { params: { path: { business_slug: slug } } })).then((value) => { setProfile(value); setProfileError(null) }, (e: unknown) => { setProfileError(e instanceof WidgetError && e.status === 404 ? S.notFound : message(e)); setProfile(null) })
  }, [slug, attempt])

  if (profile === undefined) return <p className="p-6 text-sm">{S.loading}</p>
  if (profile === null) return <main className="space-y-3 p-6"><h1 className="text-xl font-semibold">{S.contactTitle}</h1><p role="alert">{profileError}</p><button className={button} onClick={() => setAttempt(attempt + 1)}>{S.retry}</button></main>
  return (
    <main className={compact ? "space-y-3 p-3" : "mx-auto max-w-lg space-y-6 p-4 sm:p-6"}>
      <h1 className="text-xl font-semibold">{page === "chat" ? S.chatTitle(profile.name) : S.requestTitle(profile.name)}</h1>
      {page === "chat" && profile.avoid_medical_details ? <p role="note" className="rounded-md border p-3 text-sm">Please do not send medical details here. We will contact you to discuss them.</p> : null}
      {page === "chat" ? <Chat key={slug} slug={slug} businessName={profile.name} greeting={profile.ai_greeting} /> : <Request slug={slug} />}
      <aside aria-labelledby="privacy-h" className="rounded-md border p-3 text-sm text-muted-foreground">
        <h2 id="privacy-h" className="mb-1 font-medium text-foreground">{S.privacyTitle}</h2>
        <p>{S.privacy[profile.ai_policy]}</p>
        <p>{S.privacyStorage}</p>
      </aside>
    </main>
  )
}

function Chat({ slug, businessName, greeting }: { slug: string; businessName: string; greeting: string }) {
  const path = { params: { path: { business_slug: slug } } }
  const [thread, setThread] = useState<PublicThread | null>(null)
  const current = useRef<PublicThread | null>(null)
  const accept = (incoming: PublicThread) => {
    const previous = current.current?.conversation_id === incoming.conversation_id ? current.current : null
    const messages = [...new Map([...(previous?.messages ?? []), ...incoming.messages].map((m) => [m.id, m])).values()]
      .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).slice(-200)
    const last = incoming.messages.at(-1)
    const oldLast = previous?.messages.at(-1)
    const advances = last && (!oldLast || last.created_at > oldLast.created_at || (last.created_at === oldLast.created_at && last.id >= oldLast.id))
    const merged = { ...incoming, messages, next_cursor: advances ? incoming.next_cursor : previous?.next_cursor ?? incoming.next_cursor }
    current.current = merged
    setThread(merged)
  }
  const [checked, setChecked] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ name: "", email: "", message: "", consent: false })
  const [reply, setReply] = useState("")

  // Resume an existing conversation (visitor cookie), then poll for staff replies.
  useEffect(() => {
    let alive = true
    const load = () => call(client.GET("/api/v1/public/{business_slug}/chat", { params: { path: { business_slug: slug }, query: { cursor: current.current?.next_cursor ?? undefined } } }))
      .then((t) => { if (alive) { accept(t); setLoadError(null) } }, (e: unknown) => { if (alive) setLoadError(e instanceof WidgetError && e.code === "conversations.visitor_token_invalid" && !current.current ? null : message(e)) }).finally(() => alive && setChecked(true))
    void load()
    const timer = setInterval(load, 5000)
    return () => { alive = false; clearInterval(timer) }
  }, [slug, attempt])

  const run = async (fn: () => Promise<PublicThread>) => {
    setBusy(true); setError(null)
    try { accept(await fn()) } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  const start = (e: FormEvent) => {
    e.preventDefault()
    void run(() => call(client.POST("/api/v1/public/{business_slug}/chat", { ...path, body: {
      name: form.name, email: form.email || null, phone: null, message: form.message, consent_to_contact: form.consent } })))
  }
  const send = (e: FormEvent) => {
    e.preventDefault()
    if (!reply.trim()) return
    void run(async () => { const t = await call(client.POST("/api/v1/public/{business_slug}/chat/messages", { params: { path: { business_slug: slug }, query: { cursor: current.current?.next_cursor ?? undefined } }, body: { body: reply.trim() } })); setReply(""); return t })
  }

  if (!checked) return <p className="text-sm">{S.loading}</p>
  return (
    <section className="min-w-0 space-y-4">
      <p className="rounded-md border p-3 text-sm">{thread?.ai_greeting ?? greeting}</p>
      {loadError ? <div role="alert"><p>{loadError}</p><button type="button" className={button} onClick={() => setAttempt(attempt + 1)}>{S.retry}</button></div> : null}
      {error ? <p role="alert" className="rounded-md border border-destructive p-3 text-sm">{error}</p> : null}
      {thread ? (
        <>
          <button type="button" className={button} disabled={busy} onClick={() => void run(() => call(client.POST("/api/v1/public/{business_slug}/chat/handoff", path)))}>{S.talkToPerson}</button>
          <ol aria-live="polite" aria-label={S.conversation} className="space-y-2">
            {thread.messages.map((m) => (
              <li key={m.id} data-system={m.is_system ? "true" : undefined} className={m.is_system ? "mx-2 rounded-lg border border-dashed bg-accent px-3 py-2 text-center text-sm" : m.from_business ? "mr-8 rounded-lg bg-muted px-3 py-2 text-sm" : "ml-8 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"}>
                <span className="block text-xs opacity-75">{m.is_system ? S.system : m.from_business ? S.business(businessName) : S.you}</span>
                <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{m.body}</p>
              </li>
            ))}
          </ol>
          <form onSubmit={send} className="flex gap-2">
            <label className="sr-only" htmlFor="w-reply">{S.message}</label>
            <input id="w-reply" className={input} value={reply} onChange={(e) => setReply(e.target.value)} maxLength={4000} />
            <button className={button} disabled={busy}>{S.send}</button>
          </form>
        </>
      ) : (
        <form onSubmit={start} className="space-y-3">
          <label className="block text-sm">{S.name}<input className={input} required maxLength={200} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="block text-sm">{S.email}<input className={input} type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
          <label className="block text-sm">{S.message}<textarea className={input} required rows={4} maxLength={4000} value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} /></label>
          <ContactConsent checked={form.consent} onChange={(consent) => setForm({ ...form, consent })} />
          <button className={button} disabled={busy}>{S.start}</button>
        </form>
      )}
    </section>
  )
}

interface Window { date: string; time_from: string; time_to: string }

function Request({ slug }: { slug: string }) {
  const path = { params: { path: { business_slug: slug } } }
  const [services, setServices] = useState<PublicService[]>([])
  const [servicesError, setServicesError] = useState<string | null>(null)
  const [servicesLoading, setServicesLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const [form, setForm] = useState({ service_id: "", name: "", email: "", phone: "", note: "", consent: false })
  const [windows, setWindows] = useState<Window[]>([{ date: "", time_from: "09:00", time_to: "12:00" }])
  const [done, setDone] = useState<{ message: string; reply_url?: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    call(client.GET("/api/v1/public/{business_slug}/services", { params: { path: { business_slug: slug } } })).then((value) => { setServices(value); setServicesError(null) }, (e: unknown) => setServicesError(message(e))).finally(() => setServicesLoading(false))
  }, [slug, attempt])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!form.email && !form.phone) return setError(S.contactRequired)
    setBusy(true); setError(null)
    try {
      const r = await call(client.POST("/api/v1/public/{business_slug}/appointment-requests", { ...path, body: {
        service_id: form.service_id, name: form.name, email: form.email || null, phone: form.phone || null, note: form.note || null,
        consent_to_contact: form.consent,
        preferred: windows.filter((w) => w.date) } }))
      setDone(r)
    } catch (err) { setError(message(err)) } finally { setBusy(false) }
  }
  const setWindow = (i: number, patch: Partial<Window>) => setWindows(windows.map((w, j) => (j === i ? { ...w, ...patch } : w)))

  if (done) return <div className="space-y-3"><p role="status" className="rounded-md border p-4 text-sm">{done.message}</p>
    {done.reply_url ? <a className="text-sm underline" href={done.reply_url}>View replies and send a message</a> : null}</div>
  return (
    <form onSubmit={submit} className="space-y-3">
      {error ? <p role="alert" className="rounded-md border border-destructive p-3 text-sm">{error}</p> : null}
      {servicesError ? <div role="alert"><p>{servicesError}</p><button type="button" className={button} onClick={() => { setServicesLoading(true); setAttempt(attempt + 1) }}>{S.retry}</button></div> : servicesLoading ? <p role="status">{S.loading}</p> : null}
      <label className="block text-sm">{S.service}
        <select className={input} required value={form.service_id} onChange={(e) => setForm({ ...form, service_id: e.target.value })}>
          <option value="">{S.chooseService}</option>
          {services.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.duration_minutes} min)</option>)}
        </select>
      </label>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">{S.preferred}</legend>
        {windows.map((w, i) => (
          <div key={i} className="grid grid-cols-3 gap-2">
            <label className="text-xs">{S.date}<input className={input} type="date" required={i === 0} value={w.date} onChange={(e) => setWindow(i, { date: e.target.value })} /></label>
            <label className="text-xs">{S.from}<input className={input} type="time" value={w.time_from} onChange={(e) => setWindow(i, { time_from: e.target.value })} /></label>
            <label className="text-xs">{S.to}<input className={input} type="time" value={w.time_to} onChange={(e) => setWindow(i, { time_to: e.target.value })} /></label>
          </div>
        ))}
        {windows.length < 3 ? <button type="button" className="text-sm underline" onClick={() => setWindows([...windows, { date: "", time_from: "09:00", time_to: "17:00" }])}>{S.addWindow}</button> : null}
      </fieldset>
      <label className="block text-sm">{S.name}<input className={input} required maxLength={200} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
      <label className="block text-sm">{S.email}<input className={input} type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
      <label className="block text-sm">{S.phone}<input className={input} type="tel" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></label>
      <label className="block text-sm">{S.note}<textarea className={input} rows={3} maxLength={2000} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
      <ContactConsent checked={form.consent} onChange={(consent) => setForm({ ...form, consent })} />
      <button className={button} disabled={busy || servicesLoading || !!servicesError}>{S.submitRequest}</button>
    </form>
  )
}
