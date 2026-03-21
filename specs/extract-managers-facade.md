# Extract Managers Facade

## Problem

Manager construction boilerplate is repeated 247 times across 53 files. Every caller that needs domain logic instantiates managers inline:

```python
store = request.app.state.store
task_manager = TaskManager(store)
spec_manager = SpecManager(store)
state_machine = TaskStateMachine(store)
pm = ProjectManager(store)
```

All 7 managers follow the identical `__init__(self, store: Store)` pattern (stateless, pure delegation). A single endpoint like `GET /tasks/{task_id}` instantiates 5 managers. Nothing enforces that managers in the same scope share the same Store instance.

## Proposed Interface

```python
# core/managers.py

class Managers:
    __slots__ = (
        "store", "projects", "tasks", "specs",
        "state_machine", "features", "reviews",
    )

    def __init__(self, store: Store) -> None:
        self.store = store
        self.projects = ProjectManager(store)
        self.tasks = TaskManager(store)
        self.specs = SpecManager(store)
        self.state_machine = TaskStateMachine(store)
        self.features = FeatureManager(store)
        self.reviews = ReviewManager(store)

    def execution(self, local_path: str) -> ExecutionManager:
        return ExecutionManager(self.store, local_path)
```

Usage:

```python
m = Managers(store)
task = await m.tasks.get_task(task_id)
await m.state_machine.transition(task_id, ev.READY_FOR_QA)
spec = await m.specs.get_current_spec(task_id)
em = m.execution(project.local_path)
```

## Dependency Strategy

- **In-process**: Pure construction, no I/O. `Managers` is a leaf aggregation module that imports all managers but nothing imports it except callers.
- **Incremental rollout**: `Managers` is additive. Old pattern and new pattern coexist. Files migrate one at a time.
- **Tests**: `Managers(InMemoryStore())` replaces 3-5 individual manager constructions per test. Tests that only need one manager can keep using it directly.

## Testing Strategy

- **New boundary tests to write**: Test that `Managers(store)` constructs all managers with the same store instance. Test that `execution()` returns an `ExecutionManager` with correct store and path.
- **Old tests to delete**: None — this is additive. Existing tests keep working.
- **Test environment needs**: `InMemoryStore` (already available, no new dependencies).

## Implementation Recommendations

- The module should own: centralized construction of all store-backed managers from a single Store instance.
- It should hide: the list of manager classes, their constructors, and the ExecutionManager's asymmetric `local_path` parameter.
- It should expose: typed attributes for each manager (`m.tasks`, `m.projects`, etc.), the underlying `store` for raw event queries, and an `execution(local_path)` factory method.
- Callers should migrate incrementally: replace `XManager(store)` calls with `m.x` attribute access, one file at a time. Start with `ProjectDispatcher` (already groups 4 managers), then web routes (heaviest boilerplate), then scripts.
