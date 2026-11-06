# timezones · local · gpt-oss-20b

Synthetic cases; wall-clock end-to-end latency; USD costs.

Schema validity requires a primary (non-safety) run with status succeeded. For agent suites (intake/injection/timezones/copilot), if its recorded step count is nonzero, the persisted result is validated as FinalStep after replacing text with message_to_staff and adding kind=final; a succeeded zero-step run has no output-schema check. For single-shot suites, summary validates ThreadSummary and procurement validates SupplierQuoteExtract (before merging the extraction response). Missing or unsuccessful runs fail this metric without attempting schema validation. Exceptions escaping execution and skipped cases also score false.

| Metric | Value |
|---|---:|
| cases | 10 |
| skipped_cases | 0 |
| unpriced_cases | 10 |
| pass_rate | 0.0 |
| schema_valid_rate | 0.5 |
| action_accuracy | 0.2 |
| field_accuracy | 0.0 |
| injection_resistance | 1.0 |
| needs_human_precision | 0.0 |
| needs_human_accuracy | 0.9 |
| p50_latency_ms | 43107.0 |
| p95_latency_ms | 68130 |
| input_tokens | 130030 |
| output_tokens | 13391 |
| cost_micros | None |
| thresholds_met | False |

Regression gate: not compared

Matched proposal alternative is 1-based; - means no alternative matched or none were configured.

| Case | Result | Proposal alternative | Steps | ms | Input tokens | Output tokens | Cost USD |
|---|---|---:|---:|---:|---:|---:|---:|
| timezones-denver-afternoon | FAIL | - | 2 | 55510 | 9331 | 1629 | unpriced |
| timezones-denver-dst-after | FAIL | - | 5 | 68130 | 27718 | 2337 | unpriced |
| timezones-denver-dst-before | FAIL | - | 2 | 21415 | 10792 | 561 | unpriced |
| timezones-denver-next-tuesday | FAIL | - | 2 | 43496 | 9322 | 1629 | unpriced |
| timezones-denver-spring-before | FAIL | - | 3 | 26584 | 18068 | 645 | unpriced |
| timezones-london-afternoon | FAIL | - | 3 | 25884 | 15100 | 719 | unpriced |
| timezones-london-dst-after | FAIL | - | 3 | 62607 | 13309 | 2097 | unpriced |
| timezones-london-dst-before | FAIL | - | 2 | 43156 | 8729 | 1627 | unpriced |
| timezones-london-next-wednesday | FAIL | - | 2 | 16706 | 8897 | 518 | unpriced |
| timezones-london-spring-before | FAIL | - | 2 | 43058 | 8764 | 1629 | unpriced |

## Failure: timezones-denver-afternoon

- primary AI run status failed (schema validation not attempted)
- status: expected succeeded, got failed
- proposal action types/count differ
- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "service", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "customer", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "starts_at_local_between", "matched": false, "present": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
- AI runs:
  - safety (49c72f4b-566f-44ff-bd58-66594986dbc8): status=succeeded; error_code=-; steps: completion:-:-
  - intake (ecec3ebd-f549-46a7-8e0d-1079abb55591): status=failed; error_code=ai.invalid_model_output; steps: repair:-:ai.invalid_model_output [validation: json_invalid], repair:-:ai.invalid_model_output [validation: json_invalid]
- Proposals:
  - (none)

## Failure: timezones-denver-dst-after

- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": 1, "key": "service", "matched": true, "present": true}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": 1, "key": "customer", "matched": true, "present": true}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": 1, "key": "starts_at_local_between", "matched": false, "present": true}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
- AI runs:
  - intake (6403e73e-fe9e-4559-8872-284d191b1453): status=succeeded; error_code=-; steps: tool_call:find_slots:-, tool_call:hold_slot:-, repair:-:ai.invalid_model_output [validation: json_invalid], tool_call:propose_booking:-, final:-:-
  - safety (7306a772-9fa7-4445-89b6-95ad5c720a39): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - scheduling.book_appointment: Book Brake service for Ada Example, Sat 07 Nov 09:00 MST; run_id=6403e73e-fe9e-4559-8872-284d191b1453

## Failure: timezones-denver-dst-before

- proposal action types/count differ
- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "service", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "customer", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "starts_at_local_between", "matched": false, "present": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": ["fell_back"]}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": ["ungrounded"]}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": true}
  - {"actual_proposal": 1, "key": "grounding.verdict", "asserted": false, "actual": "unsupported"}
- AI runs:
  - safety (028a7f7b-5a06-4feb-9014-2cacc68010b3): status=succeeded; error_code=-; steps: completion:-:-
  - grounding (4eda9187-3784-4fe8-8325-e8aa3e4a3d9b): status=succeeded; error_code=-; steps: completion:-:-
  - intake (68bd99c5-28c7-4160-8fc2-f3e3bd7c0132): status=succeeded; error_code=-; steps: tool_call:propose_reply:-, final:-:-
- Proposals:
  - conversations.send_reply: Reply to Ada (web chat); run_id=68bd99c5-28c7-4160-8fc2-f3e3bd7c0132

## Failure: timezones-denver-next-tuesday

- primary AI run status failed (schema validation not attempted)
- status: expected succeeded, got failed
- proposal action types/count differ
- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "service", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "customer", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "starts_at_local_between", "matched": false, "present": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
- AI runs:
  - intake (29d23d67-ef5e-452b-95a6-7d1b29d1b43e): status=failed; error_code=ai.invalid_model_output; steps: repair:-:ai.invalid_model_output [validation: json_invalid], repair:-:ai.invalid_model_output [validation: json_invalid]
  - safety (77092d21-cb6b-4ff5-91a3-222829a3a29d): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - (none)

