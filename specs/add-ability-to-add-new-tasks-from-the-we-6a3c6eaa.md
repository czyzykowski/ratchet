# Spec: Add "New Task" link to project page and fix dependency filter

## Objective

Surface the existing `/projects/{project_id}/tasks/new` form from the project detail page, and fix the dependencies `<select>` to exclude tasks in terminal statuses (`deployed`, `abandoned`) that can never be meaningful predecessors.

## Success Criteria
- [ ] Project detail page (`/projects/{project_id}`) has a visible "New Task" link pointing to `/projects/{project_id}/tasks/new`
- [ ] The dependencies `<select>` in `web/templates/tasks/new.html` only shows tasks whose status is not `deployed` or `abandoned`
- [ ] The handler in `web/routes/projects.py::new_task_form` filters `project_tasks` before passing them to the template
- [ ] Existing tests pass; a new unit/integration test covers the filtered task list

## Out of Scope

- Inline form on the project page
- Any changes to the POST handler or task creation logic
- Filtering dependencies in the task detail view

## Technical Context

- `web/routes/projects.py` — `new_task_form` (GET, ~line 103) calls `queries.get_project_tasks(conn, project_id)` and passes the full result as `project_tasks` to the template with no status filter.
- `web/templates/tasks/new.html` — iterates `project_tasks` in a `<select name="depends_on">` (lines 14–23) with no filtering.
- `web/templates/project_detail.html` — shows a task table but has no link to the new-task form.
- Terminal statuses are `"deployed"` and `"abandoned"` (defined in `core/events.py`).

## Tasks
- [ ] In `web/routes/projects.py::new_task_form`, filter `project_tasks` after the query: exclude tasks where `status in ("deployed", "abandoned")` before passing to the template context.
- [ ] In `web/templates/project_detail.html`, add a "New Task" link after the `<h2>Tasks</h2>` heading: `<a href="/projects/{{ project.id }}/tasks/new">+ New Task</a>`
- [ ] Add a test in `web/tests/` that GETs `/projects/{project_id}/tasks/new` with a mix of task statuses and asserts that `deployed` and `abandoned` tasks do not appear in the response HTML.
- [ ] Update `CHANGELOG.md` under `[Unreleased]` → Added.

## Assumptions

- `get_project_tasks` returns objects with a `.status` attribute (string).
- Filtering in the route handler (Python) is acceptable; no new SQL query or query parameter needed.
- No JS or flash messages are required for this change.

## Verification Commands
```bash
pytest web/tests/ -v -k "new_task"
mypy web/
ruff check web/
```

## What Exists After This Spec

The project detail page has a visible entry point to create tasks. The new-task form shows only actionable tasks as dependency candidates. The hidden form is now discoverable.