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
same-repository PR (any target), and marks its test-merge commit pending.
Separate disposable jobs run code review and Security. The publisher uses a fresh trusted checkout,
no model execution, no OpenAI credentials, and rereads live PR head/base/target,
`refs/pull/N/merge`, and the merge commit's ordered base/head parents.
It consumes JSON through environment variables, never shell interpolation.
Only the publisher and initial pending-status job have status-write permission.
No checkout persists Git credentials; no PR program, build, dependency script,
or workflow is deliberately executed in review jobs. Builds stay in their
separate credential-free workflow.

The Codex Action is pinned to `bdf19a4a223ec2549a3e2274a0cf61556bc07675`,
CLI `0.160.1`, with read-only sandbox, privilege dropping, a fresh Codex home,
and the action as the final job step. Its schema-constrained result must echo
repository, PR, full head/base/test-merge SHAs, target branch, run ID and attempt.
Process exit 0 alone is insufficient. Missing/malformed/oversized/duplicate-key JSON,
wrong identity, incomplete reviews, failed/skipped/cancelled jobs and P0/P1/P2
findings block. P3 findings are nonblocking. A new run reviews the full current
diff again; old clean results cannot substitute for new evidence.

The separate pinned Security CLI `0.2.0` installs outside the checkout with
install scripts disabled. Both reviewers inspect the test-merge tree and its
base..merge diff, so the current base is part of the reviewed source. The Security
command uses a main-controlled scan prompt and `--fail-on-severity low`: all
rated vulnerabilities block. Its documented exit 0 means completed
coverage and policy passed; exit 1 is a blocking finding, exit 2 includes errors
and incomplete coverage, and interruptions also block. The trusted wrapper
checks the pinned 0.2.0 result contract: completed manifest, exact base/merge
target, complete coverage, matching scan IDs and no rated findings. Missing,
malformed or oversized (over 4 MiB) results block even if the CLI exits 0. It
binds the CLI exit result and output digest to this run's identity.
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
3. Decide the publisher trust boundary described below before enforcing the
   new `Codex and Security review (merge)` context. No passing result may ever
   be published on a head SHA under this context. Require the actual status,
   not the disabled workflow's job conclusion (skipped jobs can look green).
   Test GitHub's server-side test-merge selection and absent-status fallback,
   including retargeting, duplicate PRs, base movement and force updates. The
   implementation refuses unknown/conflicting/missing merges, verifies the PR
   ref and ordered parents before work and publication, and never falls back to
   head. Existing head-only contexts must not be substituted for this context.
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
   selected publisher trust boundary holds, no secret is exposed to PR code, and
   findings are delivered safely for disposition. A failed publisher/API call
   must leave missing/pending/failure, never manufacture success. Cancellation
   can leave pending; rerun it. A narrow race exists between live identity read
   and status publication, so server-side current-head/strict-base enforcement
   is mandatory. Model review is fallible; fixtures test policy, not detection.

Until these gates pass, enforce the owner's no-merge rule operationally and
leave this PR draft. Do not disable existing main approval or substitute a
thumbs-up/comment for actual review completion.

## Native publisher controls and the single-owner boundary

The draft already keeps workflow, prompts, schema and evaluator on protected
main, accepts only a PR number, checks out untrusted review material separately,
and has a fresh publisher job. A PR's copy of any control file is reviewed as
source; this workflow never loads it as policy. Least-privilege job tokens and
main-only credential environments prevent review jobs from being publishers.
These controls do not require a new GitHub App and are useful in a single-owner
repository with trusted write-capable automation.

There is nevertheless a distinct limitation: GitHub's expected source can select
the GitHub Actions app, not this particular personal-repository workflow. A new
same-repository PR workflow can request `statuses: write` and post the same
context without using our evaluator. A default read-only token setting, the
existing workflow's `permissions: {}`, CODEOWNERS on main, and an environment
protecting API keys do not cap another workflow's token. Treating PR changes as
untrusted includes this case. Checking a workflow allowlist in our publisher
cannot prevent an independent publisher from bypassing our code entirely.

A native-only operational pilot is therefore possible **only with explicit
acceptance that all repository writers and their workflow changes are trusted
not to forge results**. It is not technical enforcement against untrusted
same-repository workflow changes. No new app is required just to obtain reviews;
existing integrated reviews remain useful, but comments are not required checks.
For the stronger untrusted-PR requirement, do not claim native-only enforcement
is adequate: a separately authorized, narrow App publisher is one option, not
an automatic prerequisite or a change made by this draft. Its signing credential
must be main-only and its app identity selected as the required status source.
It needs no separate server when trusted Actions code mints installation tokens.
Organization required-workflow identity is another option, but changing ownership
or plan is outside this work; organization merge queues are not a personal-repo
shortcut. Main owner approval remains separate from all-target machine review.

The test-merge change addresses head/base reuse independently of publisher
identity: GitHub evaluates test-merge statuses when present and otherwise uses
head statuses. Our context never writes a passing head status. A new base/head
therefore cannot borrow an old head success. This still needs a live ruleset
pilot, including equal-commit duplicate PR/retarget cases; local fixtures do not
prove GitHub's enforcement. Status authenticity remains the boundary above.

## Local checks and reuse

```sh
python -I -m unittest discover -s tools/review_gate -p 'test_*.py' -v
```

The policy and runner use Python's standard library and derive repository/PR
identity at runtime. The only control-branch convention is `main`; evaluate it
explicitly for each repository. The fixtures cover main, dev and other targets,
blocking findings, malformed results, head/base/target changes, run identity,
scanner errors and incomplete/skipped reviews, wrong PR merge refs, absent or
force-updated merges, reversed/wrong parents, and malformed Security decision
fields. No API inference is used.

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
- [GitHub test-merge status selection](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
- [GitHub token permissions](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)
- [Required workflow identity at organization/enterprise level](https://docs.github.com/en/enterprise-cloud@latest/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [Merge queue availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)
