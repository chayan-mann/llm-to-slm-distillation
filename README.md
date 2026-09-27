# llm-to-slm-distillation

**Project:** Fine-tune a tiny model (0.5B–1.5B) to replace a big-model + long-prompt classification/extraction pipeline via distillation.

**Example use case:** Support ticket intent router — classify into 1 of 30 categories + extract 3 structured fields, in <100ms, no system prompt needed at inference.

A big "teacher" model (`gpt-6-luna`) with a long rulebook prompt labels support tickets; a small "student" model (Qwen2.5-1.5B-Instruct, LoRA via `mlx-lm`) learns to produce the same labels from the ticket text alone.

## What the model does

It reads **one customer support message** for a SaaS product (web app, mobile apps, public API) and fills in a **routing slip**. It does not reply to the customer.

| Field | Answers | Example |
|---|---|---|
| `category` | Which team handles it? One of 30 | `unexpected_charge` |
| `multi_intent` | More than one kind of request? | `false` |
| `urgency` | How fast to respond? | `low` / `normal` / `high` / `critical` |
| `account_identifier` | Which customer account? | `acct_83jd92`, `acme.ourapp.com`, login email |
| `reference_id` | Anything specific to look up? | `INV-4412`, `ERR_SYNC_409`, request ID |

The 30 categories cover account & access (login, 2FA, SSO, users…), billing (charges, refunds, plans, invoices, trials…), technical (bugs, outages, integrations, API, mobile…), product (how-to, feature requests, feedback), security & compliance (vulnerabilities, hacked accounts, GDPR), sales, and out-of-scope messages. Full definitions are in [`docs/spec.md`](docs/spec.md).

```
$ python ticket-router/route.py "I was charged twice this month, invoice INV-4412. Please fix."
{"category": "unexpected_charge", "multi_intent": false, "urgency": "normal", "account_identifier": null, "reference_id": "INV-4412", "needs_review": false, "review_reason": null}
```

**Training data:** 1,500 synthetic English tickets written for an imaginary product (varied tone, length, typos, quoted threads, ~18% multi-request, deliberate edge cases), all labeled by the teacher. Names, emails and IDs are made up.

**Limits:** it only knows these 30 categories and this imaginary product. Real use means rerunning the pipeline on real tickets. It's English only, and it runs on Apple Silicon (MLX). A security issue mentioned second in a ticket can be under-prioritized (see the [eval report](docs/eval_report.md)), so pair it with a keyword check in production.

**Use it:** [`ticket-router/`](ticket-router/) is a standalone folder (one `route.py` + the 35 MB adapter) that runs the router from the command line, over a file, or as a local HTTP API, with output validation and a security safety net (`needs_review`) built in:

```bash
pip install -r ticket-router/requirements.txt
python ticket-router/route.py "I was charged twice this month, invoice INV-4412"
python ticket-router/route.py --serve --port 8080     # POST /route {"text": "..."}
```

### What the adapter is

The router is two pieces used together:

```
base model  mlx-community/Qwen2.5-1.5B-Instruct-4bit   870 MB   general chat model, knows nothing about ticket routing
+ adapter   ticket-router/adapter/                      35 MB   the part trained in this project
= ticket router
```

Training used **LoRA**: the base model's 1.5B weights stay frozen, and small extra matrices added alongside its layers are trained instead. That's 9.2M numbers, about 0.6% of the model, learned from the 1,204 training tickets. They hold the whole task: the output format, the 30 categories, urgency and ID extraction. The folder has `adapters.safetensors` (the trained weights, checkpoint 350) and `adapter_config.json` (which base model it belongs to and the training settings).

Why an adapter: training took ~20 minutes on a laptop, it's 35 MB to store or share, and one base model can carry several adapters for different tasks. It can also be fused into the base as one standalone model (`mlx_lm fuse --dequantize`, 2.9 GB). An adapter only works with the exact base model it was trained on.

**Both run on every request.** The adapter isn't a model on its own: it has no vocabulary and no language understanding. For every token, the input goes through all 28 base layers, and at each adapted layer the adapter adds a small correction:

```
output = base_weights × input   +   adapter_weights × input
         (1.5B, frozen: reads       (9.2M, trained: steers it to our
          and writes language)       JSON, categories and rules)
```

About 99% of the compute and ~1 GB of memory is the base model. The adapter adds ~0.6% and 35 MB. Base alone gives chatbot replies (0/10 valid in the baseline), the adapter alone can't run, and together they're the router. Fusing just does the addition once ahead of time, so the math at runtime is the same.

## Status

