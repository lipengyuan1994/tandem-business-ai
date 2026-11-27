// Public pay page behind an invoice's pay link (/pay/<token>). Card details are entered only on Stripe's page.
import { useEffect, useState } from "react"
import S from "virtual:pay-copy"
import type { Schemas } from "@/api/types"
import { formatMoney } from "@/lib/money"
import { WidgetError, call, client } from "./api"

type Page = Schemas["PayPageOut"]
const button = "min-h-11 rounded-md bg-primary px-4 py-2 font-medium text-primary-foreground disabled:opacity-50"
const CONFIRM_TRIES = 10
// 409s that mean the invoice moved on (paid, on its way, voided, changed): show the page again. Any other 409
// (Stripe or the relay failed, a page already opening) is shown, so the customer is not left without a reason.
const INVOICE_MOVED = new Set(["payments.invoice_settled", "payments.payment_processing", "payments.not_payable", "payments.invoice_changed"])

function message(e: unknown) {
  if (e instanceof WidgetError) {
    if (e.status === 404) return S.expired
    if (e.status === 429) return S.rateLimited
    if (e.code === "payments.checkout_starting") return S.starting
  }
  return S.error
}

function returnedFromStripe() {
  return new URLSearchParams(globalThis.location?.search ?? "").get("paid") === "1"
}

export function PayPage({ token, pollMs = 3000 }: { token: string; pollMs?: number }) {
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const [payError, setPayError] = useState<unknown>(null)
  const confirming = returnedFromStripe() && page?.state === "payable" && attempt < CONFIRM_TRIES

  useEffect(() => {
    let alive = true
    call(client.GET("/api/v1/payments/pay/{token}", { params: { path: { token } } })).then(
      (d) => { if (alive) { setPage(d); setError(null) } },
      (e: unknown) => { if (alive) setError(e) },
    )
    return () => { alive = false }
  }, [token, attempt])

  // Back from Stripe: Stripe tells this server a moment later, so look again a few times.
  useEffect(() => {
    if (!confirming) return
    const timer = setTimeout(() => setAttempt((n) => n + 1), pollMs)
    return () => clearTimeout(timer)
  }, [confirming, attempt, pollMs])

  const amount = page ? formatMoney({ amount_minor: page.balance_minor, currency: page.currency }) : ""
  const business = page?.business_name ?? ""
  return <main className="mx-auto max-w-lg space-y-5 p-4 sm:p-6">
    <h1 className="text-xl font-semibold">{page ? S.title.replace("{business}", business) : S.heading}</h1>
    {error && !page ? <div className="space-y-3"><p role="alert">{message(error)}</p>{!(error instanceof WidgetError && error.status === 404) ? <button className={button} onClick={() => setAttempt((n) => n + 1)}>{S.retry}</button> : null}</div>
      : !page ? <p>{S.loading}</p>
      : <>
        {page.test_mode ? <p className="rounded-md border border-dashed p-3 text-sm">{S.testMode}</p> : null}
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border p-4 text-sm">
          <dt className="text-muted-foreground">{S.invoice}</dt><dd>{page.invoice_number ?? "—"}</dd>
          {page.due_date ? <><dt className="text-muted-foreground">{S.due}</dt><dd>{page.due_date}</dd></> : null}
          <dt className="text-muted-foreground">{S.total}</dt><dd>{formatMoney({ amount_minor: page.total_minor, currency: page.currency })}</dd>
          {page.state === "payable" ? <><dt className="font-medium">{S.balance}</dt><dd className="font-medium">{amount}</dd></> : null}
        </dl>
        {page.state === "paid" ? <p role="status" className="rounded-md border p-4">{S.paid}</p>
          : page.state === "processing" ? <p role="status" className="rounded-md border p-4">{S.processing}</p>
          : page.state === "unavailable" ? <p role="status" className="rounded-md border p-4">{S.unavailable.replace("{business}", business)}</p>
          : confirming ? <p role="status" className="rounded-md border p-4">{S.confirming}</p>
          : <div className="space-y-3">
            <button className={button} disabled={busy} onClick={() => {
              setBusy(true); setPayError(null)
              call(client.POST("/api/v1/payments/pay/{token}/checkout", { params: { path: { token } } })).then(
                (r) => { globalThis.location.assign(r.url) },
                (e: unknown) => {
                  setBusy(false)
                  if (e instanceof WidgetError && e.status === 409 && INVOICE_MOVED.has(e.code)) { setAttempt((n) => n + 1); return }
                  setPayError(e)
                },
              )
            }}>{busy ? S.opening : S.pay.replace("{amount}", amount)}</button>
            <p className="text-sm text-muted-foreground">{S.secure.replace("{business}", business)}</p>
            {payError ? <p role="alert">{message(payError)}</p> : null}
          </div>}
      </>}
  </main>
}
