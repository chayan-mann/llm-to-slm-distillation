# Student Evaluation Report

**Student:** Qwen2.5-1.5B-Instruct (4-bit, `mlx-community/Qwen2.5-1.5B-Instruct-4bit`) + LoRA rank 8 on all 28 layers, checkpoint `0000350` of [`configs/lora.yaml`](../configs/lora.yaml). No task prompt at inference (only Qwen's built-in 21-token default system line).
**Teacher:** `gpt-6-luna` with [`prompts/teacher_system.md`](../prompts/teacher_system.md) (spec v1.3, prompt `1d5b6207281d`).
**Scored with:** [`scripts/eval_student.py`](../scripts/eval_student.py) and [`src/ticket_router/metrics.py`](../src/ticket_router/metrics.py). Invalid outputs count as wrong on every field. Greedy decoding.

## Headline numbers

| Metric | Base model, no training | Student vs teacher, **test** (n=148) | Student vs **hand-checked gold** (n=47) | Teacher vs hand-checked gold (n=47) |
|---|---|---|---|---|
| Valid JSON (schema, key order, verbatim IDs) | 0/10 | **100%** | 100% | 100% |
| All 5 fields exact | 0% | **72.3%** | 76.6% | 100% |
| Category accuracy | 0% | **88.5%** (macro-F1 88.2%) | 91.5% | 100% |
| multi_intent accuracy | — | 91.2% (precision 81.8%, recall 66.7%) | 95.7% | 100% |
| Urgency accuracy | — | 88.5% (off by 2+ levels: 1.4%) | 89.4% | 100% |
| account_identifier | — | 100% | 100% | 100% |
| reference_id | — | 99.3% (90% when present) | 95.7% (75% when present) | 100% |
| Median latency per ticket | 0.3 s* | 0.37 s | 0.41 s | ~1.7 s** |

\* Base model latency is for rambling free text, cut at 60 tokens. \*\* Teacher latency is an API round trip with a ~3.8k-token prompt; Phase 8 does a proper comparison.

The hand-checked set was used to develop the teacher prompt (so the teacher's 100% is optimistic), but the student never trained on it.

## Checkpoint selection (validation set, n=148)

| Step | 50 | 100 | 150 | 200 | 250 | 300 | **350** | 400 | 450 |
|---|---|---|---|---|---|---|---|---|---|
| Valid JSON | 94.6% | 98.6% | 99.3% | 100% | 100% | 100% | **100%** | 100% | 100% |
| All 5 exact | 38.5% | 52.7% | 66.2% | 63.5% | 72.3% | 66.9% | **71.6%** | 66.9% | 70.9% |
| Category | 64.9% | 79.7% | 87.2% | 83.8% | 90.5% | 87.2% | **90.5%** | 89.9% | 89.9% |

Learning plateaus around step 250. Differences after that (about ±3 points, where 1 ticket = 0.7 points) are noise. Validation loss was flat at ~0.035 from step 350 to 450, with no overfitting. Step 350 was chosen for being tied-best on category and from the stable end of training.

## Error analysis

### 1. Category errors on test (17 of 148): about 7 real mistakes, about 10 borderline

Reading every miss:

- **Real student mistakes (~7):** the student reacts to surface words where the teacher applies a rule.
  - "refunded … need a credit note" → `refund_request`; teacher `invoice_request`. The word "refund" wins.
  - A crypto "partnership opportunity" pitch, and an order meant for another company → `sales_inquiry`; teacher `out_of_scope`. The student doesn't recognize messages that aren't for us.
  - "our webhook endpoint was down" → `service_outage`; teacher `api_support`. The word "down" wins, even though it's the customer's system.
  - A public-sector security questionnaire tender → `out_of_scope`; teacher `privacy_compliance`.
  - Wrong totals in a report → `data_sync_issue`; teacher `bug_report`.
  - Payment already fixed, now asking for an invoice → `payment_failure`; the current request is the invoice.
- **Borderline (~10):** cases where either label is defensible and the teacher itself is on a fuzzy boundary. Examples: bug vs performance ("page freezes"), bug vs data sync, feedback vs feature request, "cancel but keep the free plan" (plan change vs cancellation).

**So measured against a lenient judge, category accuracy is closer to ~93–95%.** The strict 88.5% includes teacher-side noise on borderline tickets.

### 2. Misses on the hand-checked set (11 of 47): the teacher gets all of them right

These are the cases the rulebook exists for. The student has seen too few examples of each rule to learn it.

| Rule | Ticket | Student error |
|---|---|---|
| Severity override (a hacked account beats everything, even if mentioned second) | m32 | `feature_request` / `low` instead of `compromised_account` / `critical`. **The one dangerous miss:** a possible account takeover routed as a low-priority feature request |
| Classify the newest message in a quoted thread | m34 | Labeled from the old quoted login problem: `login_issue` / `high` |
| "Refund" wording on a disputed charge | m36 | `refund_request` instead of `unexpected_charge` |
| Past outage + credit request → `refund_request` | m40 | `service_outage` |
| Product error codes are `reference_id` | m03, m20 | Missed `SAML_AUDIENCE_MISMATCH`, `E_IMPORT_ENCODING` (also `dispatch_failed` on test) |
| Deadline within ~24h → `high`; cosmetic → `low` | m13, m33, m05 | Off by one level |
| Same-category requests are not multi-intent | m09, m27 | Marked multi-intent |

### 3. Field by field

- **Format and IDs are solved:** 100% valid JSON; `account_identifier` 100% on test. The student never invents a reference ID (100% correct when the answer is null).
- **Error-code reference IDs are the weak spot:** invoice numbers and request IDs are extracted reliably, but code-style references (`SAML_AUDIENCE_MISMATCH`, `dispatch_failed`) are often missed. They're rare in training: only ~7% of tickets have any reference.
- **Urgency:** 88.5%, and nearly all misses are one level off. Two tickets on test are off by two levels, and one is the teacher's own questionable `critical` on bouncing emails.
- **multi_intent:** recall is only 66.7% on test, so the student misses a third of genuine two-request tickets.

## Conclusions

1. **Distillation works for format and for most routing.** A 1.5B model with no prompt produces spec-valid JSON 100% of the time and agrees with the teacher on about 89% of categories (about 93–95% leniently), in about 0.4 s on a laptop.
2. **The gap to the teacher is concentrated in rule-driven reasoning:** severity override, quoted threads, past incidents, "refund" wording, messages meant for someone else, error-code references. Each rule shows up in only a handful of training tickets.
3. **The dangerous failure mode is missing a security or compromise signal** when it isn't the first thing mentioned (m32). For a real router, this matters more than the average accuracy.

## Recommended next steps (Phase 9)

1. **Targeted data (most likely to help):** about 150–250 extra tickets focused on the weak rules above:
   - a security or compromise issue mentioned second or buried in the ticket
   - quoted threads
   - past incidents with a request for a credit
   - "refund" wording on disputed charges
   - pitches and wrong-company messages
   - product and third-party error codes

   Label them with the same teacher and prompt, then retrain. Check again on the same test set and hand-checked set.
2. **Capacity experiments:** the full-precision base (`Qwen2.5-1.5B-Instruct-bf16`) instead of 4-bit, and/or LoRA rank 16.
3. **A safety net for security tickets:** an alert on security keywords ("hacked", "unknown login", "vulnerability") that forces a human review whatever the model says. This is cheap insurance while the model is weaker on the severity override.

## Latency and efficiency

Measured with [`scripts/benchmark.py`](../scripts/benchmark.py) on the 148 test tickets on an M5 Pro (24 GB), one ticket at a time, greedy decoding, after 3 warm-up tickets. Teacher numbers are the per-call latencies of the 1,500 v1.3 labeling calls (including the network round trip).

| | Size on disk | Peak memory | Median latency | p95 latency | First token | Generation speed | Tickets / min | Category | All 5 exact |
|---|---|---|---|---|---|---|---|---|---|
| **4-bit base + LoRA adapter** | 870 MB + 35 MB | 1.5 GB | **0.38 s** | 0.48 s | 0.11 s | 106 tok/s | 152 | **88.5%** | **72.3%** |
| Fused, full precision (bf16) | 2.9 GB | 3.3 GB | 0.41 s | 0.50 s | 0.09 s | 88 tok/s | 140 | 88.5% | 72.3% |
| Fused, 8-bit | 1.5 GB | 2.0 GB | **0.28 s** | 0.34 s | 0.09 s | 154 tok/s | 208 | 86.5% | 69.6% |
| Teacher (`gpt-6-luna` API) | — | — | 1.96 s | 3.59 s | — | — | — | (reference) | (reference) |

**Cost:** the teacher uses ~3,900 prompt tokens + ~40 output tokens per ticket, which is **~$0.41 per 1,000 tickets** at $0.10 / $0.50 per 1M tokens. The student runs locally with no per-ticket cost and no data leaving the machine.

**Findings:**
- **The student is 5–7× faster than the teacher** (0.28–0.38 s vs 1.96 s median; 0.34–0.48 s vs 3.59 s at p95), with steadier latency, since there's no network and no queueing behind a rate limit.
- **Don't fuse LoRA into a 4-bit model.** Merging and re-quantizing to 4 bits wiped out most of the fine-tuning (59.5% valid JSON, 43.9% category), because the LoRA update is smaller than the 4-bit rounding step. Fusing into full precision (`--dequantize`) matches the adapter exactly. Re-quantizing that to 8 bits loses about 2 points of category accuracy.
- **Best overall: keep the 4-bit base and load the 35 MB adapter.** It's the most accurate, uses the least memory, and is the smallest on disk. The fused 8-bit model is the fastest if 2 points of accuracy are an acceptable trade.
- **The <100 ms goal from the project plan is not met yet.** The first token arrives in ~0.1 s, but generating the ~27-token JSON takes another ~0.2–0.3 s. Ways to close the gap (Phase 9 options): a shorter output encoding (e.g. category ids instead of names), the 0.5B base model, speculative decoding, or batching several tickets per call for throughput.
