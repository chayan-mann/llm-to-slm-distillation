"""Run the teacher prompt on the manual eval set and compare against the hand-checked answers.

Usage:
    Fill in OPENAI_API_KEY and TEACHER_MODEL in .env at the project root, then:
    python scripts/run_teacher.py [--limit N] [--workers 8]

Writes every raw output to runs/teacher_<timestamp>/results.jsonl and prints a per-field report.
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from ticket_router.spec import OUTPUT_KEYS, format_input, parse_output
from ticket_router.teacher import call_teacher, load_prompt, make_client

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # variables already set in the shell take precedence
EVAL_PATH = ROOT / "data" / "eval" / "manual_eval.jsonl"


def run_one(client, model, system_prompt, example):
    ticket_text = format_input(example["body"], example.get("subject"))
    start = time.monotonic()
    try:
        raw = call_teacher(client, model, system_prompt, ticket_text)
        error = None
    except Exception as e:  # keep going; one failed call shouldn't sink the run
        raw, error = None, f"{type(e).__name__}: {e}"
    latency = time.monotonic() - start

    pred, problems = (None, [error]) if raw is None else parse_output(raw, ticket_text)
    return {
        "id": example["id"],
        "input": ticket_text,
        "raw": raw,
        "pred": pred,
        "problems": problems,
        "gold": example["gold"],
        "notes": example.get("notes", ""),
        "latency_s": round(latency, 2),
    }


def report(results):
    n = len(results)
    valid = [r for r in results if not r["problems"]]
    print(f"\n{n} tickets, {len(valid)} valid outputs ({len(valid) / n:.0%})")

    print("\nField accuracy (invalid outputs count as wrong):")
    for key in OUTPUT_KEYS:
        correct = sum(1 for r in valid if r["pred"][key] == r["gold"][key])
        print(f"  {key:<20} {correct}/{n}  {correct / n:.0%}")
    exact = sum(1 for r in valid if all(r["pred"][k] == r["gold"][k] for k in OUTPUT_KEYS))
    print(f"  {'all 5 correct':<20} {exact}/{n}  {exact / n:.0%}")

    confusions = Counter(
        (r["gold"]["category"], r["pred"]["category"])
        for r in valid
        if r["pred"]["category"] != r["gold"]["category"]
    )
    if confusions:
        print("\nCategory confusions (expected → got):")
        for (gold, pred), count in confusions.most_common():
            print(f"  {gold} → {pred}  ×{count}")

    print("\nMismatches:")
    any_mismatch = False
    for r in results:
        if r["problems"]:
            any_mismatch = True
            print(f"\n[{r['id']}] INVALID: {'; '.join(r['problems'])}")
            print(f"  raw: {r['raw']}")
            continue
        wrong = [k for k in OUTPUT_KEYS if r["pred"][k] != r["gold"][k]]
        if wrong:
            any_mismatch = True
            print(f"\n[{r['id']}] {r['input'][:100]!r}")
            for k in wrong:
                print(f"  {k}: expected {r['gold'][k]!r}, got {r['pred'][k]!r}")
            if r["notes"]:
                print(f"  note: {r['notes']}")
    if not any_mismatch:
        print("  none")

    latencies = sorted(r["latency_s"] for r in results)
    print(f"\nLatency: median {latencies[n // 2]}s, max {latencies[-1]}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=os.environ.get("TEACHER_MODEL"),
                        help="teacher model id (default: TEACHER_MODEL from .env)")
    parser.add_argument("--limit", type=int, help="only run the first N tickets")
    parser.add_argument("--workers", type=int, default=8, help="parallel requests")
    args = parser.parse_args()
    if not args.model:
        sys.exit("Set TEACHER_MODEL in .env or pass --model with the exact model id.")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in .env.")

    system_prompt, _ = load_prompt()
    with EVAL_PATH.open() as f:
        examples = [json.loads(line) for line in f if line.strip()]
    if args.limit:
        examples = examples[: args.limit]

    client = make_client()
    print(f"Running {len(examples)} tickets on {args.model}...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda ex: run_one(client, args.model, system_prompt, ex), examples))

    out_dir = ROOT / "runs" / f"teacher_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True)
    with (out_dir / "results.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps({"model": args.model, **r}, ensure_ascii=False) + "\n")

    report(results)
    print(f"\nSaved to {out_dir.relative_to(ROOT)}/results.jsonl")


if __name__ == "__main__":
    main()
