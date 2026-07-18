# `.esc-ai/` Generated-Artifact Consolidation
**Status:** Complete
**Plan:** [`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md)
("Composition model" and "Repository-local Escape AI directory" sections, rewritten
by this capability)
**Spans:** esc-ai-execution-framework (the layout change and the two path-resolution
bug fixes it exposed), esc-ai-orchestrator (this repo — consumer updates only)

## Objective

Confirm this repo has no hardcoded assumption that escape-ai's generated/managed
files (repository manifest, indexes, dependency graph, `INSTRUCTIONS.md`, per-component
manifests/profiles) live at repository/component root, now that
`esc-ai-execution-framework` moved all of them under a repository-local `.esc-ai/`
directory (component files flat and keyed by stable component ID, not mirroring
filesystem path).

## Deliverables

- `esc_orchestrator/escape_ai_cli.py`'s `repository_status` checked
  `(path / "INSTRUCTIONS.md").is_file()` directly — updated to
  `(path / ".esc-ai" / "INSTRUCTIONS.md").is_file()`. This was the only hardcoded
  repository/component-root-relative manifest path assumption found in
  `esc_orchestrator/` (`api.py`, `cli.py`, `runtime.py`, `scheduler.py`, `store.py`
  audited and confirmed clean; `scheduler.py`'s `.esc-ai/runs/<run-id>/` was already
  correct and unaffected).
- All rendering functions (`render_proposal`, `render_apply_result`, etc.) needed no
  code change — they render whatever path strings the execution framework's
  `analyze_repository`/`apply_onboarding_answers` results already contain, and those
  now carry the new `.esc-ai/...` paths automatically.
- `tests/test_escape_ai_cli.py` and `tests/test_orchestrator.py` updated: real
  end-to-end assertions (file-existence checks against a real onboarded repository)
  now check `.esc-ai/esc-execution.yaml`, `.esc-ai/components/<id>/esc-component.yaml`,
  and `.esc-ai/INSTRUCTIONS.md`; pure-rendering fixture data (fake proposal/result
  dicts with no real filesystem behind them) updated to use realistic `.esc-ai/...`
  path strings for consistency, though rendering itself is path-string-agnostic.
- `README.md`'s CLI walkthrough text updated to reference `.esc-ai/INSTRUCTIONS.md`.

## Test coverage

31/31 tests pass, run with `PYTHONPATH` pointed at the sibling
`esc-ai-execution-framework` checkout (`PYTHONPATH=.../esc-ai-execution-framework
python3 -m unittest discover -s tests -v`).
