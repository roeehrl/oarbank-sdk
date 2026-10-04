"""`oarbank-sdk preview`: the toy module's pages rendered by the published renderer against fixtures."""
import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from oarbank_sdk.preview import Preview

TOY = Path(__file__).parents[1] / "examples" / "toy"


@pytest.fixture
def pv():
    p = Preview(str(TOY / "oarbank-module.toml"), port=0)
    srv = p.serve()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield p
    p.close()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, dict(r.headers), r.read().decode()


def test_preview_renders_the_overview_with_the_console_csp(pv):
    st, h, body = get(f"http://127.0.0.1:{pv.port}/")
    assert st == 200 and "Checked sums" in body and 'data-module="toy"' in body
    assert "script-src 'self'" in h["Content-Security-Policy"] and f"frame-src http://127.0.0.1:{pv.frame_port}" in h["Content-Security-Policy"]
    assert ">1,000</span>" in body                     # the module computed the view from fixture inputs
    assert "<script>" not in body


def test_bridge_and_operation_preview(pv):
    assert json.loads(get(f"http://127.0.0.1:{pv.port}/bridge/view/sums")[2])["rows"][0]["n"] == 1000
    data = urllib.parse.urlencode({"p.n": "42", "return_to": "/"}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{pv.port}/op/mod.toy.set_favorite", data=data)) as r:
        body = r.read().decode()
    assert "nothing is executed" in body and "favorite_n" in body and "42" in body


def test_frames_are_served_sandboxed_from_the_second_origin(pv):
    st, h, body = get(f"http://127.0.0.1:{pv.frame_port}/f/explorer/")
    assert st == 200 and "Toy explorer" in body
    assert h["Content-Security-Policy"].startswith("sandbox allow-scripts allow-forms;")
    with pytest.raises(urllib.error.HTTPError):
        get(f"http://127.0.0.1:{pv.frame_port}/f/explorer/..%2F..%2Foarbank-module.toml")
