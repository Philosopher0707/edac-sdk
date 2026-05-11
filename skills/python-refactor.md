---
name: python-refactor
description: Refactor Python code for clarity, performance, and type safety.
applies_when: |
  Users request code refactoring, cleanup, or modernization.
  Applies to Python files with .py extension.
---

# Python Refactoring Skill

## Principles
1. Preserve existing behavior (no functional changes without explicit approval)
2. Add type hints where missing
3. Replace manual loops with comprehensions where clearer
4. Extract helper functions for repeated logic
5. Update docstrings to Google style

## Tools Available
- `read_file`: Read source file
- `edit_file`: Apply diff
- `run_tests`: Execute pytest
- `run_linter`: Run ruff/mypy

## Verification Steps
After each edit:
1. Run tests — must pass
2. Run linter — no new errors
3. Check type coverage — mypy must pass

## Failure Recovery
If tests fail after edit:
1. Read test output
2. Identify root cause
3. Fix and re-run
4. If 3 failures, emit `human.input_required`
