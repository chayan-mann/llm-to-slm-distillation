# ticket-router

A standalone, runnable support-ticket router: one Python file plus a 35 MB LoRA adapter on top of `mlx-community/Qwen2.5-1.5B-Instruct-4bit`. It gives a routing decision in ~0.4 s per ticket, locally, with no API key. **Requires an Apple Silicon Mac** (MLX).

This folder only depends on `mlx-lm`. Copy it anywhere and run it.

## Setup

```bash
pip install -r requirements.txt
```

The base model (~870 MB) downloads from Hugging Face on first run and is cached. To reuse a copy you already have, point `HF_HOME` at its cache (e.g. `export HF_HOME=~/models/huggingface`), and add `HF_HUB_OFFLINE=1` to never touch the network.

## Usage

```bash
python route.py "I was charged twice this month, invoice INV-4412"
python route.py --subject "Locked out" "reset email never arrives, login is sam@acme.io"
echo "cant login" | python route.py -
python route.py --file tickets.txt              # one ticket per line → one JSON line each
python route.py --serve --port 8080             # local HTTP API
```

```bash
curl -s -X POST localhost:8080/route -d '{"text": "Someone changed my password and it wasnt me", "subject": "optional"}'
curl -s localhost:8080/health
```

The server handles one request at a time: MLX runs the model on the thread that loaded it, and there is one GPU.

## Output

```json
{"category": "unexpected_charge", "multi_intent": false, "urgency": "normal",
 "account_identifier": null, "reference_id": "INV-4412",
 "needs_review": false, "review_reason": null}
```

| Field | Meaning |
|---|---|
| `category` | One of 30 routing categories (billing, login, outage, security, …); see [`docs/spec.md`](../docs/spec.md) |
| `multi_intent` | The ticket asks for things that belong to more than one category |
| `urgency` | `low` / `normal` / `high` / `critical` |
| `account_identifier` | Account ID, workspace URL or login email, copied from the ticket, or `null` |
| `reference_id` | Invoice number, error code, request ID, etc., copied from the ticket, or `null` |
| `needs_review` | **Safety field:** `true` means a human should look at this ticket whatever the routing says |
| `review_reason` | Why it was flagged |

**When `needs_review` is `true`:**
- **Security words the model didn't treat as security:** the ticket mentions things like "hacked", "don't recognize this device" or "vulnerability", but the model didn't route it to `security_report` / `compromised_account` as `critical`. This covers the model's main known weakness (a security problem mentioned second can be routed as a normal request). On unseen tickets, the model plus this check caught 23/23 security tickets, flagging ~4% of all tickets for review.
- **Invalid output:** the model's answer failed validation (unknown category, missing fields, an ID that isn't in the ticket). The five routing fields are then `null` and `raw_output` holds what the model produced.

## Accuracy and limits

On 148 held-out test tickets: 100% valid output, 88.5% category agreement with the teacher model, 88.5% urgency; details in [`docs/eval_report.md`](../docs/eval_report.md). It was trained on 1,500 synthetic English tickets for an imaginary SaaS product, so for a real product, retrain with the pipeline in the repo root. The adapter only works with the exact base model named in `adapter/adapter_config.json`.
