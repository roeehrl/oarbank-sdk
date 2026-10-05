# Adding a row to the compatibility table

[`reports.json`](reports.json) holds the rows of the hardware compatibility table, [`../compatibility.md`](../compatibility.md),
which the docs site publishes. Edit the data, never the page: `scripts/gen_compatibility.py` checks the data against
[`reports.schema.json`](reports.schema.json) and its own rules, then writes the page, and `tests/test_compatibility.py`
fails while the page is stale.

## From a hardware report

Contributors report with the core's issue form, `.github/ISSUE_TEMPLATE/hardware-report.yml` in
[roeehrl/oarbank](https://github.com/roeehrl/oarbank). Its fields are a row's fields, so a report becomes a draft row
mechanically. In a checkout of the core, with this repository as its `vendor/oarbank-sdk`:

```
uv run python scripts/compat-row.py 42
```

It reads issue 42 with `gh issue view`, prints the row, and lists on stderr anything it could not read and anything
the checks here refuse. Then:

1. **Read the output the reporter pasted.** The status is the reporter's; set it from the evidence. `verified` needs
   every test they ran to have passed or skipped itself, and `gpu_apis` to match what the machine has (their
   `vulkaninfo --summary`, `clinfo -l`, `nvidia-smi` or `rocminfo`). An API reported that does not work, one missed
   that works, or a failed test is `partial`; a path that stops is `not-working`.
2. **Write `notes`** in plain words: what works, what is wrong and where it stopped. One line, no `|`, no output dumps.
   `partial` and `not-working` rows must have notes.
3. **Check for personal data** once more. A row holds no names but the GitHub handle the issue was posted under (in
   `source.reporter`), and no host names, user names, paths, serial numbers, addresses or locations. The checks refuse
   home directory paths, e-mail and MAC addresses, but cannot see everything. If the issue itself shows any, hide or edit
   it there too.
4. **Add the row** to `reports`. Remove a `wanted` entry only when its issue is closed; while it is open, more machines
   of that kind are still welcome.
5. **Regenerate and test**, then commit both files with a sign-off:

   ```
   uv run python scripts/gen_compatibility.py
   uv run pytest -q tests/test_compatibility.py
   git commit -s -m "Compatibility: GeForce RTX 4070 on Ubuntu 24.04 (oarbank#42)"
   ```

## Reports that are not issue forms

A report posted as a comment on one of the help-wanted issues (#10 to #16) has the same facts in free form. Write the
row by hand from them: `source.kind` is `issue`, `source.url` the comment's own link (`…/issues/12#issuecomment-…`) and
`source.reporter` its author. A maintainers' run is `maintainers`, linked to the commit that records it; a CI job is
`ci`, linked to the job (GitHub deletes CI logs after the retention period; the row's commit and date still say what
ran).

## A new gated test

A test the table can name is an entry in `tests`: its id, the exact command, what it shows and the issues that ask for
it. The core's form has a dropdown per test, labelled `Test: <id>` with the command in its description, and the core's
`tests/test_compat_row.py` fails until the two agree: add the entry here and the dropdown there in the same change.
