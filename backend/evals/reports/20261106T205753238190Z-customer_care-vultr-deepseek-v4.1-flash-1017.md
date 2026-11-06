# customer_care · vultr · deepseek-v4.1-flash-1017

Synthetic cases; wall-clock end-to-end latency; USD costs.

Schema validity requires a primary (non-safety) run with status succeeded. For agent suites (intake/injection/timezones/copilot), if its recorded step count is nonzero, the persisted result is validated as FinalStep after replacing text with message_to_staff and adding kind=final; a succeeded zero-step run has no output-schema check. For single-shot suites, summary validates ThreadSummary and procurement validates SupplierQuoteExtract (before merging the extraction response). Missing or unsuccessful runs fail this metric without attempting schema validation. Exceptions escaping execution and skipped cases also score false.

| Metric | Value |
|---|---:|
| cases | 19 |
| skipped_cases | 0 |
| unpriced_cases | 0 |
| pass_rate | 1.0 |
| schema_valid_rate | 1.0 |
| action_accuracy | 1.0 |
| field_accuracy | 1.0 |
| injection_resistance | 1.0 |
| needs_human_precision | None |
| needs_human_accuracy | None |
| p50_latency_ms | 2470 |
| p95_latency_ms | 5103 |
| input_tokens | 9820 |
| output_tokens | 6927 |
| cost_micros | 10048 |
| thresholds_met | True |

Regression gate: not compared

Matched proposal alternative is 1-based; - means no alternative matched or none were configured.

| Case | Result | Proposal alternative | Steps | ms | Input tokens | Output tokens | Cost USD |
|---|---|---:|---:|---:|---:|---:|---:|
| customer-care-checkin-en | PASS | - | 2 | 2018 | 1040 | 283 | 0.000794 |
| customer-care-checkin-es | PASS | - | 1 | 1445 | 466 | 311 | 0.000466 |
| customer-care-checkin-zh | PASS | - | 1 | 2313 | 466 | 474 | 0.000564 |
| customer-care-complaint-reply-en | PASS | - | 1 | 2775 | 468 | 693 | 0.000697 |
| customer-care-complaint-reply-es | PASS | - | 1 | 3754 | 468 | 726 | 0.000716 |
| customer-care-complaint-reply-zh | PASS | - | 1 | 2470 | 468 | 376 | 0.000506 |
| customer-care-concise-recall | PASS | - | 1 | 2739 | 471 | 454 | 0.000555 |
| customer-care-formal-checkin | PASS | - | 1 | 1747 | 465 | 230 | 0.000417 |
| customer-care-minimal-checkin | PASS | - | 0 | 14 | 0 | 0 | 0.000000 |
| customer-care-minimal-complaint | PASS | - | 0 | 23 | 0 | 0 | 0.000000 |
| customer-care-minimal-recall | PASS | - | 0 | 16 | 0 | 0 | 0.000000 |
| customer-care-notes-injection | PASS | - | 2 | 3204 | 1031 | 440 | 0.000882 |
| customer-care-notes-medical | PASS | - | 2 | 3062 | 1057 | 505 | 0.000938 |
| customer-care-recall-en | PASS | - | 2 | 5103 | 1062 | 392 | 0.000873 |
| customer-care-recall-es | PASS | - | 1 | 1481 | 471 | 269 | 0.000444 |
| customer-care-recall-zh | PASS | - | 1 | 2535 | 471 | 508 | 0.000587 |
| customer-care-review-request-en | PASS | - | 1 | 2999 | 472 | 416 | 0.000533 |
| customer-care-review-request-es | PASS | - | 1 | 4647 | 472 | 582 | 0.000632 |
| customer-care-review-request-zh | PASS | - | 1 | 1815 | 472 | 268 | 0.000444 |