| Phase | State | Output |
|---|---|---|
| 0. Spec | ✅ v1.3 | [`docs/spec.md`](docs/spec.md) |
| 1. Teacher prompt | ✅ 47/47 on the hand-checked eval set | [`prompts/teacher_system.md`](prompts/teacher_system.md), [`data/eval/manual_eval.jsonl`](data/eval/manual_eval.jsonl) |
| 2. Raw tickets | ✅ 1,500 synthetic tickets, ~50 per category | [`data/raw/`](data/raw/) |
| 3. Distillation labeling | ✅ all 1,500 labeled blind by the teacher | [`data/labeled/labels.jsonl`](data/labeled/labels.jsonl) |
| 4. Quality pass | ✅ 2 rounds of spec fixes, ~96% of a 100-label review fully correct, 80/10/10 split | [`data/splits/`](data/splits/) |
| 5. Formatting | ✅ MLX chat JSONL, every target validated | [`data/mlx/`](data/mlx/) |
| 6. Fine-tuning | ✅ QLoRA on Qwen2.5-1.5B-Instruct-4bit, ~20 min, checkpoint 350 | [`configs/lora.yaml`](configs/lora.yaml) |
| 7. Evaluation | ✅ test: 100% valid JSON, 88.5% category, 72.3% all-fields-exact vs teacher | [`docs/eval_report.md`](docs/eval_report.md) |
| 8. Latency | ✅ 0.28–0.38 s per ticket locally vs 1.96 s for the teacher API (5–7× faster), no per-ticket cost | [`docs/eval_report.md`](docs/eval_report.md#latency-and-efficiency) |
| 9. Iterate | ⏳ optional: targeted data for the weak rules in the eval report | |
| 10. Packaging | ✅ standalone runnable router with validation + security safety net | [`ticket-router/`](ticket-router/) |

## Repository layout

```
ticket-router/               Standalone runnable router: route.py + the trained adapter
docs/spec.md                 Source of truth: categories, fields, edge cases, changelog
docs/eval_report.md          Phase 7 results and error analysis
configs/lora.yaml            LoRA training settings for mlx-lm
prompts/teacher_system.md    The teacher's system prompt (generated from the spec by hand)
src/ticket_router/
  spec.py                    Categories, JSON schema, input formatting, output validation
  teacher.py                 The one place the teacher API is called
  metrics.py                 Scoring (spec §9): validity, per-field accuracy, macro-F1, ID fields split by null
scripts/
  run_teacher.py             Phase 1: score the teacher on the hand-checked eval set
  check_raw.py               Phase 2: balance / near-duplicate checks, merges raw batches
  label.py                   Phase 3: blind labeling (resumable, rate-limited, refuses to mix prompts)
  review.py                  Phase 4: builds a local HTML page for human review of a label sample
  split.py                   Phase 4: freezes labels, 80/10/10 split stratified by category
  format_mlx.py              Phase 5: MLX chat-format JSONL + validation of every target
  eval_student.py            Phase 6-7: score a LoRA checkpoint or fused model on valid / test / hand-checked tickets
  benchmark.py               Phase 8: speed, memory and accuracy of student variants vs the teacher's API latency
data/
  eval/manual_eval.jsonl     47 hand-checked tickets with gold answers (never used for training)
  raw/                       Unlabeled tickets (tickets.jsonl) and what each was written to test (recipes.jsonl)
  labeled/labels.jsonl       Teacher labels, one per ticket; archive/ holds labels from earlier prompt versions
  splits/                    Frozen train/valid/test with labels
  mlx/                       Fine-tuning-ready train/valid/test
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # then fill in OPENAI_API_KEY
```

## Running the pipeline

```bash
python scripts/run_teacher.py        # teacher accuracy on data/eval (a few cents)
python scripts/check_raw.py --merge  # validate and merge data/raw/batches/
python scripts/label.py              # label all raw tickets (~$1, ~40 min at 40 req/min)
python scripts/review.py make        # data/review/review.html for manual review
python scripts/split.py              # data/splits/
python scripts/format_mlx.py         # data/mlx/

pip install -e ".[train]"             # mlx-lm (Apple Silicon)
export HF_HOME=~/models/huggingface HF_HUB_OFFLINE=1   # where the base model is cached
python -m mlx_lm lora -c configs/lora.yaml                                   # ~20 min on an M-series Mac
python scripts/eval_student.py --adapter-dir adapters/qwen2.5-1.5b-4bit-r8 --split valid --all-checkpoints
python scripts/eval_student.py --adapter-dir adapters/qwen2.5-1.5b-4bit-r8 --checkpoint 0000350 --split test
python -m mlx_lm fuse --model mlx-community/Qwen2.5-1.5B-Instruct-4bit --adapter-path <adapter dir> --save-path models/<name> --dequantize
python scripts/benchmark.py
```

> Fuse with `--dequantize`: fusing a LoRA adapter back into a 4-bit model destroys most of the fine-tuning (see the eval report).

`label.py --dry-run --out /tmp/x.jsonl` exercises the whole labeling path with a fake teacher and no API calls.

## Notes

- **All tickets are synthetic.** Names, emails, account IDs and companies are made up.
- **Label noise.** Estimated ~96% of labels fully correct. The weakest spot is the `normal`/`low` urgency boundary, where the teacher is inconsistent between runs.
- **Evaluation.** Phase 7 compares the student to the teacher on `data/mlx/test.jsonl`, and to hand-checked gold answers on `data/eval/manual_eval.jsonl`.

---

# Project plan

## Phase 0 — Spec & Design

**Goal:** Nail down exactly what the model needs to do before touching data or code.

- Define the 30 categories (names + precise definitions/boundaries — ambiguous category definitions poison everything downstream)
- Define the 3 structured fields to extract (type, format, required vs optional, how to handle "not present")
- Write the exact output JSON schema
- Decide edge-case policy (empty ticket, multi-intent ticket, field not mentioned → null vs omit, etc.)

**Deliverable:** A written spec doc (schema + category definitions) — this is the source of truth for everything else.

---

## Phase 1 — Teacher Prompt Engineering

**Goal:** Build the "big model + long prompt" baseline you're trying to compress.

- Write the full system prompt (category defs + few-shot examples + extraction instructions)
- Test it manually on a handful of tickets, iterate until outputs look reliable

**Deliverable:** A working teacher prompt + a small manual eval set (~20-30 hand-checked examples) to sanity check it.

---

## Phase 2 — Raw Data Collection

**Goal:** Get a pool of realistic, diverse ticket text (unlabeled).

- Source real tickets (public dataset) and/or generate synthetic ones with a big model
- Push for diversity: tone, length, formality, ambiguity, multi-topic tickets, typos

**Deliverable:** 1500–3000 raw ticket texts, no labels yet.

---

## Phase 3 — Distillation Labeling

**Goal:** Turn raw tickets into (input, label) pairs using the teacher.

- Run every raw ticket through the teacher prompt **blind** — no hints, genuine inference
- Store structured output per ticket

**Deliverable:** A labeled dataset (ticket → category + 3 fields), unfiltered.

> Note: distillation is defined by *who labels*, not *who generates the input text*. Labels must come from the teacher's actual judgment, not be dictated by you.

---

## Phase 4 — Data Quality Pass

**Goal:** Don't train on garbage labels.

- Manually review a random sample (100–200) for correctness
- Check category balance — some of the 30 categories will be underrepresented, note this
- Check field-extraction accuracy specifically (often weaker than classification)
- Fix systematic teacher errors (usually a prompt problem) and re-run if needed
- Decide train/val/test split (e.g. 80/10/10), stratified by category

**Deliverable:** A cleaned, split dataset you trust.

---

## Phase 5 — Data Formatting

**Goal:** Get data into fine-tuning-ready format.

- Convert to MLX chat-format JSONL (input ticket → target JSON string)
- Validate JSON parseability of every target

**Deliverable:** `train.jsonl`, `valid.jsonl`, `test.jsonl`

---

## Phase 6 — Fine-Tuning

**Goal:** Train the student model.

- Set up `mlx-lm`, pick base model (Qwen2.5-1.5B-Instruct to start)
- LoRA fine-tune, watch loss curves
- Save adapter checkpoints

**Deliverable:** A fine-tuned LoRA adapter (+ merged model if desired).

---

## Phase 7 — Evaluation

**Goal:** Prove it actually worked, quantitatively.

- Run fine-tuned model on held-out test set, no system prompt
- Metrics: category accuracy (exact match), per-field extraction accuracy/F1, JSON validity rate
- Compare against teacher's performance on the same set (student won't beat teacher, but how close?)
- Error analysis: which categories/fields fail most — data problem or model-capacity problem?

