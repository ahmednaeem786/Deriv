
# Design Note: Deriv Triage Pipeline

## 1. Purpose

The Deriv Triage Pipeline reads customer support tickets from local storage, classifies each ticket into a configured category, detects urgency through priority assignment, suggests a response, and routes the result to the appropriate queue.

The pipeline combines deterministic processing, a single batch LLM call, and a human-in-the-loop review checkpoint. Final outputs are validated before they are made available to downstream consumers.

## 2. Design Goals

- Keep deterministic logic separate from LLM interactions.
- Enforce pipeline stages so work cannot run out of order.
- Validate inputs, model outputs, and generated artifacts with Pydantic.
- Support evaluator-provided configuration without code changes.
- Preserve a complete audit trail for LLM calls and human overrides.
- Produce deterministic fallbacks when an LLM response is incomplete or invalid.

## 3. Architecture

Organizing the codebase into modular, single-responsibility files makes state transitions verifiable and isolates deterministic logic from LLM interactions.

```text
deriv-triage-pipeline/
├── config/
│   └── triage_config.json         # Input config (may be replaced by evaluator)
├── data/
│   └── tickets.json               # Input tickets (may be replaced by evaluator)
├── src/
│   ├── __init__.py
│   ├── state.py                   # Stage enum and pipeline state controller
│   ├── models.py                  # Pydantic models for inputs, outputs, and LLM schemas
│   ├── normalizer.py              # Stage 1: deterministic text cleaning and formatting
│   ├── llm_client.py              # Stage 2: prompt construction, dynamic schema, and API call
│   ├── reviewer.py                # Stage 3: interactive CLI review checkpoint
│   ├── queue_generator.py         # Stage 4: overrides, routing, and Markdown summary
│   ├── escalation.py              # Stage 5: confidence and "other" rule evaluator
│   └── logger.py                  # Audit logger for llm_calls.jsonl
├── main.py                        # Pipeline runner executing stages sequentially
├── validate.py                    # Standalone evaluator for constraints and files
├── requirements.txt
└── README.md
```

## 4. Pipeline Stages

| Stage | Owner | Responsibility | Primary artifact |
| --- | --- | --- | --- |
| `INPUTS_LOADED` | `main.py` | Load and validate configuration and raw tickets | `tickets.json`, `triage_config.json` |
| `TICKETS_NORMALIZED` | `normalizer.py` | Clean and format ticket text deterministically | `normalized_tickets.json` |
| `TRIAGE_PREDICTED` | `llm_client.py` | Classify tickets in one batch and apply fallbacks | `triage_predictions.json` |
| `HUMAN_REVIEW_COMPLETE` | `reviewer.py` | Display predictions and collect overrides | `review_overrides.json` |
| `FINAL_QUEUE_GENERATED` | `queue_generator.py` | Apply overrides, calculate routing, and summarize results | `final_queue.json`, `queue_summary.md` |
| `RESULTS_FINALISED` | `main.py` | Complete the run after validation | Final pipeline state |

Escalation evaluation runs after prediction and writes `escalations.json`. Validation runs as a separate audit step and may use the `VALIDATION_COMPLETE` state before finalization.

## 5. Implementation Plan

### 5.1 `src/state.py`: Pipeline State Enforcement

The specification requires stages to be explicitly enforced in code. Implement a finite state tracker so downstream steps cannot run out of order.

#### Core components

- `PipelineStage(str, Enum)`: Defines `INIT`, `INPUTS_LOADED`, `TICKETS_NORMALIZED`, `TRIAGE_PREDICTED`, `HUMAN_REVIEW_COMPLETE`, `FINAL_QUEUE_GENERATED`, `VALIDATION_COMPLETE`, and `RESULTS_FINALISED`.
- `StateManager`: Maintains `current_stage` and exposes `transition_to(next_stage)`. The method raises `RuntimeError` for illegal transitions, such as generating the final queue before human review is complete.

### 5.2 `src/models.py`: Data Contracts and Dynamic Schemas

All inputs, outputs, and validation rules must be strictly typed with Pydantic.

#### Core models

