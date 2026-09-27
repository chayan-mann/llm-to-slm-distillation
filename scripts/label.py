"""Phase 3: label every raw ticket with the teacher, blind.

Usage:
    python scripts/label.py [--limit N] [--workers 8]
    python scripts/label.py --dry-run --out /tmp/labels.jsonl   # fake teacher, no API calls

Reads data/raw/tickets.jsonl (id, subject, body only; the teacher never sees the recipes)
and appends one line per ticket to data/labeled/labels.jsonl as each call finishes.

Safe to stop and re-run: tickets that already have a valid label are skipped, and
tickets whose last attempt failed or broke a spec rule are retried. A run refuses to
continue if the prompt or model differs from the labels already in the file, so the
dataset never mixes teachers.
"""

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from ticket_router.spec import CATEGORIES, format_input, parse_output
from ticket_router.teacher import call_teacher, load_prompt, make_client

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # variables already set in the shell take precedence
TICKETS_PATH = ROOT / "data" / "raw" / "tickets.jsonl"
RECIPES_PATH = ROOT / "data" / "raw" / "recipes.jsonl"
DEFAULT_OUT = ROOT / "data" / "labeled" / "labels.jsonl"


def read_jsonl(path):
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def latest_by_id(rows):
    """The file is append-only; the last line for an id is its current state."""
    return {row["id"]: row for row in rows}


class RateLimiter:
    """Spaces out request starts so we stay under the account's tokens-per-minute limit.

    Every request costs about the same (the long system prompt dominates), so a
    requests-per-minute cap is a good proxy: 40 rpm x ~3.9k tokens is ~156k of a 200k TPM limit.
    """

    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute
        self.next_slot = time.monotonic()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            slot = max(self.next_slot, time.monotonic())
            self.next_slot = slot + self.interval
        time.sleep(max(0.0, slot - time.monotonic()))


def fake_teacher(client, model, system_prompt, ticket_text):
    """Stand-in for --dry-run: a valid output with no API call."""
    return json.dumps({"category": "how_to_question", "multi_intent": False, "urgency": "low",
                       "account_identifier": None, "reference_id": None})


def label_one(teacher, client, model, system_prompt, prompt_sha, ticket, limiter):
    ticket_text = format_input(ticket["body"], ticket.get("subject"))
    limiter.wait()
    start = time.monotonic()
    try:
        raw, error = teacher(client, model, system_prompt, ticket_text), None
    except Exception as e:  # record the failure and retry it on the next run
        raw, error = None, f"{type(e).__name__}: {e}"
    latency = time.monotonic() - start

    pred, problems = (None, [error]) if raw is None else parse_output(raw, ticket_text)
    return {
        "id": ticket["id"],
        "model": model,
        "prompt_sha": prompt_sha,
        "input": ticket_text,
        "raw": raw,
        "label": pred if not problems else None,
        "problems": problems,
        "latency_s": round(latency, 2),
        "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def summarize(out_path):
    current = latest_by_id(read_jsonl(out_path))
    labeled = [r for r in current.values() if r["label"] is not None]
    failed = [r for r in current.values() if r["label"] is None]
    total = len(read_jsonl(TICKETS_PATH))
    print(f"\n{len(labeled)}/{total} tickets labeled, {len(failed)} failed (re-run to retry)")
    if not labeled:
        return

    by_cat = Counter(r["label"]["category"] for r in labeled)
    print("\nTeacher's category counts:")
    for c in CATEGORIES:
        print(f"  {c:<20} {by_cat[c]:>3} {'#' * (by_cat[c] // 2)}")
    urgency = Counter(r["label"]["urgency"] for r in labeled)
    multi = sum(r["label"]["multi_intent"] for r in labeled)
    print(f"\nUrgency: {dict(urgency)}   multi_intent: {multi} ({multi / len(labeled):.0%})")
    for field in ("account_identifier", "reference_id"):
        filled = sum(r["label"][field] is not None for r in labeled)
        print(f"{field}: filled on {filled} ({filled / len(labeled):.0%})")

    # For Phase 4 only: how often the teacher agrees with what the ticket was written to be.
    # The recipe is NOT a label; disagreement means "look here", not "the teacher is wrong".
    recipes = latest_by_id(read_jsonl(RECIPES_PATH))
    pairs = [(recipes[r["id"]]["aimed_category"], r["label"]["category"]) for r in labeled if r["id"] in recipes]
    if pairs:
        agree = sum(a == b for a, b in pairs)
        print(f"\nAgreement with the aimed category (Phase 4 hint, not accuracy): {agree}/{len(pairs)} ({agree / len(pairs):.0%})")
        for (aimed, got), n in Counter((a, b) for a, b in pairs if a != b).most_common(10):
            print(f"  aimed {aimed} → teacher {got}  ×{n}")
    if failed:
        print("\nFailures:")
        for r in failed[:10]:
            print(f"  {r['id']}: {'; '.join(r['problems'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=os.environ.get("TEACHER_MODEL"),
                        help="teacher model id (default: TEACHER_MODEL from .env)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="labels file to append to")
    parser.add_argument("--limit", type=int, help="label at most N more tickets this run")
    parser.add_argument("--workers", type=int, default=8, help="parallel requests")
    parser.add_argument("--rpm", type=float, default=40,
                        help="max requests per minute (default 40, about 80%% of a 200k tokens/min limit)")
    parser.add_argument("--dry-run", action="store_true", help="use a fake teacher, make no API calls")
    parser.add_argument("--summary", action="store_true", help="only print the summary of the labels file")
    args = parser.parse_args()

    if args.summary:
        summarize(args.out)
        return

    if args.dry_run:
        teacher, client, model = fake_teacher, None, "dry-run"
    else:
        if not args.model:
            sys.exit("Set TEACHER_MODEL in .env or pass --model with the exact model id.")
        if not os.environ.get("OPENAI_API_KEY"):
            sys.exit("Set OPENAI_API_KEY in .env or in your shell.")
        teacher, client, model = call_teacher, make_client(), args.model

    system_prompt, prompt_sha = load_prompt()
    done = latest_by_id(read_jsonl(args.out))
    mismatched = {(r["model"], r["prompt_sha"]) for r in done.values()} - {(model, prompt_sha)}
    if mismatched:
        sys.exit(f"{args.out} already has labels from a different model/prompt {sorted(mismatched)}.\n"
                 f"Current run is ({model}, {prompt_sha}). Use a new --out file rather than mixing teachers.")

    tickets = read_jsonl(TICKETS_PATH)
    todo = [t for t in tickets if t["id"] not in done or done[t["id"]]["label"] is None]
    already = len(tickets) - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(tickets)} tickets, {already} already labeled, labeling {len(todo)} "
          f"with {model} (prompt {prompt_sha})")
    if not todo:
        summarize(args.out)
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(args.rpm)
    print(f"Rate limit: {args.rpm:g} requests/min, so about {len(todo) / args.rpm:.0f} min for this run")
    started = time.monotonic()
    with args.out.open("a") as f, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(label_one, teacher, client, model, system_prompt, prompt_sha, t, limiter)
                   for t in todo]
        for i, future in enumerate(as_completed(futures), 1):
            f.write(json.dumps(future.result(), ensure_ascii=False) + "\n")
            f.flush()  # every finished label survives a crash or Ctrl-C
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} done ({time.monotonic() - started:.0f}s)")

    summarize(args.out)


if __name__ == "__main__":
    main()
