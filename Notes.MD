Initial Review Notes:



pipeline - reading customer support tickets from local storage - categorizes each ticket into a specific ***labelled*** set - reads the ticket (detects if any text indicates urgency) - suggests a reply (possibly thru LLM) - endpoint API receiving the final LLM output allowing human reviewer to approve/reject/edit - human response is sent back to the agent for HITL (human-in-the-loop) learning

