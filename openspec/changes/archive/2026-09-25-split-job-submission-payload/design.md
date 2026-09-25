## Context

`_submit_backup_job` in `src/starrocks_br/api/routes/jobs.py` already implements the
group-existence check (a prior change landed it): it currently guards on `if not payload.group`
to return 404, then calls `inventory_groups.group_exists`. See proposal.md - Why for the shared-model
problem this change fixes.

## Goals / Non-Goals

**Goals:**
- One Pydantic request model per job-submission endpoint, each `extra="forbid"`, carrying only
  the fields that endpoint's handler reads.
- Move the restore group/table and prune strategy-count checks to the HTTP boundary as synchronous
  422s, while keeping the existing `handlers.py` checks as defense-in-depth.

**Non-Goals:**
- Changing any handler-level (`handlers.py`) behavior or its existing tests.
- Adding a "backup everything" fallback, or any other behavior change to what tables get
  backed up/restored/pruned.
- Introducing a deprecated alias for `JobSubmitRequest` — this is an internal API with no external
  consumers to preserve compatibility for.

## Decisions

- **Four separate models over one model with per-endpoint optional/required tweaks.** A shared
  model can't express "this field doesn't exist for this endpoint" — only "unused for this
  endpoint but still accepted", which is exactly the problem being fixed. Separate models make
  each endpoint's OpenAPI schema self-documenting.
- **`extra="forbid"` on every new model.** Pydantic's default (`extra="ignore"`) would keep the
  silent-no-op behavior for stray fields. `forbid` turns that into a 422, matching the goal that a
  client can't send a foreign field and have it silently do nothing.
- **Cross-field validation (restore group/table; prune strategy count) via `model_validator(mode="after")`,
  not removed from `handlers.py`.** The Pydantic validator gives a synchronous 422 for the
  HTTP path; the handler check remains for any future non-HTTP caller of `run_restore`/`run_prune`
  directly. Duplication here is intentional, not an oversight.
- **`_submit_backup_job`'s existing `if not payload.group: raise HTTPException(404, ...)` branch
  is deleted, not kept as a second guard.** Once `BackupFullRequest.group`/`BackupIncrementalRequest.group`
  are non-optional `str = Field(min_length=1, ...)`, FastAPI/Pydantic rejects a missing or empty
  `group` with 422 before the route function body runs — the branch becomes dead code. This is a
  deliberate 404→422 status change for the *missing*-group case; an unknown-but-present group is
  unaffected and stays 404 (that check runs against `inventory_groups.group_exists` after
  validation passes).
- **`_submit` drops `exclude_none=True` in `model_dump`.** Now that each model only carries fields
  its own endpoint uses, an absent optional field and an explicit `null` are the same signal to the
  handler (which reads via `.get()`), so there's no need to strip `None`s before building `params`.

## Risks / Trade-offs

- [Existing clients relying on a foreign field being silently ignored will now get a 422] →
  Acceptable per proposal.md (marked **BREAKING**); this is an internal API with no external
  compatibility guarantee, and the whole point of the change is to stop that silent acceptance.
- [Missing-group case changes from 404 to 422, which could surprise a caller pattern-matching on
  404 for "not found"] → Documented explicitly in the modified spec requirement and proposal;
  the *unknown* group case (the more common "not found" scenario) keeps returning 404.
