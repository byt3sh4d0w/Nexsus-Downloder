## What and why

## Tests
- [ ] New/updated test (bug fixes: it failed before the fix)
- [ ] `python -m compileall -q .`, `ruff check .`, `python -m unittest discover -s tests` pass
- Tested on (OS + Python):

## Checklist
- [ ] No existing test deleted or weakened
- [ ] Scheduler (`core/scheduler.py`) untouched, or a reproducing test and benchmark are included
- [ ] Docs/CHANGELOG updated if behaviour changed
- [ ] No secrets, personal paths or generated files
