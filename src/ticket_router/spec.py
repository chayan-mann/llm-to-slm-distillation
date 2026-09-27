"""Constants and checks from docs/spec.md. Keep this file in sync with the spec."""

import json
import re

SPEC_VERSION = "1.3"

CATEGORIES = (
    # Account & access
    "login_issue",
    "mfa_issue",
    "sso_setup",
    "account_settings",
    "user_management",
    "account_closure",
    # Billing
    "unexpected_charge",
    "refund_request",
    "plan_change",
    "cancellation",
    "payment_failure",
    "invoice_request",
    "trial_extension",
    # Technical
    "bug_report",
    "service_outage",
    "performance_issue",
    "data_sync_issue",
    "integration_issue",
    "api_support",
    "import_export",
    "mobile_app_issue",
    "email_delivery",
    # Product usage
    "how_to_question",
    "feature_request",
    "product_feedback",
    # Security & compliance
    "security_report",
    "compromised_account",
    "privacy_compliance",
    # Other
    "sales_inquiry",
    "out_of_scope",
)
assert len(CATEGORIES) == 30 and len(set(CATEGORIES)) == 30

URGENCY_LEVELS = ("low", "normal", "high", "critical")

OUTPUT_KEYS = ("category", "multi_intent", "urgency", "account_identifier", "reference_id")
EXTRACTED_FIELDS = ("account_identifier", "reference_id")

MAX_INPUT_CHARS = 4000

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(OUTPUT_KEYS),
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "multi_intent": {"type": "boolean"},
        "urgency": {"type": "string", "enum": list(URGENCY_LEVELS)},
        "account_identifier": {"type": ["string", "null"]},
        "reference_id": {"type": ["string", "null"]},
    },
}


def format_input(body: str, subject: str | None = None) -> str:
    """Build the model input (spec §2): optional subject line, body, cut at MAX_INPUT_CHARS."""
    text = f"Subject: {subject}\n\n{body}" if subject else body
    return text[:MAX_INPUT_CHARS]


def _appears_as_token(value: str, text: str) -> bool:
    """True if `value` is in `text` as a whole token, not cut out of a longer one.

    "acct_ch4r1" inside "acct_ch4r1t." fails; "acct_ch4r1t" followed by a full stop passes.
    """
    pattern = r"(?<![\w@.\-#])" + re.escape(value) + r"(?![\w@\-]|\.\w)"
    return re.search(pattern, text) is not None


def validate_output(obj, ticket_text: str | None = None, check_key_order: bool = False) -> list[str]:
    """Return a list of spec violations; empty means the output is valid.

    Pass `ticket_text` to also check that extracted strings are copied verbatim from the ticket.
    Key order only matters for the student's raw output, so it's opt-in.
    """
    if not isinstance(obj, dict):
        return [f"output is {type(obj).__name__}, not an object"]

    errors = []
    missing = [k for k in OUTPUT_KEYS if k not in obj]
    extra = [k for k in obj if k not in OUTPUT_KEYS]
    if missing:
        errors.append(f"missing keys: {missing}")
    if extra:
        errors.append(f"unexpected keys: {extra}")
    if check_key_order and not missing and not extra and tuple(obj) != OUTPUT_KEYS:
        errors.append(f"wrong key order: {list(obj)}")

    if "category" in obj and obj["category"] not in CATEGORIES:
        errors.append(f"unknown category: {obj['category']!r}")
    if "multi_intent" in obj and not isinstance(obj["multi_intent"], bool):
        errors.append(f"multi_intent is not a boolean: {obj['multi_intent']!r}")
    if "urgency" in obj and obj["urgency"] not in URGENCY_LEVELS:
        errors.append(f"unknown urgency: {obj['urgency']!r}")

    for field in EXTRACTED_FIELDS:
        if field not in obj or obj[field] is None:
            continue
        value = obj[field]
        if not isinstance(value, str):
            errors.append(f"{field} is not a string or null: {value!r}")
        elif value.strip() == "":
            errors.append(f"{field} is an empty string; use null")
        elif ticket_text is not None and not _appears_as_token(value, ticket_text):
            errors.append(f"{field} not found verbatim (as a whole token) in ticket: {value!r}")

    if obj.get("category") == "out_of_scope":
        for field in EXTRACTED_FIELDS:
            if obj.get(field) is not None:
                errors.append(f"{field} must be null for out_of_scope (spec §7): {obj[field]!r}")

    return errors


def parse_output(text: str, ticket_text: str | None = None, check_key_order: bool = False):
    """Parse raw model text. Returns (obj or None, errors)."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return None, [f"invalid JSON: {e}"]
    return obj, validate_output(obj, ticket_text, check_key_order)


def to_target(obj: dict) -> str:
    """Serialize a valid output as the canonical training target: compact, keys in spec order."""
    return json.dumps({k: obj[k] for k in OUTPUT_KEYS}, ensure_ascii=False, separators=(",", ":"))
