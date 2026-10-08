"""Automated validation script verifying pipeline integrity, artifacts, and business constraints."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

# Expected file paths
TICKETS_FILE = Path("tickets.json")
CONFIG_FILE = Path("triage_config.json")
NORMALIZED_FILE = Path("normalized_tickets.json")
PREDICTIONS_FILE = Path("triage_predictions.json")
OVERRIDES_FILE = Path("review_overrides.json")
FINAL_QUEUE_FILE = Path("final_queue.json")
SUMMARY_FILE = Path("queue_summary.md")

# Optional/Stretch artifacts
ESCALATIONS_FILE = Path("escalations.json")
LLM_LOGS_FILE = Path("llm_calls.jsonl")


class ValidationError(Exception):
    """Raised when an artifact or validation check fails."""


def load_json(path: Path) -> Any:
    """Verifies existence and parses JSON from disk."""
    if not path.exists():
        raise ValidationError(f"Required artifact is missing: {path.name}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Invalid JSON syntax in {path.name}: {e}")


def check_required_artifacts() -> None:
    """Verifies that all required files and summary reports exist on disk."""
    required = [
        TICKETS_FILE,
        CONFIG_FILE,
        NORMALIZED_FILE,
        PREDICTIONS_FILE,
        OVERRIDES_FILE,
        FINAL_QUEUE_FILE,
        SUMMARY_FILE,
    ]
    for file_path in required:
        if not file_path.exists():
            raise ValidationError(f"Missing required artifact: {file_path}")
        if file_path.stat().st_size == 0 and file_path != OVERRIDES_FILE:
            raise ValidationError(f"Artifact is unexpectedly empty: {file_path}")


def check_pipeline_chronology() -> None:
    """Verifies deterministic normalization was written before LLM predictions were saved."""
    norm_mtime = NORMALIZED_FILE.stat().st_mtime
    pred_mtime = PREDICTIONS_FILE.stat().st_mtime

    # In clean runs, normalization must be created/modified before or at predictions time
    if norm_mtime > pred_mtime:
        raise ValidationError(
            f"Chronology violation: {NORMALIZED_FILE.name} timestamp ({norm_mtime}) "
            f"is newer than {PREDICTIONS_FILE.name} ({pred_mtime}). "
            "Normalization must occur before LLM calls."
        )


def check_cardinality_and_ids(
    raw_tickets: List[Dict[str, Any]],
    normalized_tickets: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
    final_queue: List[Dict[str, Any]],
) -> None:
    """Verifies exact 1:1 correspondence across all pipeline stages."""
    raw_ids = [t["ticket_id"] for t in raw_tickets]
    norm_ids = [t["ticket_id"] for t in normalized_tickets]
    pred_ids = [t["ticket_id"] for t in predictions]
    final_ids = [t["ticket_id"] for t in final_queue]

    if len(raw_ids) != len(set(raw_ids)):
        raise ValidationError("Duplicate ticket_id found in input tickets.json")

    if raw_ids != norm_ids:
        raise ValidationError(
            "IDs or order mismatch between tickets.json and normalized_tickets.json"
        )

    if set(raw_ids) != set(pred_ids) or len(predictions) != len(raw_tickets):
        raise ValidationError(
            f"Prediction cardinality mismatch: expected {len(raw_tickets)} unique predictions, "
            f"received {len(predictions)}"
        )

    if set(raw_ids) != set(final_ids) or len(final_queue) != len(raw_tickets):
        raise ValidationError(
            f"Final queue cardinality mismatch: expected {len(raw_tickets)} tickets, "
            f"received {len(final_queue)}"
        )


def check_config_adherence(
    final_queue: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> None:
    """Verifies categories, priorities, routing derivation, and max word limits."""
    allowed_categories: Set[str] = set(config.get("allowed_categories", []))
    allowed_priorities: Set[str] = set(config.get("allowed_priorities", []))
    routing_rules: Dict[str, str] = config.get("routing_rules", {})
    max_words: int = config.get("reply_style", {}).get("max_words", 80)

    for item in final_queue:
        t_id = item.get("ticket_id")

        # 1. Allowed category check
        cat = item.get("final_category")
        if cat not in allowed_categories:
            raise ValidationError(
                f"Ticket {t_id} has invalid category '{cat}'. Must be one of {allowed_categories}"
            )

        # 2. Allowed priority check
        prio = item.get("final_priority")
        if prio not in allowed_priorities:
            raise ValidationError(
                f"Ticket {t_id} has invalid priority '{prio}'. Must be one of {allowed_priorities}"
            )

        # 3. Routing rule derived check
        expected_route = routing_rules.get(cat, "manual_review_queue")
        actual_route = item.get("final_route_to")
        if actual_route != expected_route:
            raise ValidationError(
                f"Ticket {t_id} routing mismatch: category '{cat}' must route to "
                f"'{expected_route}', found '{actual_route}'"
            )

        # 4. Suggested reply word limit check
        reply = item.get("suggested_reply", "")
        word_count = len(reply.strip().split())
        if word_count > max_words:
            raise ValidationError(
                f"Ticket {t_id} reply exceeded max word limit: {word_count} words (max allowed: {max_words})"
            )


def check_overrides_application(
    final_queue: List[Dict[str, Any]],
    overrides: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> None:
    """Verifies that human corrections strictly updated downstream attributes."""
    overrides_map = {ov["ticket_id"]: ov for ov in overrides}
    pred_map = {p["ticket_id"]: p for p in predictions}
    allowed_categories = set(config.get("allowed_categories", []))
    allowed_priorities = set(config.get("allowed_priorities", []))

    for item in final_queue:
        t_id = item["ticket_id"]
        was_overridden = item.get("was_overridden", False)

        if t_id in overrides_map:
            ov = overrides_map[t_id]

            if not was_overridden:
                raise ValidationError(
                    f"Ticket {t_id} was overridden in review but was_overridden is False"
                )

            # Check override validity against config
            if ov["new_category"] not in allowed_categories:
                raise ValidationError(
                    f"Override for {t_id} contains invalid new_category '{ov['new_category']}'"
                )
            if ov["new_priority"] not in allowed_priorities:
                raise ValidationError(
                    f"Override for {t_id} contains invalid new_priority '{ov['new_priority']}'"
                )

            # Check that final queue reflects the overridden values
            if item["final_category"] != ov["new_category"]:
                raise ValidationError(
                    f"Override mismatch for {t_id}: category expected '{ov['new_category']}', "
                    f"got '{item['final_category']}'"
                )
            if item["final_priority"] != ov["new_priority"]:
                raise ValidationError(
                    f"Override mismatch for {t_id}: priority expected '{ov['new_priority']}', "
                    f"got '{item['final_priority']}'"
                )
        else:
            if was_overridden:
                raise ValidationError(
                    f"Ticket {t_id} marked as was_overridden=True but no override exists"
                )
            # Unmodified tickets must match initial predictions
            if item["final_category"] != pred_map[t_id]["category"]:
                raise ValidationError(
                    f"Ticket {t_id} category was altered without an override record"
                )
            if item["final_priority"] != pred_map[t_id]["priority"]:
                raise ValidationError(
                    f"Ticket {t_id} priority was altered without an override record"
                )


def check_optional_artifacts() -> None:
    """Validates escalations.json and llm_calls.jsonl if generated."""
    if ESCALATIONS_FILE.exists():
        escalations = load_json(ESCALATIONS_FILE)
        if not isinstance(escalations, list):
            raise ValidationError("escalations.json must contain a JSON array")
        print(f"  ✓ escalations.json valid ({len(escalations)} items)")

    if LLM_LOGS_FILE.exists():
        with open(LLM_LOGS_FILE, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                try:
                    entry = json.loads(line)
                    required_keys = {
                        "stage",
                        "timestamp",
                        "provider",
                        "model",
                        "prompt_hash",
                        "input_artifacts",
                        "output_artifact",
                    }
                    if not required_keys.issubset(entry.keys()):
                        raise ValidationError(
                            f"llm_calls.jsonl line {idx} missing required audit keys"
                        )
                except json.JSONDecodeError:
                    raise ValidationError(
                        f"llm_calls.jsonl contains invalid JSON on line {idx}"
                    )
        print("  ✓ llm_calls.jsonl valid and properly formatted")


def main() -> None:
    """Executes the validation test suite."""
    print("Running triage pipeline validation suite...\n")

    try:
        # 1. Artifacts existence
        check_required_artifacts()
        print("  ✓ All required artifacts exist on disk")

        # 2. File parsing
        raw_tickets = load_json(TICKETS_FILE)
        config = load_json(CONFIG_FILE)
        normalized_tickets = load_json(NORMALIZED_FILE)
        predictions = load_json(PREDICTIONS_FILE)
        overrides = load_json(OVERRIDES_FILE)
        final_queue = load_json(FINAL_QUEUE_FILE)
        print("  ✓ All artifacts contain valid JSON")

        # 3. Chronological stage order
        check_pipeline_chronology()
        print("  ✓ Deterministic normalization executed prior to predictions")

        # 4. Cardinality and IDs
        check_cardinality_and_ids(
            raw_tickets, normalized_tickets, predictions, final_queue
        )
        print(
            f"  ✓ Exactly one prediction and final entry per input ticket ({len(raw_tickets)} tickets)"
        )

        # 5. Config adherence (categories, priorities, routing, reply length)
        check_config_adherence(final_queue, config)
        print(
            "  ✓ Category, priority, routing rules, and word limits strictly conform to config"
        )

        # 6. Override consistency
        check_overrides_application(final_queue, overrides, predictions, config)
        print("  ✓ Human overrides correctly reflected in final queue outputs")

        # 7. Check optional stretch items
        check_optional_artifacts()

        print("\n🎉 ALL VALIDATION CHECKS PASSED SUCCESSFULLY.")
        sys.exit(0)

    except ValidationError as err:
        print(f"\n❌ VALIDATION FAILED: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
