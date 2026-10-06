"""Fail-closed review policy. No third-party dependencies or model execution."""
import json
import re

MAX_BYTES = 65536
SHA = re.compile(r"[0-9a-f]{40}\Z")
IDENTITY = ("repository", "pr", "head", "base", "base_ref", "run_id", "run_attempt")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse(raw):
    require(isinstance(raw, str) and 0 < len(raw.encode()) <= MAX_BYTES, "missing/oversized result")
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


def validate(expected, current, code, security, jobs):
    require(set(expected) == set(IDENTITY), "invalid expected identity")
    require(SHA.fullmatch(expected["head"]) and SHA.fullmatch(expected["base"]), "invalid revision")
    require(type(expected["pr"]) is int and expected["pr"] > 0, "invalid PR")
    require(current["state"] == "open" and current["head"]["repo"]["full_name"] == expected["repository"], "closed/fork PR")
    require(current["base"]["repo"]["full_name"] == expected["repository"], "wrong destination")
    require(current["number"] == expected["pr"] and current["head"]["sha"] == expected["head"]
            and current["base"]["sha"] == expected["base"] and current["base"]["ref"] == expected["base_ref"], "stale or retargeted PR")
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
