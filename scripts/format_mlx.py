"""turn the frozen splits into MLX chat-format JSONL for fine-tuning.

Usage:
    python scripts/format_mlx.py

Reads data/splits/{train,valid,test}.jsonl and writes data/mlx/{train,valid,test}.jsonl,
one line per ticket:

    {"messages": [{"role": "user", "content": <ticket text>},
                  {"role": "assistant", "content": <compact JSON label>}]}

No system message: the whole point is that the student learns the task without the prompt.
Every target is checked: it must parse, pass the spec checks (including key order and
verbatim identifiers), and round-trip back to exactly the teacher's label.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from ticket_router.spec import MAX_INPUT_CHARS, parse_output, to_target

ROOT = Path(__file__).resolve().parent.parent
SPLITS_DIR = ROOT / "data" / "splits"
OUT_DIR = ROOT / "data" / "mlx"
SPLITS = ("train", "valid", "test")


def to_example(row):
    target = to_target(row["label"])
    return {"messages": [{"role": "user", "content": row["input"]},
                         {"role": "assistant", "content": target}]}


def check(example, row):
    """Return a list of problems with one formatted example."""
    user, assistant = example["messages"]
    problems = []
    if user["content"] != row["input"]:
        problems.append("user content differs from the labeled input")
    if len(user["content"]) > MAX_INPUT_CHARS:
        problems.append(f"input longer than {MAX_INPUT_CHARS} characters")
    parsed, errors = parse_output(assistant["content"], ticket_text=user["content"], check_key_order=True)
    problems += errors
    if parsed is not None and parsed != row["label"]:
        problems.append("target doesn't round-trip to the teacher's label")
    if "\n" in assistant["content"] or ": " in assistant["content"]:
        problems.append("target is not compact single-line JSON")
    return problems


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_problems, seen_ids = [], set()
    for split in SPLITS:
        rows = [json.loads(line) for line in (SPLITS_DIR / f"{split}.jsonl").open()]
        overlap = seen_ids & {r["id"] for r in rows}
        if overlap:
            sys.exit(f"{split} shares ids with an earlier split: {sorted(overlap)[:5]}")
        seen_ids |= {r["id"] for r in rows}

        examples = []
        for row in rows:
            example = to_example(row)
            all_problems += [f"{split}/{row['id']}: {p}" for p in check(example, row)]
            examples.append(example)
        with (OUT_DIR / f"{split}.jsonl").open("w") as f:
            for example in examples:
                f.write(json.dumps(example, ensure_ascii=False) + "\n")

        in_len = sorted(len(e["messages"][0]["content"]) for e in examples)
        out_len = sorted(len(e["messages"][1]["content"]) for e in examples)
        cats = Counter(json.loads(e["messages"][1]["content"])["category"] for e in examples)
        print(f"{split:<5} {len(examples):>5} examples | input chars median {in_len[len(in_len) // 2]}, max {in_len[-1]}"
              f" | target chars median {out_len[len(out_len) // 2]}, max {out_len[-1]} | {len(cats)} categories")

    if all_problems:
        print(f"\n{len(all_problems)} problems:")
        for p in all_problems[:20]:
            print(f"  {p}")
        sys.exit(1)
    print(f"\nAll targets parse, pass the spec checks and match the teacher's labels. Wrote {OUT_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
