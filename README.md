# Deriv Support Triage Pipeline

A replayable support-triage pipeline that reads customer support tickets from disk, normalizes them deterministically, classifies them with one batch LLM call, pauses for human review, applies corrections, and generates a final agent queue with validation artifacts.

The pipeline is designed for evaluator-replaced inputs: it does not depend on the sample ticket IDs or wording. Categories, priorities, routing rules, tone, and reply length are read from the active configuration at runtime.

## Features

- Deterministic ticket normalization before LLM processing.
- One batch Gemini call for all normalized tickets.
- Runtime-constrained category and priority values.
- Config-derived routing and safety fallbacks.
- Suggested replies checked against the configured word limit.
- Interactive human-in-the-loop category and priority overrides.
- Deterministic escalation rules based on category and confidence.
- Sequential state enforcement for every pipeline stage.
- JSON, Markdown, and JSONL artifacts for inspection and replay.
- Standalone validation with `python validate.py`.

## Requirements

- Python 3.10 or newer.
- A Google Gemini API key.
- Internet access for the Gemini API call.

The project uses:

- `google-genai` for the Gemini API.
- `pydantic` for input, configuration, and output validation.
- `python-dotenv` for loading `GEMINI_API_KEY` from `.env`.

## Project Layout

```text
deriv-triage-pipeline/
├── config/
│   └── triage_config.json       # Local configuration fixture
├── data/
│   └── tickets.json             # Local ticket fixture
├── src/
│   ├── escalation.py            # Deterministic escalation rules
│   ├── llm_client.py            # Gemini call, dynamic schema, and fallbacks
│   ├── logger.py                # LLM audit logging
│   ├── models.py                # Pydantic data contracts
│   ├── normalizer.py            # Deterministic ticket normalization
│   ├── paths.py                 # Project-root path resolution
│   ├── queue_generator.py       # Final queue and Markdown summary
│   ├── reviewer.py              # Interactive review checkpoint
│   └── state.py                 # Pipeline stage enforcement
├── main.py                      # Pipeline entrypoint
├── validate.py                  # Artifact and business-rule validator
├── requirements.txt              # Python dependencies
├── Design.MD                    # Detailed implementation design note
└── Notes.MD                     # Initial product notes
```

Generated artifacts are written to the repository root:

```text
normalized_tickets.json
triage_predictions.json
review_overrides.json
final_queue.json
queue_summary.md
escalations.json
llm_calls.jsonl
```

## Installation

Create and activate a virtual environment from the project directory.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks script activation for the current process, use:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configuration

Create a local `.env` file in the project directory:

```dotenv
GEMINI_API_KEY=replace-with-your-key
```

Never commit `.env` or expose the key in documentation, logs, screenshots, or source control. The repository's `.gitignore` excludes `.env`.

The default configuration is stored in `config/triage_config.json`:

```json
{
  "allowed_categories": [
    "billing_issue",
    "account_access",
    "product_how_to",
    "bug_report",
    "other"
  ],
  "allowed_priorities": [
    "urgent",
    "high",
    "normal",
    "low"
  ],
  "reply_style": {
    "tone": "clear, polite, concise",
    "max_words": 80
  },
  "routing_rules": {
    "billing_issue": "payments_queue",
    "account_access": "trust_and_access_queue",
    "product_how_to": "general_support_queue",
    "bug_report": "technical_queue",
    "other": "manual_review_queue"
  }
}
```

### Configuration rules

- Every category returned by the model must be in `allowed_categories`.
- Every priority returned by the model must be in `allowed_priorities`.
- Every final route is derived from `routing_rules` using the final category.
- `reply_style.max_words` limits generated and fallback replies.
- At least one allowed category must have a routing rule so a safe fallback can be constructed.

## Input Files

The required logical input files are:

- `tickets.json`
- `triage_config.json`

For compatibility with the current repository layout, the path resolver uses this order:

1. Root-level `tickets.json` and `triage_config.json`, when present.
2. `data/tickets.json` and `config/triage_config.json`, respectively.

This allows an evaluator to replace the required root-level files while preserving the checked-in local fixtures.

### Ticket schema

Each ticket must contain:

```json
{
  "ticket_id": "T-1001",
  "customer_id": "C-001",
  "subject": "Charged twice for my deposit",
  "message": "Please help reverse the extra charge.",
  "channel": "email",
  "created_at": "2026-05-10T09:15:00Z"
}
```

`tickets.json` must contain a JSON array. Ticket IDs should be unique because they identify records throughout the pipeline.

## Running the Pipeline

From the project directory, with the virtual environment activated:

```powershell
python main.py
```

The pipeline pauses at the human review checkpoint. It displays each ticket's predicted category and priority, then prompts:

```text
Enter any overrides as: ticket_id,category,priority
Press Enter on an empty line when done.
```

Example override:

```text
T-1002,account_access,urgent
```

Finish review by pressing Enter on an empty line. The pipeline writes an empty `review_overrides.json` array when no changes are entered.

The scripts use project-root paths, so they can also be invoked with an absolute path from another working directory:

```powershell
python "G:\Resumes & Cover Letters\Deriv\main.py"
```

## Pipeline Flow

The state machine enforces this order:

```text
INIT
  -> INPUTS_LOADED
  -> TICKETS_NORMALIZED
  -> TRIAGE_PREDICTED
  -> HUMAN_REVIEW_COMPLETE
  -> FINAL_QUEUE_GENERATED
  -> VALIDATION_COMPLETE
  -> RESULTS_FINALISED
```

### 1. Inputs loaded

`main.py` reads and validates the active ticket and configuration files. Configuration is validated with the Pydantic `TriageConfig` model.

### 2. Tickets normalized

`src/normalizer.py` processes every raw ticket without loading or calling the LLM. It creates:

```json
{
  "ticket_id": "T-1001",
  "subject": "Charged twice for my deposit",
  "message": "Please help reverse the extra charge.",
  "channel": "email",
  "created_at": "2026-05-10T09:15:00Z",
  "text_for_model": "Subject: Charged twice for my deposit\\nMessage: Please help reverse the extra charge.",
  "char_count": 84
}
```

`customer_id` is intentionally excluded from the normalized artifact. `text_for_model` is built deterministically from trimmed subject and message values, and `char_count` is calculated in Python.

### 3. Batch triage prediction

`src/llm_client.py` builds one prompt containing all normalized tickets and the complete routing configuration. The Gemini response uses a dynamically generated Pydantic schema whose category and priority fields are constrained by the active configuration.

Each prediction contains:

```json
{
  "ticket_id": "T-1001",
  "category": "billing_issue",
  "priority": "high",
  "confidence": 0.94,
  "reason": "The ticket describes a duplicate payment.",
  "suggested_reply": "We are reviewing the duplicate charge and will help resolve it.",
  "route_to": "payments_queue"
}
```

Before predictions are written, the pipeline checks that:

- The category is allowed.
- The priority is allowed.
- `route_to` matches the configured route for the category.
- The suggested reply does not exceed `reply_style.max_words`.

Missing or invalid records receive a deterministic fallback built from the active configuration.

### 4. Escalation evaluation

`src/escalation.py` flags a prediction when either condition is true:

```text
category == "other" OR confidence < 0.60
```

The result is saved to `escalations.json`.

### 5. Human review

`src/reviewer.py` displays predictions and accepts category and priority corrections. Each accepted correction records the previous and new values in `review_overrides.json`.

Invalid ticket IDs, categories, priorities, and malformed input lines are rejected without stopping the review loop.

### 6. Final queue generation

`src/queue_generator.py` applies overrides and recalculates routing from the final category. It does not trust the original model route when constructing the final queue.

Each final item contains:

```json
{
  "ticket_id": "T-1001",
  "final_category": "billing_issue",
  "final_priority": "high",
  "final_route_to": "payments_queue",
  "suggested_reply": "We are reviewing the duplicate charge and will help resolve it.",
  "was_overridden": false
}
```

### 7. Validation and finalization

The entrypoint performs inline artifact checks before transitioning to `RESULTS_FINALISED`. The standalone validator performs broader checks and should be run after a pipeline execution.

## Output Artifacts

| File | Purpose |
| --- | --- |
| `normalized_tickets.json` | Deterministically normalized input tickets |
| `triage_predictions.json` | One prediction per ticket after model-output checks and fallbacks |
| `review_overrides.json` | Human corrections, including old and new values |
| `final_queue.json` | Final category, priority, route, reply, and override status |
| `queue_summary.md` | Ticket totals, category counts, priority counts, destinations, and overrides |
| `escalations.json` | Predictions flagged by confidence or `other` category |
| `llm_calls.jsonl` | Audit records for LLM calls |

Generated artifacts can be deleted and regenerated from the input files. The LLM output itself may vary between runs, but deterministic normalization, routing, fallback behavior, review application, and validation rules are reproducible.

## Validation

Run the standalone validator:

```powershell
python validate.py
```

The validator checks:

- Required input and output artifacts exist.
- JSON artifacts can be parsed.
- Normalization output is not newer than prediction output.
- Input, normalized, prediction, and final queue ticket IDs have one-to-one cardinality.
- Ticket IDs are not duplicated in the input.
- Final categories and priorities belong to the active configuration.
- Final routes match configured routing rules.
- Final suggested replies respect the configured word limit.
- Human overrides are valid and reflected in final outputs.
- Optional escalation and LLM log artifacts have valid structure.

The validator is anchored to the repository path and can be run from another working directory:

```powershell
Push-Location G:\
python "G:\Resumes & Cover Letters\Deriv\validate.py"
Pop-Location
```

## Troubleshooting

### `Configuration file missing` or `Tickets file missing`

Confirm that either the root-level files exist:

```text
tickets.json
triage_config.json
```

or the repository fixtures exist:

```text
data/tickets.json
config/triage_config.json
```

### `GEMINI_API_KEY not found`

Confirm that `.env` is in the project directory and contains:

```dotenv
GEMINI_API_KEY=your-key
```

Make sure the virtual environment is active and dependencies are installed.

### `ImportError: cannot import name 'genai' from 'google'`

Install the dependencies into the same Python environment used to run the pipeline:

```powershell
python -m pip install -r requirements.txt
```

Then verify the interpreter and package:

```powershell
python -c "from google import genai; print('google-genai is available')"
```

### The pipeline stops at the review prompt

This is expected. The review checkpoint is intentionally interactive. Enter zero or more overrides, then press Enter on an empty line to continue.

### A prediction receives a safety fallback

A fallback is used when the model omits a ticket, returns an invalid route, or exceeds the configured reply limit. Check the terminal warning and inspect `triage_predictions.json` and `escalations.json`.

## Security and Data Handling

- Keep API keys in `.env`, which is ignored by Git.
- Do not commit customer data or generated artifacts containing sensitive ticket content.
- Review `llm_calls.jsonl` before sharing logs because artifact paths and execution metadata are recorded.
- Use evaluator or test fixtures instead of production customer records during development.

## Development Notes

The implementation intentionally keeps deterministic work outside the LLM client:

- Normalization is performed before the Gemini call.
- Routing is calculated from configuration in Python.
- Escalation decisions are deterministic.
- Human overrides are applied after prediction and before final queue generation.
- Validation is performed from files on disk rather than only in-memory objects.

For design rationale and the complete implementation plan, see [Design.MD](Design.MD).