- `RawTicket`: Validates raw records in `tickets.json`: `ticket_id`, `customer_id`, `subject`, `message`, `channel`, and `created_at`.
- `NormalizedTicket`: Validates `normalized_tickets.json`: `ticket_id`, `subject`, `message`, `channel`, `created_at`, `text_for_model`, and `char_count`.
- `TriageConfig`: Validates `triage_config.json`: `allowed_categories`, `allowed_priorities`, `reply_style`, and `routing_rules`.
- `SinglePrediction`: Represents one prediction: `ticket_id`, `category`, `priority`, `confidence`, `reason`, `suggested_reply`, and `route_to`.
- `BatchTriageResponse`: Contains `tickets: list[SinglePrediction]`.
- `ReviewOverride`: Validates `review_overrides.json`: `ticket_id`, `old_category`, `new_category`, `old_priority`, and `new_priority`.
- `FinalQueueItem`: Validates `final_queue.json`: `ticket_id`, `final_category`, `final_priority`, `final_route_to`, `suggested_reply`, and `was_overridden`.

### 5.3 `src/normalizer.py`: Deterministic Normalization

This stage performs 100% deterministic preprocessing before any LLM SDK is loaded.

```python
def normalize_ticket(raw: dict) -> dict:
    text_for_model = (
        f"Subject: {raw['subject'].strip()}\n"
        f"Message: {raw['message'].strip()}"
    )
    return {
        "ticket_id": raw["ticket_id"],
        "subject": raw["subject"],
        "message": raw["message"],
        "channel": raw["channel"],
        "created_at": raw["created_at"],
        "text_for_model": text_for_model,
        "char_count": len(text_for_model),
    }
```

`run_normalization(input_path: Path, output_path: Path) -> list[NormalizedTicket]` reads `tickets.json`, processes every ticket, writes `normalized_tickets.json`, and returns the validated list. `customer_id` is intentionally omitted from the normalized artifact.

### 5.4 `src/llm_client.py`: Batch Triage and Resilience

This module constructs one batch call, injects allowed values from `triage_config.json`, and handles malformed or incomplete responses.

#### Dynamic schema generation

Read `triage_config.json` at runtime and dynamically create a `SinglePrediction` Pydantic model. The `category` and `priority` fields should use `typing.Literal`, constrained by `allowed_categories` and `allowed_priorities`. This preserves runtime flexibility if the evaluator substitutes new categories.

#### Prompt construction

The prompt must instruct the model to:

- Analyze all tickets in a single pass.
- Respect `reply_style.max_words` (for example, no more than 80 words).
- Use `reply_style.tone`.

#### Resilience and fallback handling

Iterate through the parsed ticket list. If an input ticket ID is missing from the LLM response or fails schema validation, generate this deterministic fallback object:

```python
{
    "ticket_id": missing_id,
    "category": "other",
    "priority": "normal",
    "confidence": 0.0,
    "reason": "Malformed or omitted by LLM batch response; routed for safety.",
    "suggested_reply": "Thank you for reaching out. A support agent will review your inquiry shortly.",
    "route_to": config["routing_rules"].get("other", "manual_review_queue"),
}
```

Write the resulting array to `triage_predictions.json`.

### 5.5 `src/logger.py`: LLM Audit Logging

This is a stretch goal that provides auditability for the LLM invocation.

```python
def log_llm_call(
    stage: str,
    provider: str,
    model: str,
    prompt_text: str,
    input_paths: list[str],
    output_path: str,
):
    ...
```

Compute `prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()` and append a JSON line to `llm_calls.jsonl` containing the ISO-8601 timestamp, call metadata, and artifact references.

### 5.6 `src/reviewer.py`: Human Review Checkpoint

This stage implements the terminal-based interactive checkpoint.

- `display_predictions(predictions: list[dict])` prints a formatted table showing `ticket_id`, `category`, and `priority`.
- `run_review_checkpoint(predictions: list[dict], config: dict) -> tuple[list[dict], list[dict]]` collects and validates overrides.

Display the following prompt:

```text
Enter any overrides as: ticket_id,category,priority
Press Enter on an empty line when done.
```

For each non-empty line:

1. Parse the CSV input with `line.strip().split(',')`.
2. Validate that the ticket ID exists.
3. Validate that the new category belongs to `allowed_categories`.
4. Validate that the new priority belongs to `allowed_priorities`.
5. Create an entry for `review_overrides.json` containing both old and new values.

