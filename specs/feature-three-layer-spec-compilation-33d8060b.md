# Spec 1: Three-layer spec compilation — Feature entity, interactive creation, and compile script

## Objective
Introduce a `Feature` aggregate and a `HighLevelSpec` sub-aggregate into the Ratchet event-sourced model. Provide `scripts/create-feature.py` for interactive feature definition and `scripts/compile-feature.py` for non-interactive batch compilation of eligible high-level specs into tasks.

## Success Criteria
- [ ] `Feature` and `HighLevelSpec` Pydantic models exist in `core/models.py`
- [ ] Event type constants for `feature.*` and `high_level_spec.*` are added to `core/events.py`
- [ ] `FeatureManager` class in `core/feature_manager.py` handles all feature/high-level-spec persistence and retrieval via event replay
- [ ] Alembic migration adds `current_features` and `current_high_level_specs` materialized views and updates `refresh_all_views()`
- [ ] `scripts/create-feature.py` runs an interactive Claude session, collects feature + high-level specs, presents for human approval, and persists to DB on confirmation
- [ ] `scripts/compile-feature.py` loads a feature, determines eligible high-level specs (not compiled, all dependency task_ids in `deployed` status), compiles each via a non-interactive Claude invocation, creates a task + spec, marks the high-level spec as compiled
- [ ] Unit tests in `core/tests/test_feature_manager.py` cover: create feature, add high-level specs, mark compiled, derived status logic, dependency eligibility
- [ ] Running `python scripts/create-feature.py --project-id <uuid>` with a valid project produces a persisted Feature with HighLevelSpecs
- [ ] Running `python scripts/compile-feature.py --feature-id <uuid>` compiles all eligible specs and reports counts

## Out of Scope
- Automatic/triggered compilation (no worker integration in this spec)
- UI or API surface beyond the two CLI scripts
- Modifying existing task or spec flows

## Technical Context

**Event sourcing pattern**: All state is stored as events in the `events` table. Aggregates are reconstituted by replaying events. Materialized views provide fast reads.

**Existing patterns to follow**:
- `core/spec_manager.py` — manager pattern: takes `Store`, appends events, replays to reconstruct
- `scripts/create-spec.py` — interactive Claude loop: `build_initial_prompt` → `run_claude` → `extract_spec` → confirm → persist
- `scripts/add-task.py` — task creation: dual-write to `task` aggregate and `project_tasks` registry
- `core/events.py` — all event type and status string constants live here

**New event types** (add to `core/events.py`):
```python
FEATURE_CREATED = "feature.created"
HIGH_LEVEL_SPEC_ADDED = "high_level_spec.added"
HIGH_LEVEL_SPEC_COMPILED = "high_level_spec.compiled"
```

**New status constants** (derived, not stored — computed in `FeatureManager.get_feature_status()`):
```python
FEATURE_DRAFT = "draft"           # no tasks yet
FEATURE_GENERATED = "generated"   # tasks exist, all in early statuses
FEATURE_IN_PROGRESS = "in_progress"  # ≥1 task past ready_for_implementation
FEATURE_DONE = "done"             # all tasks deployed
```

**`Feature` model** (`core/models.py`):
```python
class Feature(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    description: str
    created_at: datetime
    updated_at: datetime
```

**`HighLevelSpec` model** (`core/models.py`):
```python
class HighLevelSpec(BaseModel):
    id: UUID
    feature_id: UUID
    task_id: UUID | None       # set when compiled
    title: str
    order: int
    content: str
    compiled: bool
    dependencies: list[UUID]   # IDs of other HighLevelSpecs in same feature
```

**`FeatureManager`** (`core/feature_manager.py`):
- `create_feature(project_id, title, description) -> Feature`
- `get_feature(feature_id) -> Feature | None`
- `list_features(project_id) -> list[Feature]`
- `add_high_level_spec(feature_id, title, order, content, dependencies) -> HighLevelSpec`
- `get_high_level_specs(feature_id) -> list[HighLevelSpec]`
- `mark_compiled(hls_id, task_id) -> None` — appends `HIGH_LEVEL_SPEC_COMPILED` event; sets `compiled=True`, `task_id`

