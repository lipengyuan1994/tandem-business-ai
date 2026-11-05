# AI module review by a second model (GPT-6.1 Sol, read-only), 2026-11-03

Findings as reported; fixes are tracked in EVENT_WORK.md and the batch reports. Links point at the code as it was when reviewed.

I found **16 defects**, ranked below. The first four are production blockers. These go beyond the earlier review’s accepted prompt-history and concurrent-budget risks.

1. **High — `propose_reorder` bypasses stock-read permissions.**  
   [tools/proposals.py:270](../../backend/app/modules/ai/tools/proposals.py:270)  
   Copilot exposes this tool to anyone with `ai.assistant.use`. Its internal inventory lookup does not require stock-read permission, and its response reveals the item’s computed reorder quantity in the title. I reproduced `Reorder Example Widget ×57` with no stock-read permission. **Fix:** require the relevant inventory read permissions before lookup and disclosure, including when selecting copilot tools.

2. **High — Procurement prices always assume two decimal places.**  
   [usecases/procurement.py:315](../../backend/app/modules/ai/usecases/procurement.py:315), [usecases/procurement.py:486](../../backend/app/modules/ai/usecases/procurement.py:486)  
   Verification and conversion hard-code multiplication by 100, although platform money supports currency-specific exponents. A source price of **1200 JPY becomes 120000 JPY**; a valid **1.234 KWD** is rejected. Both reproduced. **Fix:** resolve the verified currency first, then use `currency_exponent()` for exact validation and conversion.

3. **High — Rejected currency evidence silently becomes the business currency.**  
   [usecases/procurement.py:339](../../backend/app/modules/ai/usecases/procurement.py:339), [usecases/procurement.py:462](../../backend/app/modules/ai/usecases/procurement.py:462)  
   For a EUR source and incorrectly extracted USD, verification clears currency to `None` but still sets `verified=true`. The proposal tool then defaults to the business currency, potentially recording EUR prices as USD. **Fix:** distinguish absent currency from rejected currency; block proposal creation when supplied currency evidence fails verification.

4. **High — Intake does not atomically enforce one proposal per message.**  
   [usecases/intake.py:173](../../backend/app/modules/ai/usecases/intake.py:173), [tools/proposals.py:82](../../backend/app/modules/ai/tools/proposals.py:82)  
   Concurrent attempts can both pass the initial guard and propose different slots. Different booking arguments produce different effect keys, so uniqueness does not prevent both commits. Sequentially, a run can also propose a reply and then a booking: the booking guard checks only existing bookings. **Fix:** claim a unique intake outcome by business/message inside the proposal transaction, covering both action types.

5. **Medium — Briefing caches survive permission removal.**  
   [usecases/briefing.py:56](../../backend/app/modules/ai/usecases/briefing.py:56)  
   Cache identity includes business, user and hour, but not permissions. After stock or scheduling access is removed, a user retaining AI access can retrieve the earlier unrestricted briefing. The cache branch returns before permission-filtered fact gathering. **Fix:** include an authorization fingerprint/version in cache identity and reject entries generated under different permissions.

6. **Medium — Payment follow-up’s cooldown check races with proposal creation.**  
   [usecases/payment_followup.py:101](../../backend/app/modules/ai/usecases/payment_followup.py:101), [tools/sales.py:53](../../backend/app/modules/ai/tools/sales.py:53)  
   The freshness/cooldown check commits before the tool transaction. If a manual reminder is sent in between, the tool reloads the newer invoice and creates another proposal using its new version, without rechecking cooldown. Downstream version validation therefore accepts it. **Fix:** check cooldown, pending proposals and the expected invoice version under a lock in the same transaction that creates the proposal.

7. **Medium — Numeric verification accepts numbers from unrelated fields.**  
   [usecases/procurement.py:299](../../backend/app/modules/ai/usecases/procurement.py:299), [usecases/briefing.py:38](../../backend/app/modules/ai/usecases/briefing.py:38)  
   Verification uses a global bag of numbers, losing field, item and unit associations. I reproduced acceptance of unit price `0` from “Unit price USD 11.40. Availability 0.” Writer checks likewise accept “a discount of 5%” when the only fact is stock quantity `5`; “a dozen” bypasses the number-word check. **Fix:** validate field-specific source evidence and render numerical claims deterministically from typed references.

8. **Medium — Fallback resets the effective timeout, and late results can succeed.**  
   [gateway/router.py:89](../../backend/app/modules/ai/gateway/router.py:89), [agent/runner.py:100](../../backend/app/modules/ai/agent/runner.py:100)  
   Every fallback receives the original timeout. The caller does not recheck elapsed time after completion, before accepting a final answer or executing a tool. A simulated five-second run succeeded after eight seconds, with both attempts receiving five seconds. **Fix:** propagate an absolute deadline, recompute remaining time per attempt, and check it before processing returned actions.

