"""Pipeline orchestration script enforcing sequential state transitions."""

import json
from pathlib import Path
from typing import Any

from src.escalation import evaluate_escalations
from src.llm_client import call_llm_triage
from src.logger import log_llm_call
from src.models import TriageConfig
from src.normalizer import run_normalization
from src.queue_generator import build_final_queue, generate_markdown_summary
from src.reviewer import run_review_checkpoint
from src.state import PipelineStage, StateManager

# Default file artifact paths
TICKETS_FILE = Path("tickets.json")
CONFIG_FILE = Path("triage_config.json")
NORMALIZED_FILE = Path("normalized_tickets.json")
PREDICTIONS_FILE = Path("triage_predictions.json")
OVERRIDES_FILE = Path("review_overrides.json")
FINAL_QUEUE_FILE = Path("final_queue.json")
SUMMARY_FILE = Path("queue_summary.md")
ESCALATIONS_FILE = Path("escalations.json")
LLM_LOG_FILE = Path("llm_calls.jsonl")


def load_inputs(tickets_path: Path, config_path: Path) -> tuple[dict[str, Any], list]:
    """Reads and validates the raw input files from disk."""
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file missing: {config_path.resolve()}")
    if not tickets_path.exists():
        raise FileNotFoundError(f"Tickets file missing: {tickets_path.resolve()}")

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    # Validate configuration against Pydantic schema
    TriageConfig.model_validate(config_data)

    with open(tickets_path, "r", encoding="utf-8") as f:
        tickets_data = json.load(f)

    return config_data, tickets_data


def verify_pipeline_outputs(config: dict[str, Any]) -> None:
    """Performs inline validation to satisfy VALIDATION_COMPLETE before finalising."""
    required_files = [
        NORMALIZED_FILE,
        PREDICTIONS_FILE,
        OVERRIDES_FILE,
        FINAL_QUEUE_FILE,
        SUMMARY_FILE,
        ESCALATIONS_FILE,
    ]
    for file_path in required_files:
        if not file_path.exists():
            raise FileNotFoundError(
                f"Validation failure: Expected artifact {file_path} does not exist."
            )

    with open(FINAL_QUEUE_FILE, "r", encoding="utf-8") as f:
        final_queue = json.load(f)

    allowed_cats = set(config["allowed_categories"])
    allowed_prios = set(config["allowed_priorities"])
    routing_rules = config["routing_rules"]

    for item in final_queue:
        if item["final_category"] not in allowed_cats:
            raise ValueError(
                f"Invalid category in final queue: {item['final_category']}"
            )
        if item["final_priority"] not in allowed_prios:
            raise ValueError(
                f"Invalid priority in final queue: {item['final_priority']}"
            )
        expected_route = routing_rules.get(
            item["final_category"], "manual_review_queue"
        )
        if item["final_route_to"] != expected_route:
            raise ValueError(
                f"Routing mismatch for ticket {item['ticket_id']}: "
                f"got {item['final_route_to']}, expected {expected_route}"
            )


def main() -> None:
    """Executes the full triage pipeline across all mandatory lifecycle stages."""
    state = StateManager()

    # Stage 1: INPUTS_LOADED
    state.transition_to(PipelineStage.INPUTS_LOADED)
    print(f"[{state.current_stage.value}] Reading {TICKETS_FILE} and {CONFIG_FILE}...")
    config, _ = load_inputs(TICKETS_FILE, CONFIG_FILE)

    # Stage 2: TICKETS_NORMALIZED
    state.transition_to(PipelineStage.TICKETS_NORMALIZED)
    print(f"[{state.current_stage.value}] Normalizing raw tickets deterministically...")
    normalized_tickets = run_normalization(TICKETS_FILE, NORMALIZED_FILE)
    print(
        f"  ✓ Saved {len(normalized_tickets)} normalized ticket(s) to {NORMALIZED_FILE}"
    )

    # Stage 3: TRIAGE_PREDICTED
    state.transition_to(PipelineStage.TRIAGE_PREDICTED)
    print(f"[{state.current_stage.value}] Executing batch LLM triage prediction...")
    predictions = call_llm_triage(normalized_tickets, config, PREDICTIONS_FILE)
    print(f"  ✓ Saved predictions to {PREDICTIONS_FILE}")

    # Audit logging for the LLM invocation
    prompt_record = json.dumps(
        [t.model_dump() for t in normalized_tickets], ensure_ascii=False
    )
    log_llm_call(
        stage=state.current_stage.value,
        provider="google",
        model="gemini-2.5-flash",
        prompt_text=prompt_record,
        input_artifacts=[str(NORMALIZED_FILE), str(CONFIG_FILE)],
        output_artifact=str(PREDICTIONS_FILE),
        log_file_path=LLM_LOG_FILE,
    )

    # Deterministic escalation detection
    escalations = evaluate_escalations(predictions, ESCALATIONS_FILE)
    print(
        f"  ✓ Evaluated escalations ({len(escalations)} flagged) -> {ESCALATIONS_FILE}"
    )

    # Stage 4: HUMAN_REVIEW_COMPLETE
    state.transition_to(PipelineStage.HUMAN_REVIEW_COMPLETE)
    print(f"[{state.current_stage.value}] Initiating terminal review checkpoint...")
    overrides = run_review_checkpoint(predictions, config, OVERRIDES_FILE)

    # Stage 5: FINAL_QUEUE_GENERATED
    state.transition_to(PipelineStage.FINAL_QUEUE_GENERATED)
    print(
        f"[{state.current_stage.value}] Applying overrides and recalculating routing..."
    )
    final_queue = build_final_queue(predictions, overrides, config, FINAL_QUEUE_FILE)
    generate_markdown_summary(final_queue, overrides, SUMMARY_FILE)
    print(f"  ✓ Generated {FINAL_QUEUE_FILE} and {SUMMARY_FILE}")

    # Stage 6: VALIDATION_COMPLETE
    state.transition_to(PipelineStage.VALIDATION_COMPLETE)
    print(f"[{state.current_stage.value}] Running inline artifact integrity checks...")
    verify_pipeline_outputs(config)
    print("  ✓ All pipeline assertions verified.")

    # Stage 7: RESULTS_FINALISED
    state.transition_to(PipelineStage.RESULTS_FINALISED)
    print(f"[{state.current_stage.value}] Pipeline execution completed successfully.\n")


if __name__ == "__main__":
    main()