**Materialized views** (new migration):
- `current_features`: replays `feature.created` events — columns: `id`, `project_id`, `title`, `description`, `created_at`, `updated_at`
- `current_high_level_specs`: replays `high_level_spec.added` + `high_level_spec.compiled` events — columns: `id`, `feature_id`, `task_id`, `title`, `order`, `content`, `compiled`, `dependencies` (JSONB array of UUIDs)
- Update `refresh_all_views()` SQL function to include both new views

**Compilation prompt** (`compile-feature.py`): Similar structure to `create-spec.py`'s `build_initial_prompt` — passes INTENT.md + high-level spec content as the "opening description", instructs Claude to produce the spec in `## SPEC READY` format without interactive turns. Single non-interactive `run_claude` call.

**Dependency eligibility**: A `HighLevelSpec` is eligible for compilation when `compiled == False` AND for every UUID in `dependencies`, the corresponding `HighLevelSpec.task_id` is not None and that task's status is `deployed`. High-level specs with no dependencies are immediately eligible.

## Tasks
- [ ] Add `FEATURE_CREATED`, `HIGH_LEVEL_SPEC_ADDED`, `HIGH_LEVEL_SPEC_COMPILED` constants to `core/events.py`; add `FEATURE_DRAFT`, `FEATURE_GENERATED`, `FEATURE_IN_PROGRESS`, `FEATURE_DONE` status constants
- [ ] Add `Feature` and `HighLevelSpec` Pydantic models to `core/models.py`
- [ ] Implement `core/feature_manager.py` with `FeatureManager` class (`create_feature`, `get_feature`, `list_features`, `add_high_level_spec`, `get_high_level_specs`, `mark_compiled`)
- [ ] Write unit tests in `core/tests/test_feature_manager.py` using `InMemoryStore`; cover all `FeatureManager` methods and derived status logic
- [ ] Write Alembic migration adding `current_features` and `current_high_level_specs` materialized views and updating `refresh_all_views()` to include both
- [ ] Implement `scripts/create-feature.py` — interactive Claude session, approval confirmation, persist Feature + HighLevelSpecs via `FeatureManager`
- [ ] Implement `scripts/compile-feature.py` — load feature, determine eligible specs, compile each non-interactively via `run_claude`, create task (dual-write pattern from `add-task.py`), create and assign spec (via `SpecManager`), call `mark_compiled`, report summary

## Assumptions
- The `refresh_all_views()` Postgres function exists and is called via `store.refresh_views()` — the migration must alter it to include the two new views
- `HighLevelSpec.dependencies` stores IDs of sibling `HighLevelSpec` records within the same feature (not task IDs)
- Compilation of a high-level spec creates the task in `ready_for_spec` and immediately advances it to `ready_for_implementation` by running through the spec assignment flow (same as `assign_spec_to_task` in `create-spec.py`)
- The `run_claude` helper can be copied into `compile-feature.py` (or extracted to a shared `scripts/_claude.py` utility if the implementer prefers, but not required)
- Feature has no explicit "approved" status flag in the DB — the interactive creation script only persists after human confirmation; prior to that nothing is written

## Verification Commands
```bash
# Unit tests (no DB required)
pytest core/tests/test_feature_manager.py -v

# Apply migration
alembic upgrade head

# Smoke: create a feature interactively
.venv/bin/python scripts/create-feature.py --project-id <uuid>

# Smoke: compile eligible high-level specs
.venv/bin/python scripts/compile-feature.py --feature-id <uuid>

# Verify tasks were created
.venv/bin/python scripts/board.py
```

## What Exists After This Spec

- `core/events.py` has `feature.*` and `high_level_spec.*` event type constants and feature status constants
- `core/models.py` has `Feature` and `HighLevelSpec` Pydantic models
- `core/feature_manager.py` has `FeatureManager` with full CRUD over features and high-level specs
- `core/tests/test_feature_manager.py` has full unit test coverage of `FeatureManager`
- A new Alembic migration adds `current_features` and `current_high_level_specs` materialized views
- `scripts/create-feature.py` enables humans to define features interactively with Claude
- `scripts/compile-feature.py` batch-compiles eligible high-level specs into tasks, respecting dependency ordering