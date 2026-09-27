"""Phase 4: freeze the teacher labels and split them 80/10/10, stratified by category.

Usage:
    python scripts/split.py

Writes data/splits/{train,valid,test}.jsonl, one line per ticket: {id, input, label}.
The split is by the teacher's category, with a fixed seed so re-running gives the same split.
Refuses to run if any ticket is unlabeled or the labels come from more than one prompt.
"""

import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ticket_router.spec import CATEGORIES, validate_output

ROOT = Path(__file__).resolve().parent.parent
LABELS_PATH = ROOT / "data" / "labeled" / "labels.jsonl"
OUT_DIR = ROOT / "data" / "splits"
SEED = 13
FRACTIONS = {"train": 0.8, "valid": 0.1, "test": 0.1}


def main():
    current = {}
    with LABELS_PATH.open() as f:
        for line in f:
            row = json.loads(line)
            current[row["id"]] = row

    if any(r["label"] is None for r in current.values()):
        sys.exit("Some tickets have no valid label; finish scripts/label.py first.")
    shas = {r["prompt_sha"] for r in current.values()}
    if len(shas) != 1:
        sys.exit(f"Labels come from several prompts {shas}; relabel so they all come from one.")
    invalid = [i for i, r in current.items() if validate_output(r["label"], r["input"])]
    if invalid:
        sys.exit(f"{len(invalid)} labels fail the current spec checks, e.g. {invalid[:5]}.")

    rng = random.Random(SEED)
    by_cat = defaultdict(list)
    for row in current.values():
        by_cat[row["label"]["category"]].append(row["id"])

    splits = {name: [] for name in FRACTIONS}
    for cat in CATEGORIES:
        ids = sorted(by_cat[cat])
        rng.shuffle(ids)
        n_test = max(1, round(len(ids) * FRACTIONS["test"]))
        n_valid = max(1, round(len(ids) * FRACTIONS["valid"]))
        splits["test"] += ids[:n_test]
        splits["valid"] += ids[n_test : n_test + n_valid]
        splits["train"] += ids[n_test + n_valid :]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, ids in splits.items():
        rng.shuffle(ids)
        with (OUT_DIR / f"{name}.jsonl").open("w") as f:
            for i in ids:
                r = current[i]
                f.write(json.dumps({"id": i, "input": r["input"], "label": r["label"]}, ensure_ascii=False) + "\n")

    (OUT_DIR / "README.md").write_text(
        f"Frozen from data/labeled/labels.jsonl (teacher {next(iter(current.values()))['model']}, "
        f"prompt {shas.pop()}), split {'/'.join(str(int(v * 100)) for v in FRACTIONS.values())} "
        f"by category with seed {SEED} using scripts/split.py.\n"
    )

    total = len(current)
    print(f"{total} labeled tickets → " + ", ".join(f"{n}: {len(ids)}" for n, ids in splits.items()))
    for name, ids in splits.items():
        labels = [current[i]["label"] for i in ids]
        multi = sum(l["multi_intent"] for l in labels)
        urg = Counter(l["urgency"] for l in labels)
        cats = Counter(l["category"] for l in labels)
        print(f"  {name:<5} multi {multi / len(ids):.0%} | urgency "
              + " ".join(f"{u} {urg[u] / len(ids):.0%}" for u in ("low", "normal", "high", "critical"))
              + f" | categories {len(cats)}/30, min per category {min(cats.values())}")


if __name__ == "__main__":
    main()
