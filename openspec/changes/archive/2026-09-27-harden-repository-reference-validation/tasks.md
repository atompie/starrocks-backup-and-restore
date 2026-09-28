## 1. Confirm existing behavior (PLAN.md 3.1)

- [x] 1.1 Re-read `ensure_repository_exists` (`api/routes/_cluster_connect.py`) and its call sites in `api/routes/schedules.py` (create and update) and confirm no code change is needed - both already validate live against `SHOW REPOSITORIES` scoped to the schedule's own cluster; record this confirmation in the PR/commit description rather than as a code diff.

## 2. Cross-cluster regression test (PLAN.md 3.2, 3.4)

- [x] 2.1 In `tests/unit/service/test_api_schedules.py`, add a test that creates a schedule against cluster A naming a repository that only exists (per mocked `list_repositories`) on cluster B, and verify the request is rejected with HTTP 404, the same as an unknown repository.
- [x] 2.2 Add the equivalent test for schedule update: an existing schedule on cluster A is updated to reference a repository name that only exists on cluster B, and verify HTTP 404 and that the stored schedule's `repository` is unchanged.
- [x] 2.3 Run `python -m pytest tests/unit/service/test_api_schedules.py` and confirm both new tests pass alongside the existing unknown-repository tests.

## 3. Document the reference pattern (PLAN.md 3.3)

- [x] 3.1 Update `SPEC.md` §4 (Repository - Reference) to state the `(cluster_id, name)` reference pattern explicitly and note it applies wherever a repository is referenced: today by Schedule, and later by Backup Reference and a restore target (sections 6 and 11 of PLAN.md, not yet implemented) - phrase the latter as forward-looking intent, not implemented behavior.

## 4. Verification

- [x] 4.1 `python -m pytest tests/unit` passes in full (all green, no regressions from the new tests).
- [x] 4.2 Manually diff `SPEC.md` §4 to confirm the new subsection reads consistently with the rest of the section and does not contradict existing wording about Schedule referencing repositories.
