# retrieval · vultr · qwen3-embedding-4b

Synthetic cases; wall-clock end-to-end latency; USD costs.

Schema validity requires a primary (non-safety) run with status succeeded. For agent suites (intake/injection/timezones/copilot), if its recorded step count is nonzero, the persisted result is validated as FinalStep after replacing text with message_to_staff and adding kind=final; a succeeded zero-step run has no output-schema check. For single-shot suites, summary validates ThreadSummary and procurement validates SupplierQuoteExtract (before merging the extraction response). Missing or unsuccessful runs fail this metric without attempting schema validation. Exceptions escaping execution and skipped cases also score false.

| Metric | Value |
|---|---:|
| cases | 8 |
| skipped_cases | 0 |
| unpriced_cases | 8 |
| pass_rate | 1.0 |
| schema_valid_rate | 1.0 |
| action_accuracy | 1.0 |
| field_accuracy | 1.0 |
| injection_resistance | 1.0 |
| needs_human_precision | None |
| needs_human_accuracy | None |
| p50_latency_ms | 965.0 |
| p95_latency_ms | 1243 |
| input_tokens | 146 |
| output_tokens | 0 |
| cost_micros | None |
| thresholds_met | True |
| citation_accuracy | 1.0 |

Regression gate: not compared

Matched proposal alternative is 1-based; - means no alternative matched or none were configured.

| Case | Result | Proposal alternative | Steps | ms | Input tokens | Output tokens | Cost USD |
|---|---|---:|---:|---:|---:|---:|---:|
| retrieval-accessibility | PASS | - | 0 | 1243 | 16 | 0 | unpriced |
| retrieval-compost | PASS | - | 0 | 965 | 19 | 0 | unpriced |
| retrieval-parking | PASS | - | 0 | 905 | 18 | 0 | unpriced |
| retrieval-pets | PASS | - | 0 | 952 | 17 | 0 | unpriced |
| retrieval-refund | PASS | - | 0 | 1016 | 19 | 0 | unpriced |
| retrieval-storage | PASS | - | 0 | 1010 | 18 | 0 | unpriced |
| retrieval-toilet | PASS | - | 0 | 859 | 20 | 0 | unpriced |
| retrieval-warranty | PASS | - | 0 | 965 | 19 | 0 | unpriced |
