# Event type constants for the Ratchet event-sourced domain model.

# Project events
PROJECT_CREATED = "project.created"
PROJECT_UPDATED = "project.updated"
PROJECT_ARCHIVED = "project.archived"
PROJECT_CONFIG_UPDATED = "project.config_updated"

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
TASK_DEPLOY_HOOKS_RUN = "task.merge_hooks_run"
TASK_WORKER_DISCONNECTED = "task.worker_disconnected"
TASK_PR_CREATED = "task.pr_created"
# TASK_PR_CREATED payload: {pr_url: str, pr_number: int, branch: str}

# Spec events
SPEC_CREATED = "spec.created"

# Execution events
EXECUTION_STARTED = "execution.started"
EXECUTION_COMPLETED = "execution.completed"
EXECUTION_FAILED = "execution.failed"
EXECUTION_TRACE_RECORDED = "execution.trace_recorded"

# Q&A events
TASK_INPUT_REQUESTED = "task_input_requested"
TASK_INPUT_PROVIDED = "task_input_provided"
# TASK_INPUT_REQUESTED payload: {"question": str, "execution_id": str, "question_index": int}
# TASK_INPUT_PROVIDED payload:  {"answer": str, "question_index": int, "answered_by": str}

# Task status constants
READY_FOR_SPEC = "ready_for_spec"
SPEC_QA = "spec_qa"
READY_FOR_IMPLEMENTATION = "ready_for_implementation"
IN_PROGRESS = "in_progress"
BLOCKED = "blocked"
READY_FOR_QA = "ready_for_qa"
READY_FOR_DEPLOYMENT = "ready_for_merge"
DEPLOYED = "merged"

ABANDONED = "abandoned"
WAITING_FOR_INPUT = "waiting_for_input"

# Feature events
FEATURE_CREATED = "feature.created"
FEATURE_UPDATED = "feature.updated"
HIGH_LEVEL_SPEC_ADDED = "high_level_spec.added"
HIGH_LEVEL_SPEC_COMPILED = "high_level_spec.compiled"

# Chat session events
CHAT_SESSION_CREATED = "chat_session.created"
CHAT_SESSION_MESSAGE_ADDED = "chat_session.message_added"

# Review events
REVIEW_RUN_STARTED = "review_run.started"
REVIEW_RUN_COMPLETED = "review_run.completed"
REVIEW_SUGGESTION_CREATED = "review_suggestion.created"
REVIEW_SUGGESTION_APPLIED = "review_suggestion.applied"
REVIEW_SUGGESTION_DISMISSED = "review_suggestion.dismissed"
REVIEW_SUGGESTION_SKIPPED = "review_suggestion.skipped"

# Feature status constants (derived, not stored)
FEATURE_IDEA = "idea"
FEATURE_IN_CLARIFICATION = "in_clarification"
FEATURE_DEFINED = "defined"
FEATURE_GENERATED = "generated"
FEATURE_IN_PROGRESS = "in_progress"
FEATURE_DONE = "done"

TASK_STATUSES = (
    READY_FOR_SPEC,
    SPEC_QA,
    READY_FOR_IMPLEMENTATION,
    IN_PROGRESS,
    WAITING_FOR_INPUT,
    BLOCKED,
    READY_FOR_QA,
    READY_FOR_DEPLOYMENT,
    DEPLOYED,
    ABANDONED,
)
