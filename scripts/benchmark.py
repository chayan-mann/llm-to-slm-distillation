"""Measure student speed, memory and accuracy on the test set, next to the teacher's API latency.

Usage:
    export HF_HOME=~/models/huggingface HF_HUB_OFFLINE=1
    python scripts/benchmark.py

Student variants (each run on the same test tickets, after a short warm-up):
  - 4-bit base + LoRA adapter (checkpoint 350)
  - fused, full precision (bf16)
  - fused, 8-bit
Teacher numbers come from the per-call latencies label.py recorded in data/labeled/labels.jsonl,
so no API calls are made. Results go to runs/benchmark_<timestamp>.json.
"""

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import mlx.core as mx
from mlx_lm import load, stream_generate

from ticket_router.metrics import score

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = {
    "4-bit base + adapter": {"model": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
                             "adapter": ROOT / "adapters" / "qwen2.5-1.5b-4bit-r8-step350"},
    "fused bf16": {"model": str(ROOT / "models" / "ticket-router-qwen2.5-1.5b-bf16")},
    "fused 8-bit": {"model": str(ROOT / "models" / "ticket-router-qwen2.5-1.5b-8bit")},
}
# Teacher cost per request, from the API's own token counts during labeling (~3.9k prompt, ~40 output).
TEACHER_TOKENS_IN, TEACHER_TOKENS_OUT = 3900, 40
TEACHER_PRICE_IN, TEACHER_PRICE_OUT = 0.10, 0.50  # $ per 1M tokens (gpt-6-luna)
WARMUP = 3


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


def bench_student(name, spec, examples):
    mx.reset_peak_memory()
    start = time.monotonic()
    model, tokenizer = load(spec["model"], adapter_path=str(spec["adapter"]) if "adapter" in spec else None)
    load_s = time.monotonic() - start

    prompts = [tokenizer.apply_chat_template([{"role": "user", "content": text}], tokenize=False,
                                             add_generation_prompt=True) for text, _ in examples]
    for p in prompts[:WARMUP]:
        for _ in stream_generate(model, tokenizer, p, max_tokens=64):
            pass

    outputs, totals, ttfts, gen_tps = [], [], [], []
    for p in prompts:
        start, first, text, last = time.monotonic(), None, "", None
        for response in stream_generate(model, tokenizer, p, max_tokens=64):
            if first is None:
                first = time.monotonic() - start
            text += response.text
            last = response
        totals.append(time.monotonic() - start)
        ttfts.append(first)
        gen_tps.append(last.generation_tps)
        outputs.append(text)

    result, _ = score(outputs, [ref for _, ref in examples], [text for text, _ in examples])
    return {
        "variant": name,
        "load_s": round(load_s, 2),
        "peak_memory_gb": round(mx.get_peak_memory() / 1e9, 2),
        "latency_median_s": round(statistics.median(totals), 3),
        "latency_p95_s": round(pct(totals, 0.95), 3),
        "ttft_median_s": round(statistics.median(ttfts), 3),
        "gen_tokens_per_s_median": round(statistics.median(gen_tps), 1),
        "tickets_per_minute": round(60 / statistics.mean(totals), 1),
        "valid_json": result["valid_json"],
        "category_acc": result["category_acc"],
        "all_fields_exact": result["all_fields_exact"],
    }


def teacher_stats():
    rows = {}
    for line in (ROOT / "data" / "labeled" / "labels.jsonl").open():
        r = json.loads(line)
        rows[r["id"]] = r
    lat = [r["latency_s"] for r in rows.values() if r["label"] is not None]
    cost = (TEACHER_TOKENS_IN * TEACHER_PRICE_IN + TEACHER_TOKENS_OUT * TEACHER_PRICE_OUT) / 1e6
    return {"variant": f"teacher ({next(iter(rows.values()))['model']}, API)", "calls": len(lat),
            "latency_median_s": round(statistics.median(lat), 3), "latency_p95_s": round(pct(lat, 0.95), 3),
            "cost_per_1k_tickets_usd": round(cost * 1000, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="only the first N test tickets")
    args = parser.parse_args()

    rows = [json.loads(line) for line in (ROOT / "data" / "mlx" / "test.jsonl").open()][: args.limit]
    examples = [(r["messages"][0]["content"], json.loads(r["messages"][1]["content"])) for r in rows]

    results = [bench_student(name, spec, examples) for name, spec in VARIANTS.items()]
    teacher = teacher_stats()

    print(f"\nStudent on {len(examples)} test tickets (after {WARMUP} warm-up), greedy, one at a time:")
    print(f"{'variant':<22} {'load':>6} {'peak mem':>9} {'median':>8} {'p95':>7} {'1st token':>10} {'gen tok/s':>10} "
          f"{'tickets/min':>12} {'valid':>6} {'category':>9} {'all exact':>10}")
    for r in results:
        print(f"{r['variant']:<22} {r['load_s']:>5.1f}s {r['peak_memory_gb']:>7.2f}GB {r['latency_median_s']:>7.3f}s "
              f"{r['latency_p95_s']:>6.3f}s {r['ttft_median_s']:>9.3f}s {r['gen_tokens_per_s_median']:>10.1f} "
              f"{r['tickets_per_minute']:>12.1f} {r['valid_json']:>6.0%} {r['category_acc']:>9.1%} {r['all_fields_exact']:>10.1%}")
    print(f"\nTeacher ({teacher['calls']} labeling calls): median {teacher['latency_median_s']}s, "
          f"p95 {teacher['latency_p95_s']}s, ~${teacher['cost_per_1k_tickets_usd']} per 1,000 tickets")

    out = ROOT / "runs" / f"benchmark_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"students": results, "teacher": teacher}, indent=2))
    print(f"Saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
