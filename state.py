"""Pipeline state tracking and stage enforcement."""

from enum import Enum
from typing import Dict, List, Optional


class PipelineStage(str, Enum):
    INIT = "INIT"
    INPUTS_LOADED = "INPUTS_LOADED"
    TICKETS_NORMALIZED = "TICKETS_NORMALIZED"
    TRIAGE_PREDICTED = "TRIAGE_PREDICTED"
    HUMAN_REVIEW_COMPLETE = "HUMAN_REVIEW_COMPLETE"
    FINAL_QUEUE_GENERATED = "FINAL_QUEUE_GENERATED"
    VALIDATION_COMPLETE = "VALIDATION_COMPLETE"
    RESULTS_FINALISED = "RESULTS_FINALISED"


class InvalidStateTransitionError(RuntimeError):
    """Raised when an illegal or out-of-order stage transition is attempted."""


class StateManager:
    """Enforces strict forward-only progression across pipeline stages."""

    # Explicit stage ordering to ensure downstream stages cannot execute out of order
    STAGE_ORDER: List[PipelineStage] = [
        PipelineStage.INIT,
        PipelineStage.INPUTS_LOADED,
        PipelineStage.TICKETS_NORMALIZED,
        PipelineStage.TRIAGE_PREDICTED,
        PipelineStage.HUMAN_REVIEW_COMPLETE,
        PipelineStage.FINAL_QUEUE_GENERATED,
        PipelineStage.VALIDATION_COMPLETE,
        PipelineStage.RESULTS_FINALISED,
    ]

    ALLOWED_TRANSITIONS: Dict[PipelineStage, List[PipelineStage]] = {
        PipelineStage.INIT: [PipelineStage.INPUTS_LOADED],
        PipelineStage.INPUTS_LOADED: [PipelineStage.TICKETS_NORMALIZED],
        PipelineStage.TICKETS_NORMALIZED: [PipelineStage.TRIAGE_PREDICTED],
        PipelineStage.TRIAGE_PREDICTED: [PipelineStage.HUMAN_REVIEW_COMPLETE],
        PipelineStage.HUMAN_REVIEW_COMPLETE: [PipelineStage.FINAL_QUEUE_GENERATED],
        PipelineStage.FINAL_QUEUE_GENERATED: [PipelineStage.VALIDATION_COMPLETE],
        PipelineStage.VALIDATION_COMPLETE: [PipelineStage.RESULTS_FINALISED],
        PipelineStage.RESULTS_FINALISED: [],
    }

    def __init__(self, initial_stage: PipelineStage = PipelineStage.INIT):
        self._current_stage: PipelineStage = initial_stage
        self._completed_stages: set[PipelineStage] = set()

    @property
    def current_stage(self) -> PipelineStage:
        return self._current_stage

    def transition_to(self, next_stage: PipelineStage) -> None:
        """Transitions to the target stage, raising an error if the transition is illegal."""
        allowed = self.ALLOWED_TRANSITIONS.get(self._current_stage, [])
        if next_stage not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid transition: Cannot advance from {self._current_stage.value} "
                f"to {next_stage.value}. Expected next stage: {[s.value for s in allowed]}"
            )

        self._completed_stages.add(self._current_stage)
        self._current_stage = next_stage

    def is_completed(self, stage: PipelineStage) -> bool:
        """Checks if a given stage has already executed and completed."""
        return stage in self._completed_stages

    def require_completed(self, stage: PipelineStage) -> None:
        """Guards critical operations by checking that prerequisite stages have run."""
        if not self.is_completed(stage):
            raise InvalidStateTransitionError(
                f"Prerequisite failure: Stage {stage.value} must be completed before "
                f"proceeding from {self._current_stage.value}."
            )
