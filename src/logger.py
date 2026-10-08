"""Stage 2: LLM predictions using Gemini with dynamic schemas and batch resilience."""

import json
import os
from pathlib import Path
from typing import Any, Literal, Type

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, create_model

from src.models import NormalizedTicket


def get_dynamic_schema(config: dict[str, Any]) -> Type[BaseModel]:
    """
    Dynamically generates a Pydantic model for the LLM output constraint.
    This injects the exact allowed categories and priorities from triage_config.json
    into the schema as strict Python Literals.
    """
    # Create tuple of allowed values for the Literal type
    allowed_categories = tuple(config["allowed_categories"])
    allowed_priorities = tuple(config["allowed_priorities"])

    # Dynamically construct the SinglePrediction model with Literals
    DynamicPrediction = create_model(
        "DynamicPrediction",
        ticket_id=(str, ...),
        category=(Literal[allowed_categories], ...),
        priority=(Literal[allowed_priorities], ...),
        confidence=(
            float,
            Field(ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0"),
        ),
        reason=(str, ...),
        suggested_reply=(str, ...),
        route_to=(str, ...),
    )

    # Wrap it in the BatchTriageResponse array structure
    class DynamicBatchResponse(BaseModel):
        tickets: list[DynamicPrediction]

    return DynamicBatchResponse


def build_prompt(tickets: list[NormalizedTicket], config: dict[str, Any]) -> str:
    """Constructs the exact instructions for the model."""
    tickets_json = json.dumps([t.model_dump() for t in tickets], indent=2)

    return f"""You are an expert customer support triage system.
Your task is to analyze the following batch of customer support tickets and classify them.

RULES:
1. You must process every single ticket provided in the JSON array below.
2. Category must be strictly chosen from the allowed categories list.
3. Priority must be strictly chosen from the allowed priorities list.
4. 'route_to' must exactly match the destination mapped to your chosen category in the routing rules.
5. 'suggested_reply' must follow this tone: {config["reply_style"]["tone"]}.
6. 'suggested_reply' MUST BE LESS THAN {config["reply_style"]["max_words"]} words.

TICKETS TO PROCESS:
{tickets_json}

ROUTING RULES FOR REFERENCE:
{json.dumps(config["routing_rules"], indent=2)}
"""


def call_llm_triage(
    normalized_tickets: list[NormalizedTicket],
    config: dict[str, Any],
    output_path: str | Path,
) -> list[dict[str, Any]]:
    """
    Executes the single batch LLM call and ensures perfect resilience.
    If a ticket is omitted by the model, a deterministic fallback is generated.
    """
    # 1. Load API Key
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in .env file.")

    client = genai.Client(api_key=api_key)
    DynamicSchema = get_dynamic_schema(config)
    prompt = build_prompt(normalized_tickets, config)

    # 2. Call Gemini with Structured Outputs
    llm_predictions = []
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DynamicSchema,
                temperature=0.1,  # Keep it highly deterministic
            ),
        )

        # Parse the structured JSON response back into Pydantic models
        parsed_response = DynamicSchema.model_validate_json(response.text)
        llm_predictions = parsed_response.tickets

    except Exception as e:
        # If the API crashes entirely (e.g. rate limit, 500 error),
        # we log the error and let the resilience loop handle it.
        print(f"⚠️ LLM API Error: {e}. Falling back to safe defaults for all tickets.")

    # 3. Implement Batch Resilience (Stretch Goal 7)
    prediction_map = {p.ticket_id: p.model_dump() for p in llm_predictions}
    final_predictions = []

    for ticket in normalized_tickets:
        if ticket.ticket_id in prediction_map:
            final_predictions.append(prediction_map[ticket.ticket_id])
        else:
            # Fallback for missing/malformed tickets
            print(
                f"⚠️ Warning: Ticket {ticket.ticket_id} missing from LLM output. Applying safety fallback."
            )
            final_predictions.append(
                {
                    "ticket_id": ticket.ticket_id,
                    "category": "other",
                    "priority": "normal",
                    "confidence": 0.0,
                    "reason": "Malformed or omitted by LLM batch response; routed for safety.",
                    "suggested_reply": "Thank you for reaching out. A support agent will review your inquiry shortly.",
                    "route_to": config["routing_rules"].get(
                        "other", "manual_review_queue"
                    ),
                }
            )

    # 4. Save to disk
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_predictions, f, indent=2, ensure_ascii=False)

    return final_predictions
