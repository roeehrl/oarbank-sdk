"""The hardware compatibility table (docs/compatibility.md) is generated from docs/compatibility/reports.json, which must
pass its schema and checks, and the page must never drift from it."""
import copy
import importlib.util
import json
from datetime import date
from pathlib import Path

import jsonschema
import pytest

from oarbank_sdk import gpu

_spec = importlib.util.spec_from_file_location("gen_compatibility", Path(__file__).parents[1] / "scripts" / "gen_compatibility.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


@pytest.fixture
def data():
    return gen.load()


def test_the_reports_pass_the_schema_and_the_checks(data):
    assert gen.problems(data) == []


def test_the_page_is_fresh(data):
    assert gen.PAGE.read_text(encoding="utf-8") == gen.render(data), "run `uv run python scripts/gen_compatibility.py`"


def test_the_schema_is_valid_draft_2020_12_and_names_the_apis_a_core_detects():
    schema = json.loads(gen.SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    assert tuple(sorted(schema["$defs"]["api"]["enum"])) == tuple(sorted(gpu.KNOWN_APIS))


def test_every_os_has_a_table_and_every_wanted_issue_a_row(data):
    page = gen.render(data)
    for label in gen.FAMILIES.values():
        assert f"\n## {label}\n" in page
    for w in data["wanted"]:
        assert f"[Help wanted: #{w['issue']}]({gen.ISSUES}/{w['issue']})" in page


def _report(data, **change):
    bad = copy.deepcopy(data)
    bad["reports"][0].update(change)
    return bad


@pytest.mark.parametrize("change, problem", [
    ({"status": "fine"}, "is not one of"),
    ({"platform": "darwin"}, "is not one of"),
    ({"commit": "HEAD"}, "does not match"),
    ({"date": "4 October"}, "does not match"),
    ({"notes": "line | break"}, "does not match"),
    ({"extra": 1}, "Additional properties"),
    ({"gpu_apis": {"host": ["metal", "glide"], "containers": None}}, "is not valid under any of the given schemas"),
    ({"source": {"kind": "issue", "url": "https://example.com/report"}}, "does not match"),
])
def test_the_schema_refuses_malformed_rows(data, change, problem):
    assert any(problem in p for p in gen.problems(_report(data, **change)))


@pytest.mark.parametrize("change, problem", [
    ({"tests": [{"id": "test_gpu_apis", "result": "failed"}]}, "a verified row has a failed test"),
    ({"tests": [{"id": "no_such_test", "result": "passed"}]}, "is not in `tests`"),
    ({"tests": [{"id": "test_gpu_apis", "result": "passed"}] * 2}, "a test is listed twice"),
    ({"status": "partial", "notes": None}, "says what is wrong in `notes`"),
    ({"gpu": {"vendor": "none", "model": None, "driver": None}}, "reports APIs, so detection is wrong"),
    ({"gpu": {"vendor": "nvidia", "model": None, "driver": "580.82"}}, "name the GPU model"),
    ({"ran_in": ["host", "wslc"]}, "WSL containers run on Windows only"),
    ({"ran_in": ["host", "container"], "gpu_apis": {"host": ["metal"], "containers": None}}, "reports `gpu_apis.containers`"),
    ({"source": {"kind": "issue", "url": "https://github.com/roeehrl/oarbank/issues/15"}}, "names the reporter's handle"),
    ({"source": {"kind": "ci", "url": "https://github.com/roeehrl/oarbank/actions/runs/1", "reporter": "someone"}},
     "only an issue report names a reporter"),
    ({"date": "2099-01-01"}, "the date is in the future"),
    ({"notes": "it ran in /Users/someone/oarbank"}, "a home directory path"),
    ({"notes": "it ran in C:\\Users\\someone\\oarbank"}, "a home directory path"),
    ({"notes": "mail me at someone@example.com"}, "an e-mail address"),
    ({"notes": "adapter 3c:22:fb:01:02:03"}, "a MAC address"),
    ({"source": {"kind": "maintainers", "url": "https://github.com/roeehrl/oarbank/blob/main/docs/design/gpu-placement.md"}},
     "a design note link"),
])
def test_the_checks_refuse_rows_the_schema_cannot(data, change, problem):
    bad = _report(data, **change)
    if change.get("notes", "") is None:
        del bad["reports"][0]["notes"]
    found = gen.problems(bad, today=date(2026, 10, 5))
    assert any(problem in p for p in found), found


def test_ids_and_wanted_issues_are_unique(data):
    bad = copy.deepcopy(data)
    bad["reports"].append(copy.deepcopy(bad["reports"][0]))
    bad["wanted"].append(copy.deepcopy(bad["wanted"][0]))
    found = gen.problems(bad)
    assert any("the id is used twice" in p for p in found) and "wanted: an issue is listed twice" in found


def test_rendering_is_deterministic_whatever_the_order_of_the_rows(data):
    shuffled = copy.deepcopy(data)
    shuffled["reports"].reverse()
    shuffled["wanted"].reverse()
    shuffled["tests"] = dict(reversed(list(shuffled["tests"].items())))
    assert gen.render(shuffled) == gen.render(data)
