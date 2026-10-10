"""Check the Oarbank agent skill (skills/oarbank) and its plugin manifests.

    python scripts/check_skill.py --llms site/deploy/oarbank/llms.txt [--base <git ref>] [--links]

- The skill follows the Agent Skills format with standard fields only (so claude.ai uploads and every installer
  accept it): `name` matches its folder, `description` is 1-1024 characters, the body stays under 500 lines.
- `metadata.version` equals the version in .claude-plugin/plugin.json, and the plugin and marketplace entry are
  named like the skill.
- Every docs page and llms set the skill routes to is listed in llms.txt (pass the built one in CI, the live one
  in the freshness check), so renaming or removing a page the skill names fails the build.
- With --base: a change to the skill since that ref must come with a new version, or installed copies never update.
- With --links: every absolute URL in the skill answers (network; the freshness check only).

Exit 1 with one line per problem.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "oarbank"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
DOCS = "https://docs.codonic.dev/oarbank/"
STANDARD = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def frontmatter(text):
    """The YAML frontmatter as {key: str | {key: str}}: enough for the flat shape the spec allows."""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None, text
    fields, current = {}, None
    for line in m.group(1).splitlines():
        if line.startswith("  ") and current is not None:
            k, _, v = line.strip().partition(":")
            fields[current][k.strip()] = v.strip().strip('"')
        else:
            k, _, v = line.partition(":")
            current = None
            if v.strip():
                fields[k.strip()] = v.strip().strip('"')
            else:
                current = k.strip()
                fields[current] = {}
    return fields, text[m.end():]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llms", required=True, help="an llms.txt file (built or downloaded)")
    ap.add_argument("--base", help="git ref to compare against for the version bump")
    ap.add_argument("--links", action="store_true", help="also request every absolute URL in the skill")
    a = ap.parse_args()
    problems = []

    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    fm, body = frontmatter(text)
    if fm is None:
        sys.exit("skills/oarbank/SKILL.md: no frontmatter")
    for k in sorted(set(fm) - STANDARD):
        problems.append(f"SKILL.md: '{k}' is not a standard Agent Skills field")
    if fm.get("name") != SKILL.name:
        problems.append(f"SKILL.md: name '{fm.get('name')}' does not match its folder '{SKILL.name}'")
    desc = fm.get("description", "")
    if not 1 <= len(desc) <= 1024 or "<" in desc or ">" in desc:
        problems.append(f"SKILL.md: description must be 1-1024 characters without angle brackets ({len(desc)})")
    if len(fm.get("compatibility", "")) > 500:
        problems.append("SKILL.md: compatibility is over 500 characters")
    if len(body.splitlines()) > 500:
        problems.append(f"SKILL.md: the body is {len(body.splitlines())} lines; keep it under 500")
    version = (fm.get("metadata") or {}).get("version")

    plugin = json.loads(PLUGIN.read_text(encoding="utf-8"))
    market = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    if plugin.get("version") != version:
        problems.append(f"plugin.json version {plugin.get('version')} != SKILL.md metadata.version {version}")
    if plugin.get("name") != SKILL.name:
        problems.append(f"plugin.json name '{plugin.get('name')}' != '{SKILL.name}'")
    if [p.get("name") for p in market.get("plugins", [])] != [SKILL.name]:
        problems.append(f"marketplace.json must list exactly the '{SKILL.name}' plugin")
    if any("version" in p for p in market.get("plugins", [])):
        problems.append("marketplace.json: set the version in plugin.json only")

    llms = Path(a.llms).read_text(encoding="utf-8")
    listed = set(re.findall(re.escape(DOCS) + r"([^)\s]+)\)", llms))
    routes = set(re.findall(r"`((?:[a-z0-9-]+/)*[a-z0-9-]+\.md)`", body)) - {"SKILL.md", "CONTRIBUTING.md"}
    routes |= set(re.findall(r"`(_llms-txt/[a-z0-9-]+\.txt)`", body))
    for r in sorted(routes):
        if r not in listed:
            problems.append(f"SKILL.md routes to {r}, which {a.llms} does not list")

    if a.base:
        changed = subprocess.run(["git", "diff", "--name-only", f"{a.base}...HEAD", "--", "skills/oarbank"],
                                 cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        old = subprocess.run(["git", "show", f"{a.base}:.claude-plugin/plugin.json"],
                             cwd=ROOT, capture_output=True, text=True)
        old_version = json.loads(old.stdout).get("version") if old.returncode == 0 else None
        if changed and old_version == version:
            problems.append(f"the skill changed since {a.base} but its version is still {version}: bump "
                            "metadata.version in SKILL.md and version in .claude-plugin/plugin.json")

    if a.links:
        for url in sorted({u.rstrip(".,'\"") for u in re.findall(r"https://[^\s`)…]+", text)}):
            if "<" in url:  # a template such as …/v<version>/…
                continue
            try:
                req = urllib.request.Request(url, method="GET", headers={"User-Agent": "oarbank-skill-check"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status != 200:
                        problems.append(f"{url}: HTTP {r.status}")
            except urllib.error.HTTPError as e:
                problems.append(f"{url}: HTTP {e.code}")
            except Exception as e:  # noqa: BLE001 - report any network failure as a problem
                problems.append(f"{url}: {e}")

    for p in problems:
        print(p)
    print(f"check_skill: {len(routes)} routes, version {version}: "
          f"{'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
