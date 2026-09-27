"""Check the raw ticket batches: format, balance, and near-duplicates.

Usage:
    python scripts/check_raw.py            # report on all batches
    python scripts/check_raw.py --merge    # also write data/raw/tickets.jsonl and recipes.jsonl

Reads data/raw/batches/bNNN.tickets.jsonl and the matching bNNN.recipes.jsonl.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from ticket_router.spec import CATEGORIES, format_input

ROOT = Path(__file__).resolve().parent.parent
BATCH_DIR = ROOT / "data" / "raw" / "batches"
EVAL_PATH = ROOT / "data" / "eval" / "manual_eval.jsonl"
PROMPT_PATH = ROOT / "prompts" / "teacher_system.md"

TARGET_PER_CATEGORY = 50
DUPLICATE_THRESHOLD = 0.5  # word-trigram Jaccard similarity


def length_bucket(text):
    words = len(text.split())
    if words <= 10:
        return "very short (<=10 words)"
    if words <= 40:
        return "short (11-40)"
    if words <= 90:
        return "medium (41-90)"
    return "long (>90)"


def read_jsonl(path):
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def shingles(text):
    words = re.findall(r"[a-z0-9']+", text.lower())
    if len(words) < 3:  # too short for trigrams; compare the exact text instead
        return {("__text__", text.strip().lower())}
    return {tuple(words[i : i + 3]) for i in range(len(words) - 2)}


def jaccard(a, b):
    return len(a & b) / len(a | b)


def reference_texts():
    """Tickets the raw data must not copy: the manual eval set and the prompt's examples."""
    refs = {f"eval:{r['id']}": r["body"] for r in read_jsonl(EVAL_PATH)}
    prompt = PROMPT_PATH.read_text()
    for i, block in enumerate(re.findall(r"Ticket:\n(.*?)\n\nOutput:", prompt, re.S)):
        refs[f"prompt_example:{i + 1}"] = block
    return refs


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--merge", action="store_true", help="write merged tickets/recipes files")
    args = parser.parse_args()

    tickets, recipes, errors = [], [], []
    for ticket_path in sorted(BATCH_DIR.glob("b*.tickets.jsonl")):
        recipe_path = ticket_path.with_name(ticket_path.name.replace(".tickets.", ".recipes."))
        batch_tickets = read_jsonl(ticket_path)
        batch_recipes = read_jsonl(recipe_path) if recipe_path.exists() else []
        if [t["id"] for t in batch_tickets] != [r["id"] for r in batch_recipes]:
            errors.append(f"{ticket_path.name}: ticket and recipe ids don't line up")
        tickets += batch_tickets
        recipes += batch_recipes

    ids = [t["id"] for t in tickets]
    for id_, count in Counter(ids).items():
        if count > 1:
            errors.append(f"duplicate id {id_}")
    for t in tickets:
        if set(t) != {"id", "subject", "body"}:
            errors.append(f"{t['id']}: unexpected keys {sorted(t)}")
        if not t["body"].strip():
            errors.append(f"{t['id']}: empty body")
        if len(format_input(t["body"], t.get("subject"))) >= 4000:
            errors.append(f"{t['id']}: will be truncated at 4,000 characters")
    for r in recipes:
        if r["aimed_category"] not in CATEGORIES:
            errors.append(f"{r['id']}: unknown aimed_category {r['aimed_category']!r}")

    n = len(tickets)
    print(f"{n} tickets in {len(list(BATCH_DIR.glob('b*.tickets.jsonl')))} batches")

    by_category = Counter(r["aimed_category"] for r in recipes)
    print(f"\nPer aimed category (target {TARGET_PER_CATEGORY}):")
    for c in CATEGORIES:
        bar = "#" * (by_category[c] // 2)
        print(f"  {c:<20} {by_category[c]:>3} {bar}")

    multi = sum(r["aimed_multi_intent"] for r in recipes)
    boundary = sum(1 for r in recipes if r.get("boundary"))
    with_subject = sum(1 for t in tickets if t.get("subject"))
    print(f"\nMulti-intent: {multi} ({multi / n:.0%})   boundary traps: {boundary} ({boundary / n:.0%})"
          f"   with subject: {with_subject} ({with_subject / n:.0%})")
    tones = ", ".join(f"{k} {v}" for k, v in Counter(r["tone"] for r in recipes).most_common(12))
    print(f"tone: {tones}")
    lengths = Counter(length_bucket(t["body"]) for t in tickets)
    print("length: " + ", ".join(f"{k} {v} ({v / n:.0%})" for k, v in sorted(lengths.items())))
    words = sorted(len(t["body"].split()) for t in tickets)
    print(f"words per body: min {words[0]}, median {words[n // 2]}, 90th pct {words[int(n * 0.9)]}, max {words[-1]}")
    openers = Counter(" ".join(t["body"].lower().split()[:2]) for t in tickets)
    print("most common openings: " + ", ".join(f"'{k}' {v}" for k, v in openers.most_common(8)))

    sh = {t["id"]: shingles(t["body"]) for t in tickets}
    dupes = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            s = jaccard(sh[a], sh[b])
            if s >= DUPLICATE_THRESHOLD:
                dupes.append((s, a, b))
    for ref_id, text in reference_texts().items():
        ref = shingles(text)
        for t in ids:
            s = jaccard(ref, sh[t])
            if s >= DUPLICATE_THRESHOLD:
                dupes.append((s, t, ref_id))
    print(f"\nNear-duplicates (similarity >= {DUPLICATE_THRESHOLD}): {len(dupes)}")
    for s, a, b in sorted(dupes, reverse=True):
        print(f"  {a} ~ {b}  {s:.2f}")

    if errors:
        print(f"\n{len(errors)} errors:")
        for e in errors:
            print(f"  {e}")

    if args.merge:
        if errors or dupes:
            sys.exit("\nNot merging: fix the errors and near-duplicates first.")
        for name, rows in (("tickets", tickets), ("recipes", recipes)):
            with (ROOT / "data" / "raw" / f"{name}.jsonl").open("w") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("\nWrote data/raw/tickets.jsonl and data/raw/recipes.jsonl")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