Always write `review_overrides.json`; write an empty array (`[]`) when no overrides are entered.

### 5.7 `src/queue_generator.py`: Final Queue Generation

This stage applies overrides, recalculates routing, and generates summaries.

`build_final_queue(predictions: list[dict], overrides: list[dict], config: dict) -> list[dict]` creates a `ticket_id -> override` mapping and processes each prediction:

- If overridden, replace the category and priority, set `was_overridden = True`, and set `final_route_to = config["routing_rules"][final_category]`.
- If not overridden, copy the prediction values, set `was_overridden = False`, and set `final_route_to = config["routing_rules"][pred["category"]]`.

Write the result to `final_queue.json`.

`generate_markdown_summary(final_queue: list[dict], overrides: list[dict], output_path: Path)` aggregates the overall count, counts by category, counts by priority, and counts by destination queue. It writes tables and an **Overridden Tickets** section listing modified IDs, old values, and new values to `queue_summary.md`.

### 5.8 `src/escalation.py`: Deterministic Escalation Rules

`evaluate_escalations(predictions: list[dict], output_path: Path)` loops through each prediction and flags a ticket when:

```text
category == "other" OR float(confidence) < 0.60
```

Write flagged items containing `ticket_id`, `category`, `confidence`, and `reason` to `escalations.json`.

### 5.9 `main.py`: Orchestration Entrypoint

Coordinate the full lifecycle with `StateManager`:

```python
state.transition_to(PipelineStage.INPUTS_LOADED)
config, raw_tickets = load_inputs()

state.transition_to(PipelineStage.TICKETS_NORMALIZED)
normalized = run_normalization(raw_tickets)

state.transition_to(PipelineStage.TRIAGE_PREDICTED)
predictions = call_llm_triage(normalized, config)
evaluate_escalations(predictions)

state.transition_to(PipelineStage.HUMAN_REVIEW_COMPLETE)
overrides = run_review_checkpoint(predictions, config)

state.transition_to(PipelineStage.FINAL_QUEUE_GENERATED)
final_queue = build_final_queue(predictions, overrides, config)
generate_markdown_summary(final_queue, overrides)

state.transition_to(PipelineStage.RESULTS_FINALISED)
print("Pipeline run successfully completed.")
```

### 5.10 `validate.py`: Automated Audit Script

Implement a self-contained script that verifies pipeline integrity.

#### Checks

- **Artifact existence:** Confirm `tickets.json`, `triage_config.json`, `normalized_tickets.json`, `triage_predictions.json`, `review_overrides.json`, `final_queue.json`, and `queue_summary.md` exist.
- **One-to-one cardinality:** Verify `len(raw_tickets) == len(normalized_tickets) == len(predictions) == len(final_queue)`.
- **Configuration adherence:** Confirm every category and priority in `final_queue.json` belongs to the configured allowed sets.
- **Routing correctness:** Verify `final_route_to == config["routing_rules"][final_category]` for every ticket.
- **Override integrity:** Confirm every ticket in `review_overrides.json` has `was_overridden == True` and matching updated values in `final_queue.json`.
- **Length guard:** Verify `len(item["suggested_reply"].split()) <= config["reply_style"]["max_words"]` for every output item.

## 6. Output Artifacts

| Artifact | Description |
| --- | --- |
| `normalized_tickets.json` | Deterministically normalized tickets ready for triage |
| `triage_predictions.json` | Batch LLM predictions, including deterministic fallbacks |
| `review_overrides.json` | Human reviewer changes, including old and new values |
| `escalations.json` | Tickets requiring escalation due to category or confidence |
| `final_queue.json` | Final routed tickets after human review |
| `queue_summary.md` | Markdown summary of queue totals and overrides |
| `llm_calls.jsonl` | Optional audit log of LLM calls |

## 7. Acceptance Criteria

- Pipeline stages reject illegal transitions.
- Every input ticket produces exactly one normalized ticket, prediction, and final queue item.
- Categories, priorities, and routes follow the active configuration.
- Missing or malformed LLM predictions receive a deterministic safety fallback.
- Human overrides are applied and represented accurately in the final queue.
- Suggested replies respect the configured word limit.
- `validate.py` passes against the generated artifacts.
