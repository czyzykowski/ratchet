# Spec 1: Add reset button to blocked task detail page

## Objective

Add a "Reset" button to the task detail page that is visible only when the task is `blocked`. Clicking it transitions the task directly to `ready_for_implementation` and redirects back to the task detail page.

## Success Criteria

- [ ] `POST /tasks/{task_id}/reset` route exists in `web/routes/tasks.py`
- [ ] Route returns 400 if task status is not `blocked`
- [ ] Route transitions task from `blocked` to `ready_for_implementation` via `TaskStateMachine`
- [ ] Route redirects to `/tasks/{task_id}` with status 303 on success
- [ ] Reset button renders in `web/templates/tasks/detail.html` only when `task.status == "blocked"`
- [ ] Button is a `<form method="post" action="/tasks/{task_id}/reset">` with a submit button
- [ ] Unit tests cover happy path and guard (non-blocked task) in `web/tests/test_reset_route.py`

## Out of Scope

- Spec reassignment or any intermediate state transitions
- Showing the reset button on any status other than `blocked`
- Adding the button to the task board/list view

## Technical Context

- Valid transition `BLOCKED → READY_FOR_IMPLEMENTATION` is already defined in `core/state_machine.py` line 22
- Existing route pattern to follow: `POST /tasks/{task_id}/deploy` in `web/routes/tasks.py:171`
- Test pattern to follow: `web/tests/test_deploy_routes.py` — uses `InMemoryStore`, patches `PostgresStore`, uses `TestClient` with `follow_redirects=False`
- Template: `web/templates/tasks/detail.html` — deploy button pattern at lines 22-26 shows the conditional render pattern
- `TaskStateMachine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)` is the only call needed

## Tasks

- [ ] Add `POST /tasks/{task_id}/reset` route to `web/routes/tasks.py`:
  - Instantiate `PostgresStore(pool)` and `TaskStateMachine(store)`
  - Call `state_machine.get_current_status(task_id)` — return 404 if `None`
  - Return 400 if status is not `ev.BLOCKED`
  - Call `await state_machine.transition(task_id, ev.READY_FOR_IMPLEMENTATION)`
  - Return `RedirectResponse(url=f"/tasks/{task_id}", status_code=303)`
- [ ] Add reset button to `web/templates/tasks/detail.html` after the deploy button block (after line 26):
  ```html
  {% if task.status == "blocked" %}
  <p>
    <form method="post" action="/tasks/{{ task.id }}/reset">
      <button type="submit" class="btn">Reset to Ready</button>
    </form>
  </p>
  {% endif %}
  ```
- [ ] Create `web/tests/test_reset_route.py` with:
  - `test_reset_blocked_task` — sets up blocked task, posts to reset, asserts 303 redirect to `/tasks/{task_id}`, asserts store now has status `ready_for_implementation`
  - `test_reset_non_blocked_task` — sets up task in `in_progress`, posts to reset, asserts 400

## Assumptions

- No flash message needed (deploy route sets one, but reset is a simpler operation)
- `InvalidTransitionError` cannot be raised here since `BLOCKED → READY_FOR_IMPLEMENTATION` is always valid; no try/catch needed
- The route does not need to verify task existence beyond checking status (None status = 404)

## Verification Commands

```bash
pytest web/tests/test_reset_route.py -v
mypy web/routes/tasks.py
ruff check web/routes/tasks.py web/tests/test_reset_route.py
```

## What Exists After This Spec

A "Reset to Ready" button on the task detail page for blocked tasks. Clicking it immediately retries the task by transitioning it back to `ready_for_implementation` without reassigning the spec, allowing the worker to pick it up again.