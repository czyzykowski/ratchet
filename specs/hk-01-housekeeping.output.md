---

```
COMPLETED: specs/hk-01-housekeeping.md

Tasks completed: 8/8
Verification:
  test -f CLAUDE.md: PASS
  test ! -f docs/CLAUDE.md: PASS
  test -x scripts/run-spec.sh: PASS
  git log --oneline -1: PASS

Files created/modified:
- CLAUDE.md (moved from docs/CLAUDE.md, rewritten with current codebase state)
- scripts/run-spec.sh (new, executable)

Commit: 9f4fd24 spec(hk-01): housekeeping CLAUDE.md and run script
```

**Note:** `CLAUDE.md` is in the global git ignore (`~/.config/git/ignore:7`). It was force-added with `git add -f` for this commit. Future `git add -A` calls will skip it — you may want to remove `CLAUDE.md` from `~/.config/git/ignore` or add a repo-level `.gitignore` that un-ignores it with `!CLAUDE.md`.
