"""Stage 4: Final queue generation, override application, and summary reporting."""

import json
from collections import Counter
from pathlib import Path
from typing import Any, Union

from src.models import FinalQueueItem


def build_final_queue(
    predictions: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
    config: dict[str, Any],
    output_path: Union[str, Path] = "final_queue.json",
) -> list[dict[str, Any]]:
    """
    Applies human overrides to LLM predictions, recalculates routing,
    and writes the final queue artifact.
    """
    # 1. Map overrides by ticket_id for O(1) lookup
    overrides_map = {ov["ticket_id"]: ov for ov in overrides}
    routing_rules = config.get("routing_rules", {})

    final_queue: list[dict[str, Any]] = []

    # 2. Process each ticket
    for pred in predictions:
        t_id = pred["ticket_id"]

        # Check if the human reviewer modified this ticket
        if t_id in overrides_map:
            override = overrides_map[t_id]
            final_category = override["new_category"]
            final_priority = override["new_priority"]
            was_overridden = True
        else:
            final_category = pred["category"]
            final_priority = pred["priority"]
            was_overridden = False

        # 3. Deterministically compute routing based on the final category
        # Fallback to 'manual_review_queue' if a category isn't in routing rules
        final_route_to = routing_rules.get(final_category, "manual_review_queue")

        # 4. Construct strictly typed output item
        queue_item = FinalQueueItem(
            ticket_id=t_id,
            final_category=final_category,
            final_priority=final_priority,
            final_route_to=final_route_to,
            suggested_reply=pred["suggested_reply"],
            was_overridden=was_overridden,
        )
        final_queue.append(queue_item.model_dump())

    # 5. Save to disk
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(final_queue, f, indent=2, ensure_ascii=False)

    return final_queue


def generate_markdown_summary(
    final_queue: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
    output_path: Union[str, Path] = "queue_summary.md",
) -> None:
    """
    Generates a Markdown report aggregating the final queue statistics.
    """
    # Aggregate counts
    total_tickets = len(final_queue)
    categories = Counter(item["final_category"] for item in final_queue)
    priorities = Counter(item["final_priority"] for item in final_queue)
    destinations = Counter(item["final_route_to"] for item in final_queue)

    # Format Markdown
    md = []
    md.append("# Support Triage Queue Summary\n")
    md.append(f"**Total Tickets Processed:** {total_tickets}\n")

    md.append("## Breakdown by Category")
    for cat, count in categories.most_common():
        md.append(f"- **{cat}**: {count}")
    md.append("")

    md.append("## Breakdown by Priority")
    for prio, count in priorities.most_common():
        md.append(f"- **{prio}**: {count}")
    md.append("")

    md.append("## Queue Destinations")
    for dest, count in destinations.most_common():
        md.append(f"- **{dest}**: {count}")
    md.append("")

    md.append("## Human Review Overrides")
    if not overrides:
        md.append("*No tickets were overridden during the human review checkpoint.*")
    else:
        md.append(f"*{len(overrides)} ticket(s) were modified by human review:*\n")
        md.append("| Ticket ID | Category Change | Priority Change |")
        md.append("|-----------|-----------------|-----------------|")
        for ov in overrides:
            cat_change = f"{ov['old_category']} ➔ {ov['new_category']}"
            prio_change = f"{ov['old_priority']} ➔ {ov['new_priority']}"
            md.append(f"| {ov['ticket_id']} | {cat_change} | {prio_change} |")

    # Save to disk
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
