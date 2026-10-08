"""Stage 5: Confidence-based and category-based escalation rules (Should Attempt 5)."""

import json
from pathlib import Path
from typing import Any, Dict, List, Union

from src.models import EscalationItem

CONFIDENCE_THRESHOLD = 0.60
ESCALATION_CATEGORY = "other"


def evaluate_escalations(
    predictions: List[Dict[str, Any]],
    output_path: Union[str, Path] = "escalations.json",
) -> List[Dict[str, Any]]:
    """Deterministically identifies tickets requiring human escalation.

    A ticket is flagged for manual escalation if:
      - category == "other", OR
      - confidence < 0.60

    Args:
        predictions: Output list from Stage 2 (triage predictions).
        output_path: Destination path for escalations.json.

    Returns:
        List of flagged escalation records.
    """
    escalated_items: List[Dict[str, Any]] = []

    for pred in predictions:
        ticket_id = pred["ticket_id"]
        category = pred["category"]
        confidence = float(pred.get("confidence", 1.0))

        # Deterministic criteria evaluation
        reasons = []
        if category == ESCALATION_CATEGORY:
            reasons.append(f"Category is '{ESCALATION_CATEGORY}'")
        if confidence < CONFIDENCE_THRESHOLD:
            reasons.append(
                f"Confidence score {confidence:.2f} is below threshold {CONFIDENCE_THRESHOLD:.2f}"
            )

        # If any escalation condition was triggered
        if reasons:
            escalation_record = EscalationItem(
                ticket_id=ticket_id,
                category=category,
                confidence=confidence,
                reason="; ".join(reasons),
            )
            escalated_items.append(escalation_record.model_dump())

    # Write out artifact to disk
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(escalated_items, f, indent=2, ensure_ascii=False)

    return escalated_items
