"""Check docs/compatibility/reports.json and regenerate docs/compatibility.md from it (after adding or changing a row).

    uv run python scripts/gen_compatibility.py

The schema (docs/compatibility/reports.schema.json) holds each field's shape; `problems()` adds what a schema cannot
say: unique ids, test ids from the file's own list, a status that agrees with the tests and the APIs, and nothing that
identifies a reporter but the GitHub handle they posted under. tests/test_compatibility.py runs both and fails while the
page is stale. docs/compatibility/README.md says how a report becomes a row.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "compatibility" / "reports.json"
SCHEMA = ROOT / "docs" / "compatibility" / "reports.schema.json"
PAGE = ROOT / "docs" / "compatibility.md"

ISSUES = "https://github.com/roeehrl/oarbank/issues"
FAMILIES = {"macos": "macOS", "linux": "Linux", "windows": "Windows"}
FAMILY_OF = {"darwin": "macos", "linux": "linux", "windows": "windows"}
STATUS = {"verified": "Verified", "partial": "Partial", "not-working": "Not working"}
VENDORS = ["nvidia", "amd", "intel", "apple", "virtual", "none"]
MACHINES = {"physical": "", "vm": " (virtual machine)", "ci": " (CI runner)"}
SOURCES = {"maintainers": "maintainers", "ci": "CI"}
ISSUE_URL = re.compile(r"^https://github\.com/roeehrl/oarbank/issues/\d+(#issuecomment-\d+)?$")
# What a report must not carry: home directories (which name the user), e-mail addresses and MAC addresses.
PERSONAL = [
    (re.compile(r"/Users/|/home/|[A-Za-z]:\\+Users\\+", re.IGNORECASE), "a home directory path"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "an e-mail address"),
    (re.compile(r"\b[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}\b"), "a MAC address"),
]


def load(path: Path = DATA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def problems(data: dict, today: date | None = None) -> list[str]:
    """Everything wrong with `data`, schema first; empty when it may be published."""
    validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    found = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
             for e in sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))]
    if found:
        return found
    today = today or date.today()
    seen = set()
    for r in data["reports"]:
        where = f"reports/{r['id']}"
        if r["id"] in seen:
            found.append(f"{where}: the id is used twice")
        seen.add(r["id"])
        ids = [t["id"] for t in r["tests"]]
        found += [f"{where}: test {t!r} is not in `tests`" for t in ids if t not in data["tests"]]
        if len(set(ids)) != len(ids):
            found.append(f"{where}: a test is listed twice")
        results = {t["result"] for t in r["tests"]}
        if r["status"] == "verified" and "failed" in results:
            found.append(f"{where}: a verified row has a failed test")
        if r["status"] != "verified" and "notes" not in r:
            found.append(f"{where}: a {r['status']} row says what is wrong in `notes`")
        gpu, apis = r["gpu"], r["gpu_apis"]
        if gpu["vendor"] == "none":
            if gpu["model"] is not None or gpu["driver"] is not None:
                found.append(f"{where}: a machine without a GPU has no GPU model or driver")
            if r["status"] == "verified" and (apis["host"] or apis["containers"]):
                found.append(f"{where}: a machine without a GPU reports APIs, so detection is wrong there and the row is not verified")
        elif gpu["model"] is None:
            found.append(f"{where}: name the GPU model")
        family = FAMILY_OF[r["platform"].split("-")[0]]
        if "wslc" in r["ran_in"] and family != "windows":
            found.append(f"{where}: WSL containers run on Windows only")
        if "container" in r["ran_in"] and family == "windows":
            found.append(f"{where}: Linux containers on Windows are WSL containers (`wslc`)")
        if {"container", "wslc"} & set(r["ran_in"]) and apis["containers"] is None:
            found.append(f"{where}: a run in containers reports `gpu_apis.containers`")
        src = r["source"]
        if src["kind"] == "issue" and not (ISSUE_URL.match(src["url"]) and "reporter" in src):
            found.append(f"{where}: an issue report links the issue or its comment, and names the reporter's handle")
        if src["kind"] != "issue" and "reporter" in src:
            found.append(f"{where}: only an issue report names a reporter")
        if date.fromisoformat(r["date"]) > today:
            found.append(f"{where}: the date is in the future")
    issues = [w["issue"] for w in data["wanted"]]
    if len(set(issues)) != len(issues):
        found.append("wanted: an issue is listed twice")
    for text in _strings({"reports": data["reports"], "wanted": data["wanted"]}):
        found += [f"personal data ({what}): {text!r}" for pattern, what in PERSONAL if pattern.search(text)]
        if "docs/design/" in text:
            found.append(f"a design note link, which the docs site's hygiene gate refuses (link the commit): {text!r}")
    return found


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _codes(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _apis(apis) -> str:
    def one(v):
        return "not reported" if v is None else _codes(v) if v else "none"
    return f"host: {one(apis['host'])}; containers: {one(apis['containers'])}"


def _report_row(r: dict) -> list[str]:
    gpu = r["gpu"]
    machine = "No GPU" if gpu["vendor"] == "none" else gpu["model"]
    if gpu["driver"]:
        machine += f", driver {gpu['driver']}"
    machine += MACHINES[r["machine"]]
    kernel = "WSL kernel" if r["platform"].startswith("windows") else "kernel"
    os_ = ", ".join([r["os"], *([f"{kernel} {r['kernel']}"] if "kernel" in r else []), r["platform"]])
    ran = ", ".join("WSL containers" if w == "wslc" else w for w in r["ran_in"])
    if r["tools"]:
        ran += " (" + "; ".join(f"{k} {v}" for k, v in sorted(r["tools"].items(), key=lambda kv: kv[0].lower())) + ")"
    tests = "; ".join(f"`{t['id']}` {t['result']}" for t in r["tests"]) or "none"
    src = r["source"]
    who = f"@{src['reporter']}" if src["kind"] == "issue" else SOURCES[src["kind"]]
    report = f"[{who}, `{r['commit'][:7]}`, {r['date']}]({src['url']})"
    return [STATUS[r["status"]], machine, os_, ran, _apis(r["gpu_apis"]), tests, r.get("notes", ""), report]


def _wanted_row(w: dict, tests: dict) -> list[str]:
    run = sorted(t for t, spec in tests.items() if w["issue"] in spec["issues"])
    return ["Untested", w["hardware"], w["os"], "–", "–", ("to run: " + _codes(run)) if run else "–", w["question"],
            f"[Help wanted: #{w['issue']}]({ISSUES}/{w['issue']})"]


def _sort_key(r: dict):
    return (VENDORS.index(r["gpu"]["vendor"]), list(MACHINES).index(r["machine"]), (r["gpu"]["model"] or "").lower(), r["id"])


def render(data: dict) -> str:
    reports, wanted, tests = data["reports"], data["wanted"], data["tests"]
    count = {s: sum(r["status"] == s for r in reports) for s in STATUS}
    out = [
        "# Hardware compatibility",
        "",
        "Where Oarbank's GPU detection, GPU containers and Windows container runtime have run, and with what result. Each",
        "row is one configuration someone ran: its operating system, GPU and driver, where the work ran, the GPU APIs the",
        "agent reported, the gated tests and their results, the core commit, the date and a link to the report.",
        "",
        "Oarbank's CI has no GPU runners, and the maintainers' machines have no NVIDIA, AMD or Intel GPU, so rows for those",
        "come from contributors. **Untested** rows are configurations nobody has reported yet: if you have one, its issue says",
        "what to run, and [Add your machine](#add-your-machine) says how to report it.",
        "",
        f"**{len(reports)} configurations reported:** {count['verified']} verified, {count['partial']} partial, "
        f"{count['not-working']} not working. **{len(wanted)} untested.**",
        "",
        "Generated from [`compatibility/reports.json`](compatibility/reports.json) by `scripts/gen_compatibility.py`. Do not",
        "edit by hand.",
        "",
        "## Legend",
        "",
        "| Status | Meaning |",
        "|---|---|",
        "| Verified | Works as documented: every gated test in the run passed, or skipped itself where its hardware is absent, and the APIs reported are the ones the machine has. |",
        "| Partial | Some of it works: a test failed, an API is reported that does not work, or one that works is missed. The notes say which. |",
        "| Not working | The path fails on this configuration. The notes say where it stopped. |",
        "| Untested | Nobody has reported this configuration yet. Help wanted: the linked issue says what to run. |",
        "",
        "**GPU APIs** are the `gpu_apis` the agent reported: `host` for runners on the machine itself, `containers` for",
        "container jobs (see [Place work by GPU API](gpu-placement.md)). *none* means the list was reported and empty; *not",
        "reported* means the run did not record it. **Ran in** is the host, a Linux container (Podman, Docker or Colima), or",
        "WSL containers on Windows, with the versions of the tools involved. **Tests** are the gated tests below.",
    ]
    header = "| Status | GPU | OS | Ran in | GPU APIs | Tests | Notes | Report |"
    for family, label in FAMILIES.items():
        rows = [_report_row(r) for r in sorted(reports, key=_sort_key) if FAMILY_OF[r["platform"].split("-")[0]] == family]
        rows += [_wanted_row(w, tests) for w in sorted(wanted, key=lambda w: w["issue"]) if w["family"] == family]
        out += ["", f"## {label}", "", header, "|---|---|---|---|---|---|---|---|"]
        out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    out += [
        "",
        "## Tests",
        "",
        "Run from the repository root of [roeehrl/oarbank](https://github.com/roeehrl/oarbank) (the agent's commands from",
        "`rust/` with `cargo run -p oarbank-agent --` in place of `oarbank-agent` when it is not installed).",
        "",
        "| Test | Command | What it shows | Issues |",
        "|---|---|---|---|",
    ]
    for tid, spec in sorted(tests.items()):
        issues = ", ".join(f"[#{n}]({ISSUES}/{n})" for n in spec["issues"])
        out.append(f"| `{tid}` | `{_cell(spec['command'])}` | {_cell(spec['checks'])} | {issues} |")
    out += [
        "",
        "## Add your machine",
        "",
        f"1. Find your hardware in [#9]({ISSUES}/9), which links the issue with the exact commands for each kind of",
        f"   machine. Any machine, with or without a GPU, can run [#15]({ISSUES}/15): one command, `oarbank-agent gpu-apis`.",
        "2. Run them on the current `main` of the core, and note the commit (`git rev-parse --short HEAD`).",
        f"3. Open a [hardware report]({ISSUES}/new?template=hardware-report.yml). It asks for exactly what a row holds,",
        "   so a maintainer can turn it into one. A failed run is as useful as a passing one: don't fix anything to make it",
        "   pass, say where it stopped.",
        "",
        "Before you post, remove anything that identifies you or your network from the output: host names, user names,",
        "paths, serial numbers and addresses. A row keeps only what is in the tables above, and names a reporter only by the",
        "GitHub handle the report was posted under. How maintainers add a row is in",
        "[`compatibility/README.md`](compatibility/README.md).",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    data = load()
    found = problems(data)
    if found:
        print("docs/compatibility/reports.json:", *found, sep="\n  ", file=sys.stderr)
        return 1
    PAGE.write_text(render(data), encoding="utf-8", newline="\n")
    print(f"wrote {PAGE.relative_to(ROOT)}: {len(data['reports'])} reports, {len(data['wanted'])} untested")
    return 0


if __name__ == "__main__":
    sys.exit(main())
