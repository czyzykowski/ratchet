# Changelog

## [Unreleased]

### Added
- `--verbose` / `-v` flag to `scripts/board.py` to display full 36-char task UUIDs instead of truncated 8-char IDs

### Fixed
- `current_projects.updated_at` now reflects `MAX(occurred_at)` across all project events instead of being locked to the creation event
- `current_executions` now includes `branch_name` column extracted from `execution.started` payload, matching the `Execution` Pydantic model
