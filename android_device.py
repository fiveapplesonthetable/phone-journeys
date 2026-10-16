"""Small, non-destructive ADB adapter for observing and driving Android UI journeys."""

from __future__ import annotations

import re
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Sequence


class AndroidDeviceError(RuntimeError):
    """ADB command failed or returned unusable device data."""


def discover_devices(adb: str = "adb", timeout: float = 20) -> list[dict[str, str]]:
    """Return attached device records from `adb devices -l`."""
    try:
        result = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AndroidDeviceError(f"Could not run adb devices -l: {exc}") from exc
    if result.returncode:
        raise AndroidDeviceError(result.stderr.strip() or "adb devices -l failed")
    devices = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            record = {"serial": parts[0]}
            for item in parts[2:]:
                key, sep, value = item.partition(":")
                if sep:
                    record[key] = value
            devices.append(record)
    return devices


_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_hierarchy(xml_data: str | bytes) -> list[dict[str, Any]]:
    """Parse uiautomator XML into a flat list, redacting text from password nodes."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise AndroidDeviceError(f"Invalid UI hierarchy XML: {exc}") from exc
    nodes: list[dict[str, Any]] = []
    for element in root.iter("node"):
        attrs = element.attrib
        password = attrs.get("password", "false").lower() == "true"
        bounds_match = _BOUNDS.fullmatch(attrs.get("bounds", ""))
        bounds = None
        if bounds_match:
            x1, y1, x2, y2 = (int(value) for value in bounds_match.groups())
            bounds = {"left": x1, "top": y1, "right": x2, "bottom": y2,
                      "center_x": (x1 + x2) // 2, "center_y": (y1 + y2) // 2}
        text = attrs.get("text", "")
        description = attrs.get("content-desc", "")
        if password:
            text = "[REDACTED]" if text else ""
            description = "[REDACTED]" if description else ""
        nodes.append({
            "text": text,
            "content_desc": description,
            "resource_id": attrs.get("resource-id", ""),
            "class": attrs.get("class", ""),
            "bounds": bounds,
            "clickable": attrs.get("clickable", "false").lower() == "true",
            "enabled": attrs.get("enabled", "false").lower() == "true",
            "scrollable": attrs.get("scrollable", "false").lower() == "true",
            "password": password,
        })
    return nodes


class AndroidDevice:
    """Issue bounded ADB commands for one explicitly selected connected device."""

    ALLOWED_ACTIVITIES = {
        "settings": ("component", "com.android.settings/.Settings"),
        "calculator": ("intent", "android.intent.action.MAIN",
                       "android.intent.category.APP_CALCULATOR"),
    }

    def __init__(self, serial: str, artifact_dir: str | Path,
                 adb: str = "adb", timeout: float = 20):
        if not serial or not isinstance(serial, str):
            raise ValueError("serial must be supplied from runtime device discovery")
        self.serial = serial
        self.adb = adb
        self.timeout = timeout
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        attached = {device["serial"] for device in discover_devices(adb, timeout)}
        if serial not in attached:
            raise AndroidDeviceError(f"Device {serial!r} is not in `adb devices -l`")

    def _run(self, args: Sequence[str], *, binary: bool = False) -> bytes | str:
        command = [self.adb, "-s", self.serial, *args]
        try:
            result = subprocess.run(command, capture_output=True, timeout=self.timeout,
                                    check=False)
        except subprocess.TimeoutExpired as exc:
            raise AndroidDeviceError(f"ADB command timed out after {self.timeout}s") from exc
        except OSError as exc:
            raise AndroidDeviceError(f"Could not run adb: {exc}") from exc
        if result.returncode:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise AndroidDeviceError(stderr or f"ADB command failed ({result.returncode})")
        return result.stdout if binary else result.stdout.decode("utf-8", "replace")

    def save_screenshot(self, path: str | Path | None = None) -> Path:
        target = (Path(path) if path is not None else
                  self.artifact_dir / f"screen-{uuid.uuid4().hex}.png")
        if path is not None and not target.is_absolute():
            target = self.artifact_dir / target
        target.parent.mkdir(parents=True, exist_ok=True)
        data = self._run(["exec-out", "screencap", "-p"], binary=True)
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AndroidDeviceError("Device screencap did not return a PNG")
        target.write_bytes(data)
        return target

    def _hierarchy(self) -> list[dict[str, Any]]:
        remote_path = f"/data/local/tmp/phone-journey-ui-{uuid.uuid4().hex}.xml"
        try:
            self._run(["shell", "uiautomator", "dump", "--compressed", remote_path])
            xml_data = self._run(["exec-out", "cat", remote_path])
            return parse_hierarchy(xml_data)
        finally:
            # Cleanup is best effort, including when the dump or read fails.
            try:
                self._run(["shell", "rm", "-f", remote_path])
            except AndroidDeviceError:
                pass

    def observe(self) -> dict[str, Any]:
        activity_text = self._run(["shell", "dumpsys", "activity", "activities"])
        resumed = re.search(
            r"(?:mResumedActivity|topResumedActivity|ResumedActivity)\s*[:=].*?"
            r"(?:u\d+\s+)?([\w.$]+/[^\s}]+)", activity_text)
        focused = re.search(r"mFocusedActivity\s*[:=].*?(?:u\d+\s+)?([\w.$]+/[^\s}]+)",
                            activity_text)
        if resumed is None and focused is None:
            window_text = self._run(["shell", "dumpsys", "window", "windows"])
            focused = re.search(
                r"(?:mCurrentFocus|mFocusedApp)\s*=?.*?(?:u\d+\s+)?([\w.$]+/[^\s}]+)",
                window_text)
        wm = self._run(["shell", "wm", "size"])
        sizes = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", wm)
        dimensions = {"width": int(sizes[-1][0]), "height": int(sizes[-1][1])} if sizes else None
        screenshot = self.save_screenshot()
        nodes = self._hierarchy()
        return {
            "serial": self.serial,
            "foreground_activity": (resumed or focused).group(1) if (resumed or focused) else None,
            "screen": dimensions,
            "screenshot": str(screenshot),
            "nodes": nodes,
        }

    def home(self) -> None:
        self._run(["shell", "input", "keyevent", "KEYCODE_HOME"])

    def back(self) -> None:
        self._run(["shell", "input", "keyevent", "KEYCODE_BACK"])

    def recents(self) -> None:
        self._run(["shell", "input", "keyevent", "KEYCODE_APP_SWITCH"])

    def start_activity(self, name: str) -> None:
        launch = self.ALLOWED_ACTIVITIES.get(name.lower())
        if launch is None:
            raise ValueError(f"Activity must be one of: {', '.join(self.ALLOWED_ACTIVITIES)}")
        if launch[0] == "component":
            output = self._run(["shell", "am", "start", "-n", launch[1]])
        else:
            _, action, category = launch
            output = self._run(["shell", "am", "start", "-a", action, "-c", category])
        if re.search(r"(?:^|\n)\s*(?:Error|Exception):", output, re.IGNORECASE):
            raise AndroidDeviceError(f"Could not start {name}: {output.strip()}")

    def tap(self, x: int, y: int) -> None:
        self._run(["shell", "input", "tap", str(int(x)), str(int(y))])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> None:
        if duration_ms <= 0:
            raise ValueError("duration_ms must be positive")
        self._run(["shell", "input", "swipe", str(int(x1)), str(int(y1)),
                   str(int(x2)), str(int(y2)), str(int(duration_ms))])
