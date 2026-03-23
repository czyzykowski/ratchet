# Data Model

All state is derived from event replay. The `events` table is the single source of truth. Pydantic models below represent the projected state at any point in time.

## Entity Relationship Diagram

```mermaid
erDiagram
    Event {
        UUID id PK
        UUID aggregate_id
        string aggregate_type
        string event_type
        json payload
        int schema_version
        datetime occurred_at
        int sequence
    }

    Project {
        UUID id PK
        string name
        string repo_url
        string local_path
        string status
        string config_source
        string claude_md
        string intent_md
        string ratchet_yaml
        list required_capabilities
        datetime created_at
        datetime updated_at
    }

    Feature {
        UUID id PK
        UUID project_id FK
        string title
        string description
        UUID session_id
        datetime created_at
        datetime updated_at
    }

    HighLevelSpec {
        UUID id PK
        UUID feature_id FK
        UUID task_id FK
        string title
        int order
        string content
        bool compiled
        list dependencies
    }

    Task {
        UUID id PK
        UUID project_id FK
        UUID current_spec_id FK
        string title
        string status
        int refinement_count
        list depends_on
        list required_capabilities
        string merge_commit_sha
        datetime created_at
        datetime updated_at
    }

    Spec {
        UUID id PK
        UUID task_id FK
        UUID previous_spec_id FK
        string content
        datetime created_at
    }

    Execution {
        UUID id PK
        UUID task_id FK
        UUID spec_id FK
        string status
        string failure_reason
        string branch_name
        datetime started_at
        datetime completed_at
    }

    ExecutionTrace {
        UUID execution_id FK
        UUID task_id FK
        UUID spec_id FK
        string content
        datetime started_at
        datetime created_at
    }

    ChatSession {
        UUID id PK
        string session_type
        UUID context_id
        string context_type
        datetime created_at
        list messages
    }

    ReviewRun {
        UUID id PK
        UUID previous_run_id FK
        json scope
        int suggestion_count
        int applied_count
        int dismissed_count
        datetime started_at
        datetime completed_at
    }

    Suggestion {
        UUID id PK
        UUID review_run_id FK
        int order
        string target
        string target_path
        string title
        string reasoning
        json evidence
        string confidence
        string priority
        string status
    }

    QAExchange {
        int question_index
        string question
        string answer
        UUID execution_id FK
        datetime asked_at
        datetime answered_at
        string answered_by
    }

    Project ||--o{ Task : "has"
    Project ||--o{ Feature : "has"
    Feature ||--o{ HighLevelSpec : "contains"
    HighLevelSpec |o--o| Task : "compiles to"
    Task ||--o{ Spec : "refined by"
    Task ||--o{ Execution : "executed by"
    Spec ||--o| Spec : "previous version"
    Execution ||--o| ExecutionTrace : "produces"
    Execution ||--o{ QAExchange : "asks"
    Task }o--o{ Task : "depends on"
    ReviewRun ||--o{ Suggestion : "produces"
    ReviewRun |o--o| ReviewRun : "follows"
```

## Event Sourcing

All entities above are projections derived by replaying events from the `events` table. Each entity has a corresponding manager class that replays events to build the current state:

| Entity | Manager | Aggregate Type |
|--------|---------|---------------|
| Project | `ProjectManager` | `project` |
| Task | `TaskManager` | `task` |
| Spec | `SpecManager` | `spec`, `task_spec` |
| Execution | `ExecutionManager` | `execution`, `task_executions` |
| Feature | `FeatureManager` | `feature`, `project_features` |
| HighLevelSpec | `FeatureManager` | `feature` |
| ChatSession | `ChatSessionManager` | `chat_session` |
| ReviewRun | `ReviewManager` | `review` |

## Materialized Views

PostgreSQL materialized views provide fast reads for the web UI:

| View | Source | Refreshed |
|------|--------|-----------|
| `current_tasks` | task events | On event append + periodic |
| `current_projects` | project events | On event append + periodic |
| `current_specs` | spec events | On event append + periodic |
| `current_executions` | execution events | On event append + periodic |
| `current_features` | feature events | On event append + periodic |
| `current_high_level_specs` | feature events | On event append + periodic |
| `current_chat_sessions` | chat_session events | On event append + periodic |

Web API handlers read from views via `web/queries.py`. Worker/dispatch logic reads from event replay via `core/` managers.
