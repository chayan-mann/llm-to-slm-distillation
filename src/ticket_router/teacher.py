"""The teacher: gpt-6-luna (or whatever TEACHER_MODEL says) with the long system prompt.

Shared by scripts/run_teacher.py (Phase 1 eval) and scripts/label.py (Phase 3 labeling),
so both phases call the teacher in exactly the same way.
"""

import hashlib
from pathlib import Path

from openai import OpenAI

from ticket_router.spec import OUTPUT_SCHEMA

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "teacher_system.md"


def load_prompt() -> tuple[str, str]:
    """Return (prompt text, short sha256) so every label can record which prompt made it."""
    text = PROMPT_PATH.read_text()
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


def make_client() -> OpenAI:
    return OpenAI(max_retries=5)  # reads OPENAI_API_KEY; retries 429s and 5xx with backoff


def call_teacher(client: OpenAI, model: str, system_prompt: str, ticket_text: str) -> str:
    """Send one ticket to the teacher and return its raw text output.

    The JSON schema is enforced by the API, so the output always parses;
    the rules the schema can't express are checked afterwards by spec.validate_output.
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": ticket_text},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "ticket_route", "strict": True, "schema": OUTPUT_SCHEMA},
        },
    )
    return response.choices[0].message.content
