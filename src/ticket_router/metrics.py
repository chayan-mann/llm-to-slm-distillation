"""Scoring model outputs against reference labels, following docs/spec.md"""

from collections import Counter

from ticket_router.spec import CATEGORIES, EXTRACTED_FIELDS, OUTPUT_KEYS, URGENCY_LEVELS, parse_output


def score(raw_outputs: list[str], references: list[dict], inputs: list[str]) -> dict:
    """Compare raw model text to reference labels. Invalid outputs count as wrong on every field."""
    n = len(references)
    preds, valid = [], 0
    for raw, text in zip(raw_outputs, inputs):
        obj, errors = parse_output(raw.strip(), ticket_text=text, check_key_order=True)
        preds.append(obj if not errors else None)
        valid += not errors

    def field_acc(key):
        return sum(p is not None and p[key] == r[key] for p, r in zip(preds, references)) / n

    result = {
        "n": n,
        "valid_json": valid / n,
        "all_fields_exact": sum(p is not None and all(p[k] == r[k] for k in OUTPUT_KEYS)
                                for p, r in zip(preds, references)) / n,
        **{f"{k}_acc": field_acc(k) for k in OUTPUT_KEYS},
    }

    # Category macro-F1 over the categories present in the references.
    f1s = []
    for cat in CATEGORIES:
        tp = sum(p is not None and p["category"] == cat and r["category"] == cat for p, r in zip(preds, references))
        fp = sum(p is not None and p["category"] == cat and r["category"] != cat for p, r in zip(preds, references))
        fn = sum(r["category"] == cat and (p is None or p["category"] != cat) for p, r in zip(preds, references))
        if tp + fn:
            f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    result["category_macro_f1"] = sum(f1s) / len(f1s)

    # multi_intent precision / recall on True.
    tp = sum(p is not None and p["multi_intent"] and r["multi_intent"] for p, r in zip(preds, references))
    pred_pos = sum(p is not None and p["multi_intent"] for p in preds)
    ref_pos = sum(r["multi_intent"] for r in references)
    result["multi_intent_precision"] = tp / pred_pos if pred_pos else None
    result["multi_intent_recall"] = tp / ref_pos if ref_pos else None

    # Urgency: how often it is off by more than one level (e.g. low vs high).
    rank = {u: i for i, u in enumerate(URGENCY_LEVELS)}
    result["urgency_off_by_2plus"] = sum(p is not None and abs(rank[p["urgency"]] - rank[r["urgency"]]) >= 2
                                         for p, r in zip(preds, references)) / n

    # Extracted fields, split by whether the reference is null, so "always null" can't look good.
    for key in EXTRACTED_FIELDS:
        present = [(p, r) for p, r in zip(preds, references) if r[key] is not None]
        absent = [(p, r) for p, r in zip(preds, references) if r[key] is None]
        result[f"{key}_acc_when_present"] = (sum(p is not None and p[key] == r[key] for p, r in present) / len(present)
                                             if present else None)
        result[f"{key}_acc_when_null"] = (sum(p is not None and p[key] is None for p, r in absent) / len(absent)
                                          if absent else None)

    result["confusions"] = Counter((r["category"], p["category"]) for p, r in zip(preds, references)
                                   if p is not None and p["category"] != r["category"]).most_common(10)
    return result, preds


def print_report(name: str, result: dict) -> None:
    pct = lambda v: "n/a" if v is None else f"{v:.1%}"
    print(f"\n== {name} (n={result['n']}) ==")
    print(f"  valid JSON (parses, schema, key order, verbatim IDs): {pct(result['valid_json'])}")
    print(f"  all 5 fields exact: {pct(result['all_fields_exact'])}")
    print(f"  category acc {pct(result['category_acc'])}, macro-F1 {pct(result['category_macro_f1'])}")
    print(f"  multi_intent acc {pct(result['multi_intent_acc'])} "
          f"(precision {pct(result['multi_intent_precision'])}, recall {pct(result['multi_intent_recall'])})")
    print(f"  urgency acc {pct(result['urgency_acc'])}, off by 2+ levels {pct(result['urgency_off_by_2plus'])}")
    for key in EXTRACTED_FIELDS:
        print(f"  {key}: acc {pct(result[key + '_acc'])} "
              f"(when present {pct(result[key + '_acc_when_present'])}, when null {pct(result[key + '_acc_when_null'])})")
    if result["confusions"]:
        print("  top category confusions (reference → predicted): "
              + "; ".join(f"{r}→{p} ×{c}" for (r, p), c in result["confusions"]))
