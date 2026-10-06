"""Trusted runner glue. Always execute from the protected main checkout with -I."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

spec = importlib.util.spec_from_file_location("policy", Path(__file__).with_name("policy.py"))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
REPO = os.environ["GITHUB_REPOSITORY"]
CONTEXT = "Codex and Security review (merge)"


def api(path, body=None):
    headers = {"Authorization": "Bearer " + os.environ["GH_TOKEN"],
               "Accept": "application/vnd.github+json", "User-Agent": "Momir-review-gate"}
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request("https://api.github.com/repos/" + REPO + path, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def output(name, value):
    # JSON is single-line; never use model text as a workflow command.
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(name + "=" + json.dumps(value, separators=(",", ":")) + "\n")


def prepare():
    policy.require(os.environ["GITHUB_REF"] == "refs/heads/main", "dispatch only protected main")
    policy.require(api("/branches/main")["commit"]["sha"] == os.environ["GITHUB_SHA"], "stale control workflow")
    number = os.environ["PR_NUMBER"]
    policy.require(re.fullmatch(r"[1-9][0-9]*", number), "invalid PR number")
    pr = api("/pulls/" + number)
    policy.require(pr["state"] == "open" and pr["head"]["repo"]["full_name"] == REPO, "only open same-repository PRs")
    identity = dict(repository=REPO, pr=int(number), head=pr["head"]["sha"],
                    base=pr["base"]["sha"], base_ref=pr["base"]["ref"], merge=pr.get("merge_commit_sha"),
                    run_id=os.environ["GITHUB_RUN_ID"], run_attempt=os.environ["GITHUB_RUN_ATTEMPT"])
    policy.validate_revision(identity, *snapshot(identity))
    # This job has no OpenAI credentials; reject untrusted source before dependent
    # jobs receive secrets. Same-repository membership is not a trust grant.
    require_scanner_trust(identity)
    output("identity", identity)
    api("/statuses/" + identity["merge"], {"state": "pending", "context": CONTEXT,
        "description": "Review started for current head and base", "target_url": run_url()})


def snapshot(identity):
    """Read only GitHub-provided identity; never trust a PR-supplied ref/policy."""
    number = str(identity["pr"])
    ref = api("/git/ref/pull/" + number + "/merge")
    commit = api("/git/commits/" + ref["object"]["sha"])
    # Reread the PR after the ref/commit reads to detect intervening pushes.
    pr = api("/pulls/" + number)
    return (pr, {"ref": ref["ref"], "object": {"type": ref["object"]["type"], "sha": ref["object"]["sha"]}},
            {"sha": commit["sha"], "parents": [parent["sha"] for parent in commit["parents"]]})


def run_url():
    return "https://github.com/" + REPO + "/actions/runs/" + os.environ["GITHUB_RUN_ID"]


def prompt():
    identity = policy.parse(os.environ["IDENTITY"])
    text = Path(__file__).parents[2].joinpath(".github/codex/review-prompt.txt").read_text()
    Path(os.environ["RUNNER_TEMP"], "review-prompt.txt").write_text(text + "\nExact identity:\n" + json.dumps(identity))


def require_scanner_trust(identity):
    # Protected-main workflow supplies an owner-managed repository variable,
    # never a dispatch input, PR file/label, or model-authored result.
    raw = os.environ.get("SECURITY_TRUST_POLICY") or '{"trusted_snapshots":[]}'
    policy.require_trusted_security_source(identity, policy.parse(raw))


def security():
    identity = policy.parse(os.environ["IDENTITY"])
    require_scanner_trust(identity)
    source = Path(os.environ["GITHUB_WORKSPACE"], "source")
    checked_out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    policy.require(checked_out == identity["merge"], "wrong scanner checkout")
    result = Path(os.environ["RUNNER_TEMP"], "security-result.json")
    binary = Path(os.environ["RUNNER_TEMP"], "security-cli/node_modules/.bin/codex-security")
    with result.open("wb") as stream:
        process = subprocess.run([str(binary), "scan", str(source), "--diff", identity["base"],
            "--head", identity["merge"], "--auth", "api-key", "--json", "--fail-on-severity", "low",
            "--scan-prompt-file", str(Path(__file__).parents[2] / ".github/codex/security-prompt.txt"),
            "--output-dir", str(Path(os.environ["RUNNER_TEMP"], "security-scan"))], stdout=stream)
    # Exit 0 is the CLI's documented complete-coverage/policy-pass contract.
    # Do not invent a model-authored security verdict or reinterpret exit 1/2.
    if process.returncode == 0:
        policy.validate_security_result(policy.parse(result.read_text(), max_bytes=4 * 1024 * 1024), identity)
    output("receipt", {"identity": identity, "exit_code": process.returncode, "threshold": "low",
                       "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest()})
    sys.exit(process.returncode or 0)


def publish():
    expected = policy.parse(os.environ["IDENTITY"])
    state = "failure"
    try:
        policy.validate(expected, *snapshot(expected),
                        policy.parse(os.environ.get("CODE_RESULT", "")),
                        policy.parse(os.environ.get("SECURITY_RESULT", "")),
                        {"code": os.environ["CODE_JOB"], "security": os.environ["SECURITY_JOB"]})
        policy.require(api("/branches/main")["commit"]["sha"] == os.environ["GITHUB_SHA"], "control workflow changed")
        state = "success"
    except (ValueError, KeyError, TypeError):
        print("Review gate rejected missing, stale, blocking, or incomplete evidence.")
    # Never publish this context on the head: GitHub falls back to head checks
    # when a new test merge has no status. A green fallback could reuse old review.
    api("/statuses/" + expected["merge"], {"state": state, "context": CONTEXT,
        "description": "Current review policy passed" if state == "success" else "Review policy failed; inspect this run",
        "target_url": run_url()})
    sys.exit(0 if state == "success" else 1)


if __name__ == "__main__":
    {"prepare": prepare, "prompt": prompt, "security": security, "publish": publish}[sys.argv[1]]()
