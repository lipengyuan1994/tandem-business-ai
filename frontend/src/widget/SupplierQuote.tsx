import { useState } from "react"
import S from "virtual:supplier-quote-copy"
import { WidgetError } from "./api"
import { useSupplierRequest, submitSupplierQuote } from "./hooks"
import { QuoteFields } from "./QuoteFields"
import { blankQuote, quoteInput, validQuote } from "./quote-model"
const button = "min-h-11 rounded-md bg-primary px-4 py-2 font-medium text-primary-foreground disabled:opacity-50"
function message(e: unknown) {
  if (e instanceof WidgetError) {
    if (e.code === "purchasing.invalid_quote_token" || e.status === 404) return S.expired
    if (e.status === 409) return S.closed
    if (e.status === 429) return S.rateLimited
    if (e.status === 422) return S.validation
  }
  return S.error
}
export function SupplierQuote({ token }: { token: string }) {
  const request = useSupplierRequest(token)
  const [draft, setDraft] = useState(blankQuote)
  const [decline, setDecline] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState(false)
  const data = request.data
  const terminal = error instanceof WidgetError && (error.code === "purchasing.invalid_quote_token" || error.status === 404 || error.status === 409)
  return <main className="mx-auto max-w-lg space-y-5 p-4 sm:p-6">
    <h1 className="text-xl font-semibold">{data ? S.title.replace("{business}", data.business_name) : S.heading}</h1>
    {request.loading ? <p>{S.loading}</p> : request.error ? <div className="space-y-3"><p role="alert">{message(request.error)}</p><button className={button} onClick={request.retry}>{S.retry}</button></div> : data ? <>
      <p>{S.needed}: {data.needed_by} · {S.reply}: {data.reply_by}</p>
      {data.message ? <p className="whitespace-pre-wrap">{data.message}</p> : null}
      {done ? <p role="status" className="rounded-md border p-4">{S.thanks}</p> : <form className="space-y-4" onSubmit={(e) => {
        e.preventDefault()
        if (busy || terminal || (!decline && !validQuote(draft, data.lines, data.currency))) return
        setBusy(true); setError(null)
        void submitSupplierQuote(token, decline ? { declined: true } : { ...quoteInput(draft, data.lines, data.currency), declined: false }).then(() => setDone(true), (e: unknown) => setError(e)).finally(() => setBusy(false))
      }}>
        <fieldset disabled={busy || terminal} className="min-w-0 space-y-4"><legend className="sr-only">{S.heading}</legend>
          <label className="flex min-h-11 items-center gap-3"><input type="checkbox" className="size-5" checked={decline} onChange={(e) => setDecline(e.target.checked)} />{S.decline}</label>
          {decline ? <ul>{data.lines.map((l) => <li key={l.item_id}>{l.name} × {l.quantity}</li>)}</ul> : <QuoteFields draft={draft} onChange={setDraft} lines={data.lines} currency={data.currency} errors={error instanceof WidgetError ? error.fields : undefined} />}
        </fieldset>
        {error ? <p role="alert">{message(error)}</p> : null}
        <button className={button} disabled={busy || terminal || (!decline && !validQuote(draft, data.lines, data.currency))}>{busy ? S.sending : decline ? S.sendResponse : S.send}</button>
      </form>}
    </> : null}
  </main>
}
