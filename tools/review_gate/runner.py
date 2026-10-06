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
                    base=pr["base"]["sha"], base_ref=pr["base"]["ref"],
                    run_id=os.environ["GITHUB_RUN_ID"], run_attempt=os.environ["GITHUB_RUN_ATTEMPT"])
    output("identity", identity)
    api("/statuses/" + identity["head"], {"state": "pending", "context": "Codex and Security review",
        "description": "Review started for current head and base", "target_url": run_url()})


def run_url():
    return "https://github.com/" + REPO + "/actions/runs/" + os.environ["GITHUB_RUN_ID"]


def prompt():
    identity = policy.parse(os.environ["IDENTITY"])
    text = Path(__file__).parents[2].joinpath(".github/codex/review-prompt.txt").read_text()
    Path(os.environ["RUNNER_TEMP"], "review-prompt.txt").write_text(text + "\nExact identity:\n" + json.dumps(identity))


def security():
    identity = policy.parse(os.environ["IDENTITY"])
    source = Path(os.environ["GITHUB_WORKSPACE"], "source")
    merge_base = subprocess.check_output(["git", "merge-base", identity["base"], identity["head"]], cwd=source, text=True).strip()
    result = Path(os.environ["RUNNER_TEMP"], "security-result.json")
    binary = Path(os.environ["RUNNER_TEMP"], "security-cli/node_modules/.bin/codex-security")
    with result.open("wb") as stream:
        process = subprocess.run([str(binary), "scan", str(source), "--diff", merge_base,
            "--head", identity["head"], "--auth", "api-key", "--json", "--fail-on-severity", "low",
            "--output-dir", str(Path(os.environ["RUNNER_TEMP"], "security-scan"))], stdout=stream)
    # Exit 0 is the CLI's documented complete-coverage/policy-pass contract.
    # Do not invent a model-authored security verdict or reinterpret exit 1/2.
    if process.returncode == 0:
        data = json.loads(result.read_text())
        policy.require(isinstance(data, dict) and all(k in data for k in ("manifest", "findings", "coverage")), "missing security result")
    output("receipt", {"identity": identity, "exit_code": process.returncode, "threshold": "low",
                       "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest()})
    sys.exit(process.returncode or 0)


def publish():
    expected = policy.parse(os.environ["IDENTITY"])
    state = "failure"
    try:
        policy.validate(expected, api("/pulls/" + str(expected["pr"])),
                        policy.parse(os.environ.get("CODE_RESULT", "")),
                        policy.parse(os.environ.get("SECURITY_RESULT", "")),
                        {"code": os.environ["CODE_JOB"], "security": os.environ["SECURITY_JOB"]})
        policy.require(api("/branches/main")["commit"]["sha"] == os.environ["GITHUB_SHA"], "control workflow changed")
        state = "success"
    except (ValueError, KeyError, TypeError):
        print("Review gate rejected missing, stale, blocking, or incomplete evidence.")
    api("/statuses/" + expected["head"], {"state": state, "context": "Codex and Security review",
        "description": "Current review policy passed" if state == "success" else "Review policy failed; inspect this run",
        "target_url": run_url()})
    sys.exit(0 if state == "success" else 1)


if __name__ == "__main__":
    {"prepare": prepare, "prompt": prompt, "security": security, "publish": publish}[sys.argv[1]]()
