"""Runner safety checks using fake devices and a fake Jev client."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import run as runner


def node(text="", *, rid="", clickable=False, enabled=True, bounds=None, klass="android.widget.TextView"):
    return {
        "text": text, "content_desc": "", "resource_id": rid, "class": klass,
        "bounds": bounds, "clickable": clickable, "enabled": enabled,
        "scrollable": False, "password": False,
    }


def battery_page():
    return {
        "foreground_activity": "com.android.settings/.Settings$BatterySettingsActivity",
        "nodes": [node("Battery", rid="com.android.settings:id/action_bar_title")],
        "serial": "fake", "screenshot": "",
    }


def settings_dashboard(with_battery_row=False):
    nodes = [node("Settings", rid="com.android.settings:id/action_bar_title")]
    if with_battery_row:
        bounds = {"left": 0, "top": 100, "right": 400, "bottom": 180,
                  "center_x": 200, "center_y": 140}
        nodes.append(node("Battery", rid="com.android.settings:id/battery_row",
                          clickable=True, bounds=bounds))
    return {"foreground_activity": "com.android.settings/.Settings", "nodes": nodes,
            "serial": "fake", "screenshot": ""}


class FakeDevice:
    observations = []
    instances = []

    def __init__(self, serial, artifact_dir):
        self.serial = serial
        self.observed = 0
        self.taps = []
        self.actions = []
        self.instances.append(self)

    def observe(self):
        value = self.observations[min(self.observed, len(self.observations) - 1)]
        self.observed += 1
        return value

    def tap(self, x, y):
        self.taps.append((x, y))

    def start_activity(self, name):
        self.actions.append(("launch", name))

    def back(self):
        self.actions.append(("back",))

    def home(self):
        self.actions.append(("home",))


class FakeClient:
    decision = None
    calls = 0
    seen_state = None

    def __init__(self, provider="typesafe"):
        pass

    def choose(self, state, criteria, instructions):
        type(self).calls += 1
        type(self).seen_state = state
        choice, confidence = type(self).decision
        return {"choice": choice, "confidence": confidence,
                "probabilities": {key: (1.0 if key == choice else 0.0) for key in criteria},
                "model": "jev-1.13.0", "usage": {}, "latency_ms": 1}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        FakeDevice.observations = []
        FakeDevice.instances = []
        FakeClient.calls = 0
        FakeClient.decision = None
        FakeClient.seen_state = None
        self.patches = [
            mock.patch.object(runner, "DATA", Path(self.temp.name)),
            mock.patch.object(runner, "AndroidDevice", FakeDevice),
            mock.patch.object(runner, "JevClient", FakeClient),
            mock.patch.object(runner.time, "sleep", lambda _: None),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def args(self, *, max_steps=2, min_confidence=0.6):
        return SimpleNamespace(serial="fake", label="test phone", journey="battery",
                               provider="typesafe", max_steps=max_steps,
                               min_confidence=min_confidence)

    def run_quietly(self, args=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run(args or self.args())

    def test_independent_stage_verification_can_pass(self):
        FakeDevice.observations = [battery_page()]
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(FakeClient.calls, 0)

    def test_settings_dashboard_does_not_count_as_battery_completion(self):
        FakeDevice.observations = [settings_dashboard(with_battery_row=True)]
        FakeClient.decision = ("stop", 0.99)
        self.assertEqual(self.run_quietly(), 1)
        self.assertEqual(FakeClient.seen_state["goal"], runner.immediate_goal(
            "battery", settings_dashboard(with_battery_row=True), runner.JOURNEYS["battery"][0][1], {}
        ))
        self.assertEqual(FakeDevice.instances[0].actions, [])

    def test_low_confidence_decision_never_executes(self):
        FakeDevice.observations = [settings_dashboard()]
        FakeClient.decision = ("open_settings", 0.54)
        self.assertEqual(self.run_quietly(), 1)
        self.assertEqual(FakeDevice.instances[0].actions, [])
        self.assertEqual(FakeDevice.instances[0].taps, [])

    def test_unknown_action_never_executes(self):
        FakeDevice.observations = [settings_dashboard()]
        FakeClient.decision = ("not-an-action", 0.99)
        self.assertEqual(self.run_quietly(), 1)
        self.assertEqual(FakeDevice.instances[0].actions, [])
        self.assertEqual(FakeDevice.instances[0].taps, [])

    def test_changed_screen_refuses_stale_tap(self):
        FakeDevice.observations = [settings_dashboard(with_battery_row=True), settings_dashboard()]
        FakeClient.decision = ("tap_1", 0.99)
        self.assertEqual(self.run_quietly(), 1)
        self.assertEqual(FakeDevice.instances[0].observed, 2)
        self.assertEqual(FakeDevice.instances[0].taps, [])


if __name__ == "__main__":
    unittest.main()
