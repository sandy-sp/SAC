"""Legal content guardrail (GR-1 from docs/SPEC.md).

Campaign messages are scanned against a prohibited-word list before any
image processing begins. A match is a hard gate failure: the message is
flagged with the offending word(s) and must not be processed.
"""

import re

from rich.console import Console

console = Console()

# Mock prohibited-word list for the PoC (regulated/overreaching claims).
PROHIBITED_WORDS: list[str] = [
    "guaranteed",
    "cure",
    "miracle",
    "risk-free",
]


def validate_campaign_message(message: str) -> bool:
    """Check a campaign message against the prohibited-word list (GR-1).

    Case-insensitive, whole-word match. Returns True when the message is
    clean; logs a strict warning and returns False on any violation.
    """
    violations = [
        word
        for word in PROHIBITED_WORDS
        if re.search(rf"\b{re.escape(word)}\b", message, flags=re.IGNORECASE)
    ]

    if violations:
        console.print(
            f"[bold red]⛔ LEGAL GUARDRAIL VIOLATION (GR-1):[/bold red] "
            f"message [yellow]{message!r}[/yellow] contains prohibited "
            f"word(s): [bold red]{', '.join(violations)}[/bold red]. "
            f"Message blocked from processing."
        )
        return False

    return True