**Deliverable:** An eval report with numbers, not vibes.

---

## Phase 8 — Latency/Efficiency Check

**Goal:** Confirm the actual point of this exercise (speed/cost win).

- Benchmark inference latency locally (MLX) — no-prompt small model vs. big-model-with-long-prompt baseline
- Measure tokens/sec, time-to-first-token, memory footprint

**Deliverable:** Before/after latency comparison.

---

## Phase 9 — Iterate

**Goal:** Close the loop based on Phase 7 findings.

- If specific categories/fields underperform → add more targeted data for those, retrain
- Try hyperparameter variations (LoRA rank, epochs, learning rate) if capacity-limited
- Optionally try a smaller base (0.5B) once 1.5B works, to see how far down you can push size

---

## Optional Phase 10 — Packaging

*If you ever want to share/deploy it:*

- Export merged model, write a simple inference wrapper/API
- Document the pipeline (this becomes the repo's README)

---

## Stack Summary

| Component | Choice |
|---|---|
| Base model | Qwen2.5-1.5B-Instruct (start), try 0.5B later |
| Fine-tuning framework | `mlx-lm` (Apple Silicon-native) |
| Method | LoRA |
| Hardware | M5 Pro, 24GB unified RAM |
| Teacher model | `gpt-6-luna` via the OpenAI API (long system prompt, strict JSON schema) |
| Data format | MLX chat-format JSONL |