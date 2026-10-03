"""docs/tutorial.md builds a real module: its files, assembled from the tutorial's code blocks, pass the conformance kit."""
import re
from pathlib import Path

from oarbank_sdk.conformance import conform

TUTORIAL = Path(__file__).parents[1] / "docs" / "tutorial.md"


def blocks(text: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", text, re.S)


def test_the_tutorial_module_conforms(tmp_path):
    text = TUTORIAL.read_text(encoding="utf-8")
    body = text[:text.index("## 7. More than one platform")]
    manifest, = blocks(body, "toml")
    module, runner = blocks(body, "python")
    result_schema, count_schema, golden = blocks(body, "json")
    files = {"oarbank-module.toml": manifest, "primes_module.py": module, "primes_runner.py": runner,
             "schemas/result.schema.json": result_schema, "schemas/count.json": count_schema,
             "goldens/primes-below-100k.json": golden}
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content, encoding="utf-8", newline="\n")
    rep = conform(tmp_path)
    assert rep.ok, rep.text()
    assert not [c for c in rep.checks if c.status == "warn"], rep.text()
