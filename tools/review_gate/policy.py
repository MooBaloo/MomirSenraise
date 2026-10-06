"""Fail-closed review policy. No third-party dependencies or model execution."""
import json
import re

MAX_BYTES = 65536
SHA = re.compile(r"[0-9a-f]{40}\Z")
IDENTITY = ("repository", "pr", "head", "base", "base_ref", "merge", "run_id", "run_attempt")
TRUST_IDENTITY = ("repository", "pr", "head", "base", "base_ref", "merge")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def require_trusted_security_source(identity, trust_policy):
    """Admission only, not a sandbox: local Security scans require trusted code."""
    require(set(trust_policy) == {"trusted_snapshots"}
            and isinstance(trust_policy["trusted_snapshots"], list), "invalid scanner trust policy")
    expected = {key: identity[key] for key in TRUST_IDENTITY}
    require(type(expected["pr"]) is int and expected["pr"] > 0
            and all(isinstance(expected[key], str) and SHA.fullmatch(expected[key])
                    for key in ("head", "base", "merge")), "invalid trusted snapshot identity")
    for entry in trust_policy["trusted_snapshots"]:
        require(isinstance(entry, dict) and set(entry) == set(TRUST_IDENTITY)
                and type(entry["pr"]) is int, "invalid trusted snapshot entry")
    require(expected in trust_policy["trusted_snapshots"],
            "Security CLI source is not explicitly trusted; scan and credentials must remain blocked")


def parse(raw, max_bytes=MAX_BYTES):
    require(isinstance(raw, str) and 0 < len(raw.encode()) <= max_bytes, "missing/oversized result")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    result = json.loads(raw, object_pairs_hook=unique,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    require(isinstance(result, dict), "result must be an object")
    return result


def validate_security_result(data, identity):
    """Check decision fields in the pinned CLI 0.2.0 JSON contract."""
    for field, document in (("manifest", "scan-manifest"), ("findings", "findings"), ("coverage", "coverage")):
        value = data.get(field)
        require(isinstance(value, dict) and value.get("documentType") == "codex-security." + document
                and value.get("schemaVersion") == "1.0", "missing/unsupported security document")
    scan = data["manifest"].get("scan")
    require(isinstance(scan, dict) and scan.get("status") == "completed"
            and isinstance(scan.get("id"), str) and bool(scan["id"]), "incomplete security scan")
    target = scan.get("target")
    require(isinstance(target, dict) and target.get("kind") == "git_diff"
            and target.get("baseRevision") == identity["base"]
            and target.get("headRevision") == identity["merge"], "wrong security revisions")
    require(data["coverage"].get("completeness") == "complete", "incomplete security coverage")
    require(data["coverage"].get("scanId") == scan["id"]
            and data["findings"].get("scanId") == scan["id"], "mixed security scan documents")
    findings = data["findings"].get("findings")
    require(isinstance(findings, list), "missing security findings")
    for finding in findings:
        require(isinstance(finding, dict) and isinstance(finding.get("severity"), dict)
                and finding["severity"].get("level") == "informational", "blocking/invalid security finding")


def validate_revision(expected, current, merge_ref, merge_commit):
    """Bind a PR to GitHub's current synthetic merge and its ordered parents."""
    require(set(expected) == set(IDENTITY), "invalid expected identity")
    require(all(isinstance(expected[k], str) and SHA.fullmatch(expected[k])
                for k in ("head", "base", "merge")), "invalid revision")
    require(expected["merge"] not in (expected["head"], expected["base"]), "test merge must be distinct")
    require(type(expected["pr"]) is int and expected["pr"] > 0, "invalid PR")
    require(current["state"] == "open" and current["head"]["repo"]["full_name"] == expected["repository"], "closed/fork PR")
    require(current["base"]["repo"]["full_name"] == expected["repository"], "wrong destination")
    require(current["number"] == expected["pr"] and current["head"]["sha"] == expected["head"]
            and current["base"]["sha"] == expected["base"] and current["base"]["ref"] == expected["base_ref"], "stale or retargeted PR")
    require(current.get("mergeable") is True and current.get("merge_commit_sha") == expected["merge"], "absent/stale test merge")
    require(merge_ref.get("ref") == f"refs/pull/{expected['pr']}/merge"
            and merge_ref.get("object") == {"type": "commit", "sha": expected["merge"]}, "wrong PR merge ref")
    require(merge_commit.get("sha") == expected["merge"]
            and merge_commit.get("parents") == [expected["base"], expected["head"]], "wrong merge parents")


def validate(expected, current, merge_ref, merge_commit, code, security, jobs):
    validate_revision(expected, current, merge_ref, merge_commit)
    require(jobs == {"code": "success", "security": "success"}, "review failed, skipped, cancelled, or missing")
    for result in (code, security):
        require(result.get("identity") == expected, "wrong review identity")
    require(set(code) == {"identity", "status", "findings"}, "invalid code review fields")
    require(code["status"] == "complete", "incomplete code review")
    require(isinstance(code["findings"], list), "invalid findings")
    for finding in code["findings"]:
        require(isinstance(finding, dict) and set(finding) == {"priority", "title", "path", "line"}, "invalid finding")
        require(type(finding["priority"]) is int and 0 <= finding["priority"] <= 3, "invalid priority")
        require(all(isinstance(finding[k], str) and 0 < len(finding[k]) <= 1000 for k in ("title", "path")), "invalid finding text")
        require(type(finding["line"]) is int and finding["line"] > 0, "invalid line")
        require(finding["priority"] > 2, "blocking code finding (P0/P1/P2)")
    require(set(security) == {"identity", "exit_code", "result_sha256", "threshold"}, "invalid security receipt")
    require(type(security["exit_code"]) is int and security["exit_code"] == 0, "security findings/error/incomplete coverage")
    require(security["threshold"] == "low", "incorrect security threshold")
    require(isinstance(security["result_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", security["result_sha256"]), "missing security evidence digest")
    return True
