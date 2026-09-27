""" human review of a sample of teacher labels.

Usage:
    python scripts/review.py make                   # build data/review/review.html from the labels
    python scripts/review.py report <results.json>  # summarise the file the page downloads

The sample is 2 random tickets per category (by the teacher's label) plus 40 more at random,
100 in total, with a fixed seed so re-running `make` gives the same sample.
"""

import argparse
import html
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ticket_router.spec import CATEGORIES, OUTPUT_KEYS, URGENCY_LEVELS

ROOT = Path(__file__).resolve().parent.parent
LABELS_PATH = ROOT / "data" / "labeled" / "labels.jsonl"
OUT_DIR = ROOT / "data" / "review"
SEED = 7


def load_labels(path=LABELS_PATH):
    current = {}
    with Path(path).open() as f:
        for line in f:
            row = json.loads(line)
            current[row["id"]] = row
    missing = [r["id"] for r in current.values() if r["label"] is None]
    if missing:
        sys.exit(f"{len(missing)} tickets have no valid label yet; finish scripts/label.py first.")
    return current


def pick_sample(labels):
    rng = random.Random(SEED)
    by_cat = defaultdict(list)
    for row in labels.values():
        by_cat[row["label"]["category"]].append(row["id"])
    chosen = []
    for cat in CATEGORIES:
        ids = sorted(by_cat[cat])
        chosen += rng.sample(ids, min(2, len(ids)))
    rest = sorted(set(labels) - set(chosen))
    chosen += rng.sample(rest, 100 - len(chosen))
    rng.shuffle(chosen)
    return chosen


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Label Review</title>
<style>
:root { --bg:#f7f7f5; --card:#fff; --text:#1d1d1b; --muted:#6b6b66; --line:#e2e2dc; --ok:#1f7a4d; --bad:#b3261e; --accent:#2f5bd3; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --card:#21211f; --text:#ececea; --muted:#9a9a94; --line:#34342f; --ok:#5cc28f; --bad:#f07167; --accent:#8aa8ff; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 -apple-system, system-ui, sans-serif; }
header { position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line); padding:10px 16px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
header b { font-size:16px; }
.progress { color:var(--muted); }
button { font:inherit; padding:6px 12px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--text); cursor:pointer; }
button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
main { max-width:860px; margin:0 auto; padding:16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }
.ticket { white-space:pre-wrap; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:12px; margin:8px 0 16px; max-height:340px; overflow:auto; }
.meta { color:var(--muted); font-size:13px; }
table { width:100%; border-collapse:collapse; }
td { padding:6px 4px; border-top:1px solid var(--line); vertical-align:middle; }
td.k { width:150px; color:var(--muted); font-family:ui-monospace, monospace; font-size:13px; }
td.v { font-family:ui-monospace, monospace; }
td.w { width:230px; text-align:right; }
tr.wrong td.v { color:var(--bad); text-decoration:line-through; }
select, input[type=text], textarea { font:inherit; padding:4px 6px; border-radius:6px; border:1px solid var(--line); background:var(--bg); color:var(--text); max-width:100%; }
textarea { width:100%; min-height:54px; margin-top:10px; }
.verdict { display:flex; gap:8px; margin-top:12px; flex-wrap:wrap; align-items:center; }
.status-ok { color:var(--ok); font-weight:600; } .status-bad { color:var(--bad); font-weight:600; } .status-none { color:var(--muted); }
.nav { display:flex; justify-content:space-between; margin-top:14px; }
.help { color:var(--muted); font-size:13px; margin-top:12px; }
</style></head><body>
<header><b>Label review</b><span class="progress" id="progress"></span>
<span style="flex:1"></span><button onclick="downloadResults()" class="primary">Download results</button></header>
<main><div class="card" id="card"></div>
<div class="help">Read the ticket, decide what <i>you</i> think is right, then check the label. <b>Y</b> = all correct &nbsp;·&nbsp; <b>←/→</b> = previous/next &nbsp;·&nbsp; Mark a field wrong to enter the correct value. Your answers autosave in this browser; click "Download results" when done.</div></main>
<script>
const ITEMS = __ITEMS__;
const CATS = __CATS__, URG = __URG__, KEYS = __KEYS__;
const STORE = "label-review-" + __SHA__;
let state = {}; try { state = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) {}
let i = 0;
const save = () => { try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) {} };
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const show = v => v === null ? "null" : JSON.stringify(v);
function entry(id) { return state[id] || (state[id] = {verdict: null, wrong: {}, notes: ""}); }
function editor(k, cur) {
  if (k === "category") return `<select data-k="${k}">${CATS.map(c => `<option ${c===cur?"selected":""}>${c}</option>`).join("")}</select>`;
  if (k === "urgency") return `<select data-k="${k}">${URG.map(c => `<option ${c===cur?"selected":""}>${c}</option>`).join("")}</select>`;
  if (k === "multi_intent") return `<select data-k="${k}"><option ${cur===true?"selected":""}>true</option><option ${cur===false?"selected":""}>false</option></select>`;
  return `<input type="text" data-k="${k}" placeholder="null" value="${cur===null||cur===undefined?"":esc(cur)}">`;
}
function render() {
  const it = ITEMS[i], e = entry(it.id);
  const done = ITEMS.filter(x => state[x.id] && state[x.id].verdict).length;
  document.getElementById("progress").textContent = `${i+1} / ${ITEMS.length} · ${done} reviewed`;
  const rows = KEYS.map(k => {
    const w = k in e.wrong;
    return `<tr class="${w?"wrong":""}"><td class="k">${k}</td><td class="v">${esc(show(it.label[k]))}</td>
      <td class="w">${w ? editor(k, e.wrong[k]) : `<button data-mark="${k}">wrong</button>`}</td></tr>`;
  }).join("");
  const status = e.verdict === "ok" ? '<span class="status-ok">✓ all correct</span>' : e.verdict === "wrong" ? '<span class="status-bad">✗ has errors</span>' : '<span class="status-none">not reviewed</span>';
  document.getElementById("card").innerHTML = `<div class="meta">${it.id}</div>
    <div class="ticket">${esc(it.input)}</div><table>${rows}</table>
    <textarea id="notes" placeholder="Notes (optional): why it's wrong, or if the spec is unclear here">${esc(e.notes)}</textarea>
    <div class="verdict"><button class="primary" onclick="allCorrect()">✓ All correct (Y)</button><button onclick="clearIt()">Reset</button>${status}</div>
    <div class="nav"><button onclick="go(-1)">← Previous</button><button onclick="go(1)">Next →</button></div>`;
  document.querySelectorAll("[data-mark]").forEach(b => b.onclick = () => {
    const k = b.dataset.mark; e.wrong[k] = it.label[k]; e.verdict = "wrong"; save(); render(); });
  document.querySelectorAll("[data-k]").forEach(el => el.onchange = () => {
    const k = el.dataset.k; let v = el.value;
    if (k === "multi_intent") v = v === "true"; else if (k === "account_identifier" || k === "reference_id") v = v.trim() === "" ? null : v;
    e.wrong[k] = v; save(); });
  document.getElementById("notes").oninput = ev => { e.notes = ev.target.value; save(); };
}
function allCorrect() { const e = entry(ITEMS[i].id); e.verdict = "ok"; e.wrong = {}; save(); go(1); }
function clearIt() { state[ITEMS[i].id] = {verdict: null, wrong: {}, notes: ""}; save(); render(); }
function go(d) { i = Math.max(0, Math.min(ITEMS.length - 1, i + d)); render(); window.scrollTo(0, 0); }
document.addEventListener("keydown", ev => {
  if (["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName)) return;
  if (ev.key === "y" || ev.key === "Y") allCorrect();
  if (ev.key === "ArrowRight") go(1);
  if (ev.key === "ArrowLeft") go(-1);
});
function downloadResults() {
  const out = ITEMS.map(it => ({id: it.id, label: it.label, ...(state[it.id] || {verdict: null, wrong: {}, notes: ""})}));
  const blob = new Blob([JSON.stringify({prompt_sha: __SHA__, items: out}, null, 2)], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "review_results.json"; a.click();
}
render();
</script></body></html>
"""


def make(labels_path=LABELS_PATH, out_dir=OUT_DIR):
    labels = load_labels(labels_path)
    shas = {r["prompt_sha"] for r in labels.values()}
    if len(shas) != 1:
        sys.exit(f"Labels come from several prompts {shas}; expected one.")
    sha = shas.pop()
    ids = pick_sample(labels)
    items = [{"id": i, "input": labels[i]["input"], "label": labels[i]["label"]} for i in ids]
    page = (PAGE.replace("__ITEMS__", json.dumps(items))
                .replace("__CATS__", json.dumps(list(CATEGORIES)))
                .replace("__URG__", json.dumps(list(URGENCY_LEVELS)))
                .replace("__KEYS__", json.dumps(list(OUTPUT_KEYS)))
                .replace("__SHA__", json.dumps(sha)))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review.html").write_text(page)
    (out_dir / "sample_ids.json").write_text(json.dumps(ids, indent=0))
    print(f"Wrote {out_dir}/review.html with {len(items)} tickets (prompt {sha}).")
    print("Open it in your browser, review, then click 'Download results'.")


def report(path):
    data = json.loads(Path(path).read_text())
    items = data["items"]
    reviewed = [x for x in items if x["verdict"]]
    print(f"{len(reviewed)}/{len(items)} reviewed (prompt {data['prompt_sha']})")
    if not reviewed:
        return
    n = len(reviewed)
    ok = sum(x["verdict"] == "ok" for x in reviewed)
    print(f"All 5 fields correct: {ok}/{n} ({ok / n:.0%})\n\nPer-field accuracy (your judgement):")
    for k in OUTPUT_KEYS:
        wrong = sum(k in x["wrong"] and x["wrong"][k] != x["label"][k] for x in reviewed)
        print(f"  {k:<20} {n - wrong}/{n} ({(n - wrong) / n:.0%})")
    cat_errors = Counter((x["label"]["category"], x["wrong"]["category"]) for x in reviewed
                         if "category" in x["wrong"] and x["wrong"]["category"] != x["label"]["category"])
    if cat_errors:
        print("\nCategory corrections (teacher → you):")
        for (t, y), c in cat_errors.most_common():
            print(f"  {t} → {y}  ×{c}")
    print("\nTickets marked wrong:")
    for x in reviewed:
        changes = {k: v for k, v in x["wrong"].items() if v != x["label"][k]}
        if x["verdict"] == "wrong":
            fixes = ", ".join(f"{k}: {html.unescape(json.dumps(x['label'][k]))} → {json.dumps(v)}" for k, v in changes.items())
            print(f"  {x['id']}: {fixes or '(marked wrong, no field changed)'}" + (f"  | note: {x['notes']}" if x["notes"] else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("--labels", type=Path, default=LABELS_PATH)
    mk.add_argument("--out-dir", type=Path, default=OUT_DIR)
    rep = sub.add_parser("report")
    rep.add_argument("results")
    args = parser.parse_args()
    make(args.labels, args.out_dir) if args.cmd == "make" else report(args.results)


if __name__ == "__main__":
    main()
