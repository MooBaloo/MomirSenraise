# Agent guidance

Read [Development](README.md#development) before making changes.

- Verify the repository remote and intended PR destination before publishing.
- Keep changes focused on the requested scope and preserve existing attribution.
- Run the documented build/test command and report failures and coverage limits.
- Inspect the exact diff for unintended files and sensitive content before pushing.
- Use a branch and PR; leave merge, release, deployment, and device operations to
  separate maintainer authorization.

All target branches require completed current-head Codex code and Codex Security
review before merge; main additionally requires owner approval. The inactive
candidate and unresolved activation gates are in
[Codex review gate](docs/codex-review-gate.md). A successful process, review
comment, or fixture test is not a substitute for those reviews.
