"""oarbank-sdk command line.

    oarbank-sdk check <oarbank-module.toml>      validate a manifest strictly (typos are errors)
    oarbank-sdk export-schemas                     regenerate schemas/ from the models
    oarbank-sdk preview <oarbank-module.toml>     render the module's pages as the console will (fixtures)
    oarbank-sdk bundle build <module-dir> [-o F]   build a digest-addressed bundle (.mfb)
    oarbank-sdk bundle verify <file.mfb>           verify a bundle's files, digest and manifest
    oarbank-sdk bundle wheels <module-dir>         download the wheels for the declared platforms into wheels/
    oarbank-sdk deps compile <module-dir> <requirements.in> [-o FILE] [-- uv args]
                                                   one marker-free, hash-pinned requirements file for every platform
    oarbank-sdk conform <module-dir>               the conformance kit (manifest, bundle, protocol, runner)
"""
import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from . import manifest as m
from .schemas import export


def check_ui(path: str, man) -> list[str]:
    """Validate every page and panel file the manifest declares, and their cross-references."""
    import json
    from pathlib import Path
    from . import ui
    root = Path(path).resolve().parent
    files = {f.relative_to(root).as_posix() for f in root.rglob("*") if f.is_file()}
    errs = []
    for d in [*man.ui.pages, *man.ui.panels]:
        f = root / d.file
        if d.file not in files:
            errs.append(f"{d.id}: file {d.file} not found in the bundle")
            continue
        try:
            page = ui.Page.model_validate(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, ValidationError) as e:
            errs.append(f"{d.id} ({d.file}): {str(e).splitlines()[0] if not isinstance(e, ValidationError) else ''}")
            if isinstance(e, ValidationError):
                errs += [f"  {'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:10]]
            continue
        errs += [f"{d.id}: {x}" for x in ui.check_page(page, man.ui, man.operations, files)]
    for fr in man.ui.iframes:
        if fr.entry not in files:
            errs.append(f"iframe {fr.id}: entry {fr.entry} not found in the bundle")
    for o in man.operations:
        if o.params_schema and o.params_schema not in files:
            errs.append(f"operation {o.verb}: params_schema {o.params_schema} not found in the bundle")
        elif o.params_schema and '"x-secret"' in (root / o.params_schema).read_text(encoding="utf-8"):
            errs.append(f"operation {o.verb}: params_schema uses x-secret; a form never takes a credential: declare it in "
                        "[[secrets]] and the owner sets it (oarbank secret set)")
    return errs


def cmd_check(path: str) -> int:
    try:
        man = m.load(path)
    except ValidationError as e:
        print(f"{path}: INVALID")
        for err in e.errors():
            print(f"  {'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
        return 1
    except Exception as e:                          # unreadable TOML etc.
        print(f"{path}: cannot read: {e}")
        return 1
    ui_errors = check_ui(path, man)
    if ui_errors:
        print(f"{path}: INVALID (UI contract)")
        for e in ui_errors:
            print(f"  {e}")
        return 1
    from . import images
    key_errors = []
    for cs in man.sandbox.container_sets:
        try:
            images.load_key(Path(path).parent, cs)
        except images.ImageError as e:
            key_errors.append(str(e))
    if key_errors:
        print(f"{path}: INVALID (container set keys)")
        for e in key_errors:
            print(f"  {e}")
        return 1
    extra = m.unknown_fields(man)
    if extra:
        print(f"{path}: INVALID (unknown fields: likely typos, or items newer than manifest schema 1)")
        for x in extra:
            print(f"  {x}")
        return 1
    for w in m.lint(man):
        print(f"{path}: warning: {w}")
    if man.requires.experimental:
        print(f"{path}: ok (experimental opt-ins {man.requires.experimental} block the verified badge)")
    else:
        print(f"{path}: ok  {man.module.id} {man.module.version} compat={man.module.compat} stages={[s.name for s in man.stages]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oarbank-sdk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate manifests strictly")
    c.add_argument("paths", nargs="+")
    sub.add_parser("export-schemas", help="regenerate schemas/ from the models")
    pv = sub.add_parser("preview", help="render the module's pages as the console will, against fixtures")
    pv.add_argument("manifest")
    pv.add_argument("--port", type=int, default=8700)
    b = sub.add_parser("bundle", help="build or verify module bundles")
    bs = b.add_subparsers(dest="bcmd", required=True)
    bb = bs.add_parser("build")
    bb.add_argument("dir")
    bb.add_argument("-o", "--out")
    bv = bs.add_parser("verify")
    bv.add_argument("file")
    bw = bs.add_parser("wheels", help="download the pinned wheels for every declared platform into wheels/")
    bw.add_argument("dir")
    bw.add_argument("--python-version", default="3.12")
    dp = sub.add_parser("deps", help="a module's Python dependencies")
    ds = dp.add_subparsers(dest="dcmd", required=True)
    dc = ds.add_parser("compile", help="resolve a requirements.in for every platform that installs it into one file")
    dc.add_argument("dir")
    dc.add_argument("src")
    dc.add_argument("-o", "--out", help="the requirements file (default: requirements.txt beside the input)")
    dc.add_argument("uv_args", nargs=argparse.REMAINDER, help="after --: passed to uv pip compile (e.g. --index-url)")
    cf = sub.add_parser("conform", help="run the conformance kit against a module directory")
    cf.add_argument("dir")
    cf.add_argument("--fixtures", help="fixtures JSON (default: <dir>/conformance.json if present)")
    cf.add_argument("--no-runner", action="store_true", help="skip the runner suite")
    cf.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "conform":
        import json as _json
        from .conformance import conform
        from pathlib import Path as _P
        rep = conform(a.dir, _json.loads(_P(a.fixtures).read_text(encoding="utf-8")) if a.fixtures else None, runner=not a.no_runner)
        print(_json.dumps(rep.as_dict(), indent=1) if a.json else rep.text())
        return 0 if rep.ok else 1
    if a.cmd == "deps":
        from pathlib import Path as _P
        from . import deps, manifest as _mf
        try:
            rep = deps.compile(_P(a.dir), _mf.load(_P(a.dir) / "oarbank-module.toml"), _P(a.src), _P(a.out) if a.out else None,
                               tuple(x for x in a.uv_args if x != "--"))
        except deps.DepsError as e:
            print(f"deps compile: {e}")
            return 1
        print(f"{rep['out']}: {rep['pins']} pins for {', '.join(rep['platforms'])}")
        for name, plats in rep["partial"].items():
            print(f"  {name}: needed on {', '.join(plats)} (installed everywhere)")
        return 0
    if a.cmd == "bundle":
        from . import bundle as B
        try:
            if a.bcmd == "wheels":
                from pathlib import Path as _P
                from . import deps, manifest as _mf
                man = _mf.load(_P(a.dir) / "oarbank-module.toml")
                plats = deps.download(_P(a.dir), man, a.python_version)
                left = deps.check(_P(a.dir), man)
                print(f"wheels/: fetched for {', '.join(plats) or 'nothing (no requirements)'}")
                for i in left:
                    print(f"  {i}")
                return 1 if left else 0
            if a.bcmd == "build":
                out, info = B.build(a.dir, a.out)
                print(f"{out}  {info.module_id} {info.version}  {info.content_digest}  {len(info.files)} files")
                for w in B.platform_lint(info.manifest, [f["path"] for f in info.files]):
                    print(f"  warning: {w}")
            else:
                info = B.verify(a.file)
                print(f"{a.file}: ok  {info.module_id} {info.version}  {info.content_digest}")
        except (B.BundleError, ValueError) as e:
            print(f"bundle: {e}")
            return 1
        return 0
    if a.cmd == "check":
        return max(cmd_check(p) for p in a.paths)
    if a.cmd == "preview":
        from .preview import main as preview_main
        return preview_main(a.manifest, a.port)
    if a.cmd == "export-schemas":
        w = export()
        print(f"{len(w)} schema(s) updated" + (": " + ", ".join(w) if w else ""))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
