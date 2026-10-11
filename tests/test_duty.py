"""The GPU duty governor (oarbank_sdk.duty): it holds the asked share of time over many units, follows a new duty at
once, holds at duty 0 until a newer document, stops while idling, sizes units with scale(), and reports what it did."""
import json

import pytest

from oarbank_sdk import gpu
from oarbank_sdk.control import Stopped
from oarbank_sdk.duty import Duty


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeControl:
    """A control document the test changes; a change is 'nudged' until polled."""

    def __init__(self, duty=None):
        self.gpu_duty, self.stop, self._pending, self.script = duty, False, None, []

    def send(self, **doc):
        self._pending = doc

    def poll(self):
        if self._pending is None:
            return False
        for k, v in self._pending.items():
            setattr(self, k, v)
        self._pending = None
        return True

    def check(self):
        self.poll()
        if self.stop:
            raise Stopped()

    def wait(self):
        if self.script:
            self.send(**self.script.pop(0))
        if not self.poll():
            raise AssertionError("waited with nothing to come")
        if self.stop:
            raise Stopped()


def work(clock, d, seconds):
    with d.unit():
        clock.t += seconds


@pytest.mark.parametrize("duty", [0.1, 0.25, 0.5, 0.9])
def test_the_governor_holds_the_asked_share_of_time(duty):
    c = Clock()
    d = Duty(FakeControl(duty), clock=c, sleep=c.sleep)
    for k in range(400):
        work(c, d, 0.01 + 0.002 * (k % 5))
    assert d.busy_s / c.t == pytest.approx(duty, abs=0.01)
    assert d.idle_s == pytest.approx(c.t - d.busy_s, abs=1e-6)


def test_no_duty_runs_flat_out_and_a_new_duty_applies_while_idling():
    c = Clock()
    ctl = FakeControl(None)
    d = Duty(ctl, clock=c, sleep=c.sleep)
    for _ in range(10):
        work(c, d, 0.05)
    assert c.t == pytest.approx(0.5) and d.idle_s == 0
    ctl.gpu_duty = 0.1
    t0 = c.t
    ctl_send_at = []

    def sleep(s):
        c.t += s
        if not ctl_send_at:
            ctl.send(gpu_duty=0.5)                       # the agent raises the duty in the first idle slice
            ctl_send_at.append(c.t)
    d.sleep = sleep
    work(c, d, 0.4)                                    # at 0.1 it would idle 3.6 s; at 0.5 it idles 0.4 s
    assert c.t - t0 == pytest.approx(0.8, abs=0.26)


def test_duty_zero_holds_until_a_newer_document_and_a_stop_while_idle_raises():
    c = Clock()
    ctl = FakeControl(0.0)
    ctl.script = [{"gpu_duty": 0.5}]
    d = Duty(ctl, clock=c, sleep=c.sleep)
    work(c, d, 0.1)                                    # held first, then ran at 0.5
    assert ctl.gpu_duty == 0.5 and d.units == 1
    ctl.gpu_duty = 0.05
    ctl.send(stop=True)

    def sleep(s):
        c.t += s
    d.sleep = sleep
    with pytest.raises(Stopped):
        work(c, d, 1.0)                               # 19 s of idle; the stop is seen in the first slice
    assert c.t < 1.0 + 0.3


def test_scale_says_how_much_smaller_the_next_units_should_be():
    c = Clock()
    d = Duty(FakeControl(0.2), clock=c, sleep=c.sleep)
    assert d.scale() == 1.0
    for _ in range(4):
        work(c, d, 0.8)                                # 0.8 s units at duty 0.2: a unit should be at most 0.2 s
    assert d.scale() == pytest.approx(0.25)
    for _ in range(8):
        work(c, d, 0.1)
    assert d.scale() == 1.0


def test_reports_are_metric_events_with_the_duty_asked(tmp_path):
    c = Clock()
    ev = tmp_path / "events.ndjson"
    ctl = FakeControl(0.3)
    d = Duty(ctl, events=ev, clock=c, sleep=c.sleep, device="cpu")
    for _ in range(100):
        work(c, d, 0.02)
    ctl.gpu_duty = 0.6
    for _ in range(100):
        work(c, d, 0.02)
    d.close()
    lines = [json.loads(x) for x in ev.read_text().splitlines()]
    assert all(x["kind"] == "metric" and x["name"] == "gpu_duty" and x["data"]["device"] == "cpu" for x in lines)
    for x in lines:
        assert x["value"] == pytest.approx(x["data"]["asked"], abs=0.05), x
    assert {x["data"]["asked"] for x in lines} == {0.3, 0.6}


def test_the_gpu_module_names_the_governor():
    assert gpu.Duty is Duty
    with pytest.raises(RuntimeError):
        Duty(None, duty=0.0).unit().__enter__()
