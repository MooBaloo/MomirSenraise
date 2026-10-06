# Codex review gate — inactive implementation draft

Every PR must have completed Codex code review **and** Codex Security review of
its current head and base before merging, regardless of target branch. Main
also requires owner approval. Dev and other targets do not require owner
approval. Existing build/test checks remain required. No merge is authorized
by this draft, including merging the gate itself without its actual reviews.

## What is implemented

`codex-review.yml` is manual-only and its first job has literal `if: false`.
Consequently it cannot run either paid tool or publish a status. Merging this
file alone does not activate it. `review-policy-tests.yml` runs free local
Python fixtures on ordinary GitHub runners; GitHub runner usage is subject to
the repository's existing plan. No keys, settings, environments or apps are
created by these files.

The candidate runs only from current protected main, snapshots an open
same-repository PR (any target), and marks its head pending. Separate disposable
jobs run code review and Security. The publisher uses a fresh trusted checkout,
no model execution, no OpenAI credentials, and rereads live PR head/base/target.
It consumes JSON through environment variables, never shell interpolation.
Only the publisher and initial pending-status job have status-write permission.
No checkout persists Git credentials; no PR program, build, dependency script,
or workflow is deliberately executed in review jobs. Builds stay in their
separate credential-free workflow.

The Codex Action is pinned to `bdf19a4a223ec2549a3e2274a0cf61556bc07675`,
CLI `0.160.1`, with read-only sandbox, privilege dropping, a fresh Codex home,
and the action as the final job step. Its schema-constrained result must echo
repository, PR, full head/base SHAs, target branch, run ID and attempt. Process
exit 0 alone is insufficient. Missing/malformed/oversized/duplicate-key JSON,
wrong identity, incomplete reviews, failed/skipped/cancelled jobs and P0/P1/P2
findings block. P3 findings are nonblocking. A new run reviews the full current
diff again; old clean results cannot substitute for new evidence.

The separate pinned Security CLI `0.2.0` installs outside the checkout with
install scripts disabled. It scans merge-base..head with `--fail-on-severity
low`: all rated vulnerabilities block. Its documented exit 0 means completed
coverage and policy passed; exit 1 is a blocking finding, exit 2 includes errors
and incomplete coverage, and interruptions also block. The trusted wrapper
requires a parseable result with the documented manifest/findings/coverage
fields and binds the CLI exit result and output digest to this run's identity.
It does not invent an alternative verdict from model prose. Raw Security
results remain on the ephemeral runner; secure evidence delivery must be
settled during activation rather than publishing vulnerability artifacts by
accident. General Codex code review is **not** Codex Security clearance.

## Activation gates (not performed)

1. Approve a bounded API budget, model/effort choice, per-run limits and alert /
   cutoff arrangement. ChatGPT or a GitHub subscription does not establish API
   billing authorization. Timeouts are not dollar caps. Confirm separately that
   the owner has Codex Security CLI access; a working general OpenAI key does
   not demonstrate that entitlement. Recheck pinned tool compatibility before
   the first explicitly authorized paid pilot.
2. Provision credentials securely outside chat under a protected
   `codex-review-main` GitHub environment restricted to **branch main only**,
   excluding tags and other branches. Use separate least-privilege project keys
   `OPENAI_REVIEW_API_KEY` and `CODEX_SECURITY_API_KEY` as supported by each
   service. Never use repository-wide review secrets that a PR workflow could
   request. Environment provisioning and credential creation require separate
   authorization. This environment should not introduce an owner approval on
   each dev PR; protected control code on main carries the owner review.
3. Establish a genuinely trusted required-check publisher before enabling
   enforcement. The candidate's `Codex and Security review` commit-status name
   and GitHub Actions app ID **alone are not sufficient**: another workflow with
   write permission could spoof them. Determine available account capabilities
   and choose an enforceable protected required-workflow identity or a dedicated
   narrowly scoped GitHub App publisher with its credential available only to
   the protected control workflow. The latter requires a separately approved
   app/install and adapter change; neither is created here. Do not represent
   this candidate as a bypass-resistant gate until this is solved and tested.
   GitHub commit statuses are head-SHA scoped, not PR/base scoped: two PRs can
   share a head but target different bases. The final enforcement path must
   bind the required result to the PR and base as well, including a newly opened
   or retargeted PR that shares an already-green head. A dedicated App alone
   fixes publisher identity, not this scope problem. Include this case in the
   activation pilot; the generic status in this draft is not sufficient.
4. Protect control workflow, prompt, schema, policy and pins on main with owner
   review. Apply required PRs, current strict build/review checks, no bypass,
   no force pushes/deletions, and resolved threads to **all merge targets**;
   require owner approval additionally on main only. Validate coverage of newly
   created target branches. No settings are changed by this PR. CODEOWNERS alone
   does not secure a status publisher or require a review on every target.
5. In a separately reviewed activation PR, replace the hard disable only after
   the above controls exist. Manual dispatch from main is the initial bounded
   pilot: a trusted writer can request review without an owner approval on dev.
   Fork PRs remain blocked pending an explicitly designed trust path. An
   eventual automatic trigger must invoke protected main control code for all
   targets, never load a PR's workflow/script with secrets. Rerun after every
   head/base/target change. Strict up-to-date requirements prevent a previous
   base's green status being reused after base movement. Merge queues are not
   supported until their synthetic head identity has a tested review path.
6. Run a paid pilot only with explicit authorization: clean change, blocking
   code finding, blocking Security finding, missing credentials/access, timeout,
   stale head/base, concurrent retries and attempted forged status. Verify the
   trusted publisher cannot be spoofed, no secret is exposed to PR code, and
   findings are delivered safely for disposition. A failed publisher/API call
   must leave missing/pending/failure, never manufacture success. Cancellation
   can leave pending; rerun it. A narrow race exists between live identity read
   and status publication, so server-side current-head/strict-base enforcement
   is mandatory. Model review is fallible; fixtures test policy, not detection.

Until these gates pass, enforce the owner's no-merge rule operationally and
leave this PR draft. Do not disable existing main approval or substitute a
thumbs-up/comment for actual review completion.

## Local checks and reuse

```sh
python -I -m unittest discover -s tools/review_gate -p 'test_*.py' -v
```

The policy and runner use Python's standard library and derive repository/PR
identity at runtime. The only control-branch convention is `main`; evaluate it
explicitly for each repository. The fixtures cover main, dev and other targets,
blocking findings, malformed results, head/base/target changes, run identity,
scanner errors and incomplete/skipped reviews. No API inference is used.

Bounded rollout: finish Momir's inactive draft and approvals first; validate one
paid Momir pilot; inventory only other **active personal** repositories read-only
with owner agreement; open one focused inactive draft per agreed repository;
verify each repository's branch model, tests, protected publisher and budget
before activation. No other repository is mutated now. Archived/paused projects
remain untouched. Share the small policy through reviewed, pinned copies first;
do not introduce an organization-wide delivery framework or blanket secrets.

References inspected 2026-10-06:
- [Official Codex Action guidance](https://learn.chatgpt.com/docs/github-action)
- [Action source at the pin](https://github.com/openai/codex-action/tree/bdf19a4a223ec2549a3e2274a0cf61556bc07675)
- [Codex Security CI](https://learn.chatgpt.com/docs/security/cli/ci)
- [Security CLI result/exit contract](https://learn.chatgpt.com/docs/security/cli/reference)