9. **Medium — Business scans can outlive their worker lease and execute concurrently.**  
   [usecases/procurement.py:649](../../backend/app/modules/ai/usecases/procurement.py:649), [usecases/payment_followup.py:85](../../backend/app/modules/ai/usecases/payment_followup.py:85)  
   A single job performs sequential inference for every target, but the platform lease lasts five minutes without renewal. Ten thirty-second calls already reach that boundary; the reaper can start another attempt while the original continues spending tokens. One persistently failing target also aborts processing of later targets. **Fix:** fan out independently deduplicated, bounded per-target jobs, or implement lease renewal and item-level failure isolation.

10. **Medium — Concurrent copilot turns have no conversation ordering guarantee.**  
    [usecases/copilot.py:148](../../backend/app/modules/ai/usecases/copilot.py:148), [usecases/copilot.py:222](../../backend/app/modules/ai/usecases/copilot.py:222)  
    Two turns can generate simultaneously from histories that omit the preceding answer. Their answers commit in completion order, so a slow earlier answer can appear after the later answer and contaminate subsequent history. **Fix:** persist turn sequencing and allow one active generation per thread, or explicitly associate and order responses by their originating turn.

11. **Medium — Retrying an interrupted HTTP operation creates a new effect namespace.**  
    [usecases/copilot.py:157](../../backend/app/modules/ai/usecases/copilot.py:157), [usecases/procurement.py:436](../../backend/app/modules/ai/usecases/procurement.py:436)  
    Copilot creates a new message ID on every POST; extraction and summary generate random trigger keys. If a proposal commits but its response is lost, retrying the same request creates another proposal because effect deduplication is scoped to the new trigger. **Fix:** accept and persist a client operation/idempotency key, and return or resume the existing operation on retry.

12. **Medium — Input-token limits are not actually enforced.**  
    [structured.py:74](../../backend/app/modules/ai/structured.py:74), [agent/context.py:39](../../backend/app/modules/ai/agent/context.py:39)  
    Structured completions never apply the input limit. Agent trimming cannot reduce an oversized initial task/history, and repair requests append output without another trim. Thirty long conversation messages can substantially exceed the configured limit. **Fix:** enforce a shared pre-call size check, including schema overhead and repairs; trim source records at safe boundaries or reject oversized input explicitly.

13. **Medium — Effect replay reports expired holds as successful.**  
    [tools/registry.py:147](../../backend/app/modules/ai/tools/registry.py:147), [tools/proposals.py:105](../../backend/app/modules/ai/tools/proposals.py:105)  
    A retry after a hold expires receives its old ID without checking appointment state. It can then repeatedly fail booking, or bind an expired-but-not-yet-reaped hold because the booking check omits expiration. **Fix:** validate hold liveness during replay and binding; return an explicit expired observation and support safe reacquisition.

14. **Medium — Unexpected exceptions leave runs permanently `running`.**  
    [agent/runner.py:244](../../backend/app/modules/ai/agent/runner.py:244), [tools/scheduling.py:43](../../backend/app/modules/ai/tools/scheduling.py:43)  
    The runner catches only `StopRun`. A schema-valid `find_slots` date near year 9999 overflows when fourteen days are added; I reproduced an uncaught `OverflowError` with no `close_run` call. Cleanup requires a sufficiently late retry of the same trigger; structured runs have no equivalent recovery. **Fix:** bound date inputs, close unexpected failures safely, and add stale-run reconciliation independent of trigger retries.

15. **Medium — Disconnected SSE requests leave queued executor work active.**  
    [usecases/copilot.py:259](../../backend/app/modules/ai/usecases/copilot.py:259)  
    Generator cleanup sets `stopped` but never cancels the executor future. I reproduced a queued worker starting after cancellation. It still performs run initialization and database reads before reaching the router’s stop check. A burst of abandoned streams accumulates work in the shared executor. **Fix:** cancel pending futures, check cancellation at worker entry, and bound submitted work. The event queue itself is bounded indirectly by the step limit.

16. **Medium — Repeated full-list/history reads create quadratic work.**  
    [usecases/payment_followup.py:102](../../backend/app/modules/ai/usecases/payment_followup.py:102), [tools/purchasing.py:297](../../backend/app/modules/ai/tools/purchasing.py:297), [usecases/intake.py:155](../../backend/app/modules/ai/usecases/intake.py:155)  
    Payment follow-up reloads up to 500 invoices twice per generated reminder. Supplier follow-up reloads all late orders per target, including their line queries. Intake loads the entire conversation for every outcome-marker check. These costs grow substantially for ordinary accumulated business data. **Fix:** use scoped point reads, batch pending-proposal checks, and a dedicated indexed outcome-marker lookup.

Reviewed all non-test AI code and the supplied context. Targeted reproductions used real functions with in-memory database/provider doubles. **PostgreSQL races, live-provider behavior and production RLS were not tested.** I found no proven cross-business RLS escape.

`make check` stopped before linting because the read-only sandbox denied uv cache access. No files were changed.