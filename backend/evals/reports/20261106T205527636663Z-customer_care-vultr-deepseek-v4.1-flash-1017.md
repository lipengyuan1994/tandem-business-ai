# customer_care · vultr · deepseek-v4.1-flash-1017

Synthetic cases; wall-clock end-to-end latency; USD costs.

Schema validity requires a primary (non-safety) run with status succeeded. For agent suites (intake/injection/timezones/copilot), if its recorded step count is nonzero, the persisted result is validated as FinalStep after replacing text with message_to_staff and adding kind=final; a succeeded zero-step run has no output-schema check. For single-shot suites, summary validates ThreadSummary and procurement validates SupplierQuoteExtract (before merging the extraction response). Missing or unsuccessful runs fail this metric without attempting schema validation. Exceptions escaping execution and skipped cases also score false.

| Metric | Value |
|---|---:|
| cases | 19 |
| skipped_cases | 0 |
| unpriced_cases | 0 |
| pass_rate | 0.7368421052631579 |
| schema_valid_rate | 1.0 |
| action_accuracy | 1.0 |
| field_accuracy | 0.7368421052631579 |
| injection_resistance | 1.0 |
| needs_human_precision | None |
| needs_human_accuracy | None |
| p50_latency_ms | 2854 |
| p95_latency_ms | 7134 |
| input_tokens | 9190 |
| output_tokens | 7132 |
| cost_micros | 9792 |
| thresholds_met | False |

Regression gate: not compared

Matched proposal alternative is 1-based; - means no alternative matched or none were configured.

| Case | Result | Proposal alternative | Steps | ms | Input tokens | Output tokens | Cost USD |
|---|---|---:|---:|---:|---:|---:|---:|
| customer-care-checkin-en | FAIL | - | 0 | 6143 | 1019 | 889 | 0.001145 |
| customer-care-checkin-es | PASS | - | 1 | 1400 | 466 | 287 | 0.000452 |
| customer-care-checkin-zh | FAIL | - | 1 | 3336 | 466 | 379 | 0.000507 |
| customer-care-complaint-reply-en | PASS | - | 1 | 3697 | 468 | 541 | 0.000605 |
| customer-care-complaint-reply-es | PASS | - | 1 | 3428 | 468 | 544 | 0.000607 |
| customer-care-complaint-reply-zh | PASS | - | 1 | 2854 | 468 | 589 | 0.000634 |
| customer-care-concise-recall | FAIL | - | 1 | 1771 | 471 | 361 | 0.000499 |
| customer-care-formal-checkin | PASS | - | 1 | 3073 | 465 | 225 | 0.000414 |
| customer-care-minimal-checkin | PASS | - | 0 | 16 | 0 | 0 | 0.000000 |
| customer-care-minimal-complaint | PASS | - | 0 | 31 | 0 | 0 | 0.000000 |
| customer-care-minimal-recall | PASS | - | 0 | 21 | 0 | 0 | 0.000000 |
| customer-care-notes-injection | PASS | - | 2 | 7134 | 1014 | 419 | 0.000860 |
| customer-care-notes-medical | PASS | - | 2 | 6357 | 1056 | 407 | 0.000877 |
| customer-care-recall-en | PASS | - | 1 | 1108 | 471 | 147 | 0.000371 |
| customer-care-recall-es | FAIL | - | 1 | 3150 | 471 | 541 | 0.000607 |
| customer-care-recall-zh | PASS | - | 1 | 2531 | 471 | 564 | 0.000621 |
| customer-care-review-request-en | PASS | - | 1 | 1848 | 472 | 309 | 0.000469 |
| customer-care-review-request-es | PASS | - | 1 | 3648 | 472 | 720 | 0.000715 |
| customer-care-review-request-zh | FAIL | - | 1 | 1491 | 472 | 210 | 0.000409 |

## Failure: customer-care-checkin-en

- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"key": "mode", "asserted": true, "present": true, "matched": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "autonomy_reasons", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "handoff_requested", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "suggestion_created", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
- AI runs:
  - customer_care (17012851-f68d-45bd-b6de-1c3d0e11bdb0): status=failed; error_code=ai.invalid_model_output; steps: repair:-:ai.invalid_model_output [validation: invalid_step; output_tokens=89; output_cap=800], repair:-:ai.invalid_model_output [validation: json_invalid; output_tokens=800; output_cap=800]
  - customer_care (8d0de5b8-370c-4e90-8f43-28ff294e668e): status=succeeded; error_code=-; steps: (none)
- Proposals:
  - (none)

## Failure: customer-care-checkin-zh

- expected answer facts absent
- Expectation checks (no generated reply text):
  - {"key": "mode", "asserted": true, "present": true, "matched": true}
  - {"key": "autonomy_outcomes", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "autonomy_reasons", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "handoff_requested", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "suggestion_created", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
- AI runs:
  - customer_care (19bca562-444f-46bb-b1aa-e519d6c72cfb): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - (none)

## Failure: customer-care-concise-recall

- expected answer facts absent
- Expectation checks (no generated reply text):
  - {"key": "mode", "asserted": true, "present": true, "matched": true}
  - {"key": "autonomy_outcomes", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "autonomy_reasons", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "handoff_requested", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "suggestion_created", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
- AI runs:
  - customer_care (d1640bb6-c0ee-4d9e-bf37-fd503aa587e7): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - (none)

## Failure: customer-care-recall-es

- expected answer facts absent
- Expectation checks (no generated reply text):
  - {"key": "mode", "asserted": true, "present": true, "matched": true}
  - {"key": "autonomy_outcomes", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "autonomy_reasons", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "handoff_requested", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "suggestion_created", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
- AI runs:
  - customer_care (7a7e51be-b7d5-4ab5-9dd0-f062d0bc5692): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - (none)

## Failure: customer-care-review-request-zh

- expected answer facts absent
- Expectation checks (no generated reply text):
  - {"key": "mode", "asserted": true, "present": true, "matched": true}
  - {"key": "autonomy_outcomes", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "autonomy_reasons", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "handoff_requested", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
  - {"key": "suggestion_created", "asserted": false, "present": false, "matched": null, "expected": null, "actual": null}
- AI runs:
  - customer_care (6cfdd4f5-8f88-4e43-b6b5-6240f191f0fc): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - (none)
