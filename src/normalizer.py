"""Stage 1: Deterministic ticket normalization without LLM interaction."""

import json
from pathlib import Path
from typing import Union, Any

from src.models import NormalizedTicket, RawTicket


def create_text_for_model(subject: str, message: str) -> str:
    """Deterministically combines subject and message into a formatted string."""
    cleaned_subject = subject.strip()
    cleaned_message = message.strip()
    return f"Subject: {cleaned_subject}\nMessage: {cleaned_message}"


def normalize_ticket(raw_data: Union[dict[str, Any], RawTicket]) -> NormalizedTicket:
    """Normalizes a single raw ticket into the exact schema required for Stage 1.

    Drops customer_id, computes text_for_model, and determines character count.
    """
    if isinstance(raw_data, dict):
        ticket = RawTicket.model_validate(raw_data)
    else:
        ticket = raw_data

    text_for_model = create_text_for_model(ticket.subject, ticket.message)
    char_count = len(text_for_model)

    return NormalizedTicket(
        ticket_id=ticket.ticket_id,
        subject=ticket.subject,
        message=ticket.message,
        channel=ticket.channel,
        created_at=ticket.created_at,
        text_for_model=text_for_model,
        char_count=char_count,
    )


def run_normalization(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
) -> list[NormalizedTicket]:
    """Reads raw tickets from disk, normalizes them, and writes normalized_tickets.json.

    Args:
        input_path: Path to tickets.json.
        output_path: Destination path for normalized_tickets.json.

    Returns:
        A list of validated NormalizedTicket objects.
    """
    input_file = Path(input_path)
    output_file = Path(output_path)

    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found at {input_file.resolve()}")

    with open(input_file, "r", encoding="utf-8") as f:
        raw_items: list[dict[str, Any]] = json.load(f)

    normalized_tickets: list[NormalizedTicket] = [
        normalize_ticket(item) for item in raw_items
    ]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            [ticket.model_dump() for ticket in normalized_tickets],
            f,
            indent=2,
            ensure_ascii=False,
        )

    return normalized_tickets
