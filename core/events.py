# Event type constants for the Ratchet event-sourced domain model.

# Project events
PROJECT_CREATED = "project.created"
PROJECT_ARCHIVED = "project.archived"

# Task events
TASK_CREATED = "task.created"
TASK_STATUS_CHANGED = "task.status_changed"
TASK_SPEC_ASSIGNED = "task.spec_assigned"
TASK_DEPENDENCY_ADDED = "task.dependency_added"
TASK_TITLE_UPDATED = "task.title_updated"
TASK_TITLE_CHANGED = "task.title_changed"
TASK_BASELINE_QA_FAILED = "task.baseline_qa_failed"
TASK_BASELINE_QA_RETRY = "task.baseline_qa_retry"
TASK_FORCE_EXECUTE = "task.force_execute"

# Spec events
SPEC_CREATED = "spec.created"

# Execution events
EXECUTION_STARTED = "execution.started"
EXECUTION_COMPLETED = "execution.completed"
EXECUTION_FAILED = "execution.failed"

# Task status constants
READY_FOR_SPEC = "ready_for_spec"
SPEC_QA = "spec_qa"
READY_FOR_IMPLEMENTATION = "ready_for_implementation"
IN_PROGRESS = "in_progress"
BLOCKED = "blocked"
READY_FOR_QA = "ready_for_qa"
READY_FOR_DEPLOYMENT = "ready_for_deployment"
DEPLOYED = "deployed"

ABANDONED = "abandoned"

# Feature events
FEATURE_CREATED = "feature.created"
HIGH_LEVEL_SPEC_ADDED = "high_level_spec.added"
HIGH_LEVEL_SPEC_COMPILED = "high_level_spec.compiled"

# Feature status constants (derived, not stored)
FEATURE_DRAFT = "draft"
FEATURE_GENERATED = "generated"
FEATURE_IN_PROGRESS = "in_progress"
FEATURE_DONE = "done"

TASK_STATUSES = (
    READY_FOR_SPEC,
    SPEC_QA,
    READY_FOR_IMPLEMENTATION,
    IN_PROGRESS,
    BLOCKED,
    READY_FOR_QA,
    READY_FOR_DEPLOYMENT,
    DEPLOYED,
    ABANDONED,
)
