"""Route a customer support ticket with the fine-tuned student model (Apple Silicon, MLX).

Usage:
    python route.py "I was charged twice this month, invoice INV-4412"
    python route.py --subject "Charged twice" "Invoice INV-4412 shows two charges."
    echo "cant login" | python route.py -
    python route.py --file tickets.txt            # one ticket per line -> one JSON line each
    python route.py --serve --port 8080           # POST /route {"text": "...", "subject": "..."}

The base model (mlx-community/Qwen2.5-1.5B-Instruct-4bit, ~870 MB) is downloaded from
Hugging Face on first run and cached. Set HF_HOME to reuse an existing download.
The adapter in ./adapter must stay paired with that exact base model.
"""

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent / "adapter"
MAX_INPUT_CHARS = 4000
MAX_TOKENS = 64

CATEGORIES = {
    "login_issue", "mfa_issue", "sso_setup", "account_settings", "user_management", "account_closure",
    "unexpected_charge", "refund_request", "plan_change", "cancellation", "payment_failure",
    "invoice_request", "trial_extension", "bug_report", "service_outage", "performance_issue",
    "data_sync_issue", "integration_issue", "api_support", "import_export", "mobile_app_issue",
    "email_delivery", "how_to_question", "feature_request", "product_feedback", "security_report",
    "compromised_account", "privacy_compliance", "sales_inquiry", "out_of_scope",
}
URGENCY = {"low", "normal", "high", "critical"}
KEYS = ("category", "multi_intent", "urgency", "account_identifier", "reference_id")

# The student's known weak spot: a security problem mentioned alongside something else can be
# routed as a normal request. These words make sure a human sees it anyway.
SECURITY_WORDS = re.compile(
    # attacks and vulnerability language
    r"hack(ed|er|ing)?\b|compromis|phish|\bscam|breach|stolen|\bleak(ed|s|ing)?\b|exposed?\b|unauthori[sz]ed access"
    r"|\bvulnerab|\bxss\b|\bcsrf\b|\bcors\b|\bidor\b|\bssrf\b|injection|clickjack|open redirect|privilege escalation"
    r"|bypass|brute[- ]?force|credential stuffing|pen ?test|penetration test|decompil|hard-?coded (api )?key|takeover|take (it )?over"
    r"|security (issue|researcher|engineer|hole|bug|vulnerability|contact|team)|responsible(ly)? disclos|bug bounty|suspicious activity"
    # signs someone else is in the account
    r"|(don'?t|do not|didn'?t|did not|never) recogni[sz]e|unrecogni[sz]ed|(unknown|suspicious|strange|new) (login|device|session|sign-?in|ip|location|user|admin)"
    r"|log(ged)?[ -]?in(s)? (to my account )?from|(logged|got|get) into (my|our|his|her) (account|workspace)|how did they get in|used from an ip"
    r"|someone (else )?(has |is )?(logged|got|getting|accessing|accessed|using|added|posting|changed)"
    r"|(wasn'?t|was not|isn'?t) me\b|(didn'?t|did not) (do|request|make|create|change) (that|this|it|them)|\bi didn'?t\.|without (me|my|our) (permission|knowledge)?"
    r"|nobody (on our side|here|of us|remembers)|none of (us|our admins)|(password|email) (was |has been |got )?changed"
    r"|(ex-|former )(employee|contractor|intern).{0,60}(still|access|export|log)",
    re.IGNORECASE,
)
# Tuned on the 1,500 labeled tickets: catches ~90% of the teacher's security/compromise tickets,
# and ~3% of other tickets also match (a false alarm only costs a human a glance).
SECURITY_CATEGORIES = {"security_report", "compromised_account"}


def format_input(text: str, subject: str | None = None) -> str:
    """Same input format the model was trained on: optional subject line, body, max 4,000 chars."""
    full = f"Subject: {subject}\n\n{text}" if subject else text
    return full[:MAX_INPUT_CHARS]