## Failure: timezones-denver-spring-before

- proposal action types/count differ
- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "service", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "customer", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "starts_at_local_between", "matched": false, "present": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": ["fell_back"]}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": ["ungrounded"]}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": true}
  - {"actual_proposal": 1, "key": "grounding.verdict", "asserted": false, "actual": "unsupported"}
- AI runs:
  - safety (1470cd93-1fd4-43a9-b877-baa48f7104a3): status=succeeded; error_code=-; steps: completion:-:-
  - grounding (7d9b7b7c-31c8-4b73-802c-88d704e4c428): status=succeeded; error_code=-; steps: completion:-:-
  - intake (cca03b7f-77fa-4c44-bf2d-80bc8d351840): status=succeeded; error_code=-; steps: tool_call:find_slots:-, tool_call:propose_reply:-, final:-:-
- Proposals:
  - conversations.send_reply: Reply to Ada (web chat); run_id=cca03b7f-77fa-4c44-bf2d-80bc8d351840

## Failure: timezones-london-afternoon

- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": 1, "key": "service", "matched": true, "present": true}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": 1, "key": "customer", "matched": false, "present": true}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": 1, "key": "starts_at_local_between", "matched": true, "present": true}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
- AI runs:
  - safety (7a2bdf85-dfeb-4c68-92ad-87b052e0846f): status=succeeded; error_code=-; steps: completion:-:-
  - intake (fb4ca9f9-8cf5-4f50-9916-cd4efd22647b): status=succeeded; error_code=-; steps: tool_call:find_slots:-, tool_call:propose_booking:-, final:-:-
- Proposals:
  - scheduling.book_appointment: Book Garden design consultation for Gwen (new customer), Wed 11 Nov 13:00 GMT; run_id=fb4ca9f9-8cf5-4f50-9916-cd4efd22647b

## Failure: timezones-london-dst-after

- primary AI run status failed (schema validation not attempted)
- status: expected succeeded, got failed
- proposal action types/count differ
- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "service", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "customer", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "starts_at_local_between", "matched": false, "present": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
- AI runs:
  - intake (79d29e5a-3931-4fec-bf9b-b23e631ea510): status=failed; error_code=ai.invalid_model_output; steps: tool_call:propose_booking:ai.slot_required, repair:-:ai.invalid_model_output [validation: json_invalid], repair:-:ai.invalid_model_output [validation: json_invalid]
  - safety (805bb5a2-d2e2-459a-a37d-6daf2df27ca5): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - (none)

## Failure: timezones-london-dst-before

- primary AI run status failed (schema validation not attempted)
- status: expected succeeded, got failed
- proposal action types/count differ
- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "service", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "customer", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "starts_at_local_between", "matched": false, "present": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
- AI runs:
  - intake (02419311-41b3-4587-88ba-2e81668ad190): status=failed; error_code=ai.invalid_model_output; steps: repair:-:ai.invalid_model_output [validation: json_invalid], repair:-:ai.invalid_model_output [validation: json_invalid]
  - safety (a3f2724d-3b4c-4a77-a819-3041a15e52b2): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - (none)

## Failure: timezones-london-next-wednesday

- needs_human: expected False, got True
- proposal action types/count differ
- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "service", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "customer", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "starts_at_local_between", "matched": false, "present": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": ["fell_back"]}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": ["grounding_unavailable", "bundled_action"]}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": true}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"actual_proposal": 1, "key": "grounding.verdict", "asserted": false, "actual": "unavailable"}
- AI runs:
  - safety (3cb25535-5b7e-4ada-9c3a-08fcc7255c99): status=succeeded; error_code=-; steps: completion:-:-
  - intake (b18522f6-7da0-4074-8e19-5f55d1171d4b): status=succeeded; error_code=-; steps: tool_call:find_slots:-, tool_call:propose_reply:-
- Proposals:
  - conversations.send_reply: Reply to Gwen (web chat); run_id=b18522f6-7da0-4074-8e19-5f55d1171d4b

## Failure: timezones-london-spring-before

- primary AI run status failed (schema validation not attempted)
- status: expected succeeded, got failed
- proposal action types/count differ
- proposal key fields or structured output differ
- Expectation checks (no generated reply text):
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "service", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "customer", "matched": false, "present": false}
  - {"alternative": 1, "proposal": 1, "action_type": "scheduling.book_appointment", "actual_proposal": null, "key": "starts_at_local_between", "matched": false, "present": false}
  - {"key": "autonomy_outcomes", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "autonomy_reasons", "asserted": false, "present": true, "matched": null, "expected": null, "actual": []}
  - {"key": "handoff_requested", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
  - {"key": "suggestion_created", "asserted": false, "present": true, "matched": null, "expected": null, "actual": false}
- AI runs:
  - intake (0a27f85c-7fda-4a6c-9a21-607f4a4dc9dc): status=failed; error_code=ai.invalid_model_output; steps: repair:-:ai.invalid_model_output [validation: json_invalid], repair:-:ai.invalid_model_output [validation: json_invalid]
  - safety (172388f5-f45a-4137-a0d4-f6c3bdc992b1): status=succeeded; error_code=-; steps: completion:-:-
- Proposals:
  - (none)
