"""Score a fine-tuned student (base model + LoRA adapter, or a fused model) with no system prompt.

Usage:
    export HF_HOME=~/models/huggingface HF_HUB_OFFLINE=1
    python scripts/eval_student.py --adapter-dir adapters/qwen2.5-1.5b-4bit-r8 --split valid --all-checkpoints
    python scripts/eval_student.py --adapter-dir adapters/qwen2.5-1.5b-4bit-r8 --checkpoint 0000300 --split test
    python scripts/eval_student.py --adapter-dir adapters/qwen2.5-1.5b-4bit-r8 --split eval   # hand-checked gold
    python scripts/eval_student.py --model models/ticket-router-qwen2.5-1.5b-4bit --split test  # fused model

Splits: valid / test compare against the teacher's labels (data/mlx/); eval compares against the
hand-checked gold answers (data/eval/manual_eval.jsonl). Predictions go to runs/student_<...>/.
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from mlx_lm import generate, load

from ticket_router.metrics import print_report, score
from ticket_router.spec import format_input

ROOT = Path(__file__).resolve().parent.parent
MAX_TOKENS = 64  # the longest target is ~44 tokens


def load_split(split):
    if split == "eval":
        rows = [json.loads(line) for line in (ROOT / "data" / "eval" / "manual_eval.jsonl").open()]
        return [(r["id"], format_input(r["body"], r.get("subject")), r["gold"]) for r in rows]
    rows = [json.loads(line) for line in (ROOT / "data" / "mlx" / f"{split}.jsonl").open()]
    ids = [json.loads(line)["id"] for line in (ROOT / "data" / "splits" / f"{split}.jsonl").open()]
    return [(i, r["messages"][0]["content"], json.loads(r["messages"][1]["content"])) for i, r in zip(ids, rows)]


def run(model, tokenizer, examples):
    outputs, latencies = [], []
    for _, text, _ in examples:
        prompt = tokenizer.apply_chat_template([{"role": "user", "content": text}],
                                               tokenize=False, add_generation_prompt=True)
        start = time.monotonic()
        outputs.append(generate(model, tokenizer, prompt=prompt, max_tokens=MAX_TOKENS))  # greedy
        latencies.append(time.monotonic() - start)
    return outputs, latencies


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--adapter-dir", type=Path, help="LoRA adapter folder (base model read from its config)")
    source.add_argument("--model", type=Path, help="a fused model folder, no adapter")
    parser.add_argument("--split", choices=["valid", "test", "eval"], default="valid")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--checkpoint", help="e.g. 0000300; default is the final adapters.safetensors")
    group.add_argument("--all-checkpoints", action="store_true", help="score every NNNNNNN_adapters.safetensors")
    parser.add_argument("--limit", type=int, help="only the first N examples")
    args = parser.parse_args()

    if args.model:
        model, tokenizer = load(str(args.model))
        weight_files, run_name = [None], args.model.name
    else:
        config = json.loads((args.adapter_dir / "adapter_config.json").read_text())
        model, tokenizer = load(config["model"], adapter_path=str(args.adapter_dir))
        run_name = args.adapter_dir.name

    if args.model:
        pass
    elif args.all_checkpoints:
        weight_files = sorted(args.adapter_dir.glob("[0-9]*_adapters.safetensors"))
    elif args.checkpoint:
        weight_files = [args.adapter_dir / f"{args.checkpoint}_adapters.safetensors"]
    else:
        weight_files = [args.adapter_dir / "adapters.safetensors"]

    examples = load_split(args.split)[: args.limit]
    out_dir = ROOT / "runs" / f"student_{run_name}_{args.split}_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True)
    summary = []
    for weights in weight_files:
        if weights is not None:
            model.load_weights(str(weights), strict=False)  # swap in this checkpoint's LoRA weights
        outputs, latencies = run(model, tokenizer, examples)
        result, preds = score(outputs, [ref for _, _, ref in examples], [text for _, text, _ in examples])
        name = "fused" if weights is None else weights.name.replace("_adapters.safetensors", "").replace(".safetensors", "")
        latencies.sort()
        result["latency_median_s"] = latencies[len(latencies) // 2]
        print_report(f"{name} on {args.split}", result)
        print(f"  median latency {result['latency_median_s']:.2f}s per ticket")
        with (out_dir / f"{name}.jsonl").open("w") as f:
            for (i, text, ref), raw, pred in zip(examples, outputs, preds):
                f.write(json.dumps({"id": i, "input": text, "reference": ref, "raw": raw, "pred": pred},
                                   ensure_ascii=False) + "\n")
        summary.append({"checkpoint": name, **{k: v for k, v in result.items() if k != "confusions"}})

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if len(summary) > 1:
        print("\ncheckpoint  valid_json  all_exact  category  macroF1  multi  urgency  acct  ref")
        for s in summary:
            print(f"{s['checkpoint']:<11} {s['valid_json']:>9.1%} {s['all_fields_exact']:>10.1%} {s['category_acc']:>9.1%}"
                  f" {s['category_macro_f1']:>8.1%} {s['multi_intent_acc']:>6.1%} {s['urgency_acc']:>8.1%}"
                  f" {s['account_identifier_acc']:>5.1%} {s['reference_id_acc']:>5.1%}")
    print(f"\nSaved predictions to {out_dir.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