def _is_token(value: str, text: str) -> bool:
    return re.search(r"(?<![\w@.\-#])" + re.escape(value) + r"(?![\w@\-]|\.\w)", text) is not None


def check_output(raw: str, ticket: str):
    """Return (route, problem). route is None if the model output is unusable."""
    try:
        obj = json.loads(raw.strip())
    except json.JSONDecodeError:
        return None, "output is not valid JSON"
    if not isinstance(obj, dict) or set(obj) != set(KEYS):
        return None, "output does not have exactly the five routing fields"
    if obj["category"] not in CATEGORIES:
        return None, f"unknown category {obj['category']!r}"
    if obj["urgency"] not in URGENCY or not isinstance(obj["multi_intent"], bool):
        return None, "invalid urgency or multi_intent"
    for key in ("account_identifier", "reference_id"):
        value = obj[key]
        if value is not None and (not isinstance(value, str) or not _is_token(value, ticket)):
            return None, f"{key} {value!r} does not appear in the ticket"
    return {k: obj[k] for k in KEYS}, None


class Router:
    def __init__(self, adapter_dir: Path = ADAPTER_DIR):
        from mlx_lm import load  # imported here so --help works without mlx installed

        base = json.loads((adapter_dir / "adapter_config.json").read_text())["model"]
        self.model, self.tokenizer = load(base, adapter_path=str(adapter_dir))

    def route(self, text: str, subject: str | None = None) -> dict:
        from mlx_lm import generate

        ticket = format_input(text, subject)
        prompt = self.tokenizer.apply_chat_template([{"role": "user", "content": ticket}],
                                                    tokenize=False, add_generation_prompt=True)
        raw = generate(self.model, self.tokenizer, prompt=prompt, max_tokens=MAX_TOKENS)

        route, problem = check_output(raw, ticket)
        if route is None:
            return {**{k: None for k in KEYS}, "needs_review": True,
                    "review_reason": f"model output failed validation: {problem}", "raw_output": raw}

        reasons = []
        match = SECURITY_WORDS.search(ticket)
        if match and (route["category"] not in SECURITY_CATEGORIES or route["urgency"] != "critical"):
            reasons.append(f"security words ({match.group(0)!r}) but routed as "
                           f"{route['category']}/{route['urgency']}")
        return {**route, "needs_review": bool(reasons), "review_reason": "; ".join(reasons) or None}


def serve(router: Router, host: str, port: int):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, {"status": "ok"}) if self.path == "/health" else self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/route":
                return self._send(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                text = body["text"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError
            except (ValueError, KeyError, json.JSONDecodeError):
                return self._send(400, {"error": 'send JSON like {"text": "...", "subject": "optional"}'})
            try:
                self._send(200, router.route(text, body.get("subject")))
            except Exception as e:  # never answer with an empty response
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    print(f"Routing tickets on http://{host}:{port}/route (GET /health to check)", file=sys.stderr)
    # Single-threaded on purpose: MLX runs the model on the thread that loaded it,
    # and there is one GPU, so requests are handled one at a time (~0.4 s each).
    HTTPServer((host, port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n".join(__doc__.splitlines()[2:]))
    parser.add_argument("text", nargs="?", help='ticket text, or "-" to read it from stdin')
    parser.add_argument("--subject", help="optional subject line")
    parser.add_argument("--file", type=Path, help="route every line of this file (one ticket per line)")
    parser.add_argument("--serve", action="store_true", help="run a local HTTP API instead")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if not (args.text or args.file or args.serve):
        parser.error("give ticket text, - for stdin, --file, or --serve")

    router = Router()
    if args.serve:
        serve(router, args.host, args.port)
    elif args.file:
        for line in args.file.read_text().splitlines():
            if line.strip():
                print(json.dumps(router.route(line), ensure_ascii=False), flush=True)
    else:
        text = sys.stdin.read() if args.text == "-" else args.text
        print(json.dumps(router.route(text, args.subject), ensure_ascii=False))


if __name__ == "__main__":
    main()
