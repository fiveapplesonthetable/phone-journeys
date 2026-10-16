import tempfile
import unittest
from unittest.mock import patch

from android_device import AndroidDevice, AndroidDeviceError, discover_devices, parse_hierarchy


SAMPLE = '''<?xml version="1.0" encoding="UTF-8"?>
<hierarchy><node text="" content-desc="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
  <node text="Email" content-desc="" resource-id="app:id/email" class="android.widget.EditText"
    bounds="[10,20][210,80]" clickable="true" enabled="true" scrollable="false" password="false"/>
  <node text="secret@example.com" content-desc="private" resource-id="app:id/password"
    class="android.widget.EditText" bounds="[10,100][210,160]" clickable="true" enabled="true"
    scrollable="false" password="true"/>
  <node text="List" content-desc="" class="android.view.View" bounds="bad"
    clickable="false" enabled="false" scrollable="true" password="false"/>
</node></hierarchy>'''


class ParseHierarchyTests(unittest.TestCase):
    def test_extracts_attributes_and_bounds(self):
        nodes = parse_hierarchy(SAMPLE)
        self.assertEqual(len(nodes), 4)
        self.assertEqual(nodes[1]["text"], "Email")
        self.assertEqual(nodes[1]["resource_id"], "app:id/email")
        self.assertTrue(nodes[1]["clickable"])
        self.assertEqual(nodes[1]["bounds"], {
            "left": 10, "top": 20, "right": 210, "bottom": 80,
            "center_x": 110, "center_y": 50,
        })
        self.assertTrue(nodes[3]["scrollable"])
        self.assertIsNone(nodes[3]["bounds"])

    def test_password_values_are_redacted(self):
        password = parse_hierarchy(SAMPLE)[2]
        self.assertEqual(password["text"], "[REDACTED]")
        self.assertEqual(password["content_desc"], "[REDACTED]")
        self.assertTrue(password["password"])

    def test_invalid_xml_has_adapter_error(self):
        with self.assertRaises(AndroidDeviceError):
            parse_hierarchy("<hierarchy>")


class DeviceCommandTests(unittest.TestCase):
    @patch("android_device.subprocess.run")
    def test_discovery_excludes_offline_and_unauthorized(self, run):
        run.return_value.stdout = ("List of devices attached\n"
                                  "serial-A device product:p model:Pixel_8\n"
                                  "serial-B offline\nserial-C unauthorized\n")
        run.return_value.returncode = 0
        self.assertEqual(discover_devices(), [{"serial": "serial-A", "product": "p", "model": "Pixel_8"}])
        self.assertEqual(run.call_args.args[0], ["adb", "devices", "-l"])

    @patch("android_device.discover_devices", return_value=[{"serial": "runtime-serial"}])
    def test_actions_use_serial_and_argument_arrays(self, _discover):
        with tempfile.TemporaryDirectory() as directory:
            device = AndroidDevice("runtime-serial", directory)
            with patch.object(device, "_run", return_value="Starting: Intent") as run:
                device.tap(12, 34)
                self.assertEqual(run.call_args.args[0], ["shell", "input", "tap", "12", "34"])
                device.start_activity("settings")
                self.assertEqual(run.call_args.args[0], ["shell", "am", "start", "-n",
                                                         "com.android.settings/.Settings"])
                device.start_activity("calculator")
                self.assertEqual(run.call_args.args[0], [
                    "shell", "am", "start", "-a", "android.intent.action.MAIN",
                    "-c", "android.intent.category.APP_CALCULATOR",
                ])
                with self.assertRaises(ValueError):
                    device.start_activity("arbitrary.package/.Activity")

    @patch("android_device.discover_devices", return_value=[{"serial": "runtime-serial"}])
    def test_am_start_output_error_is_reported(self, _discover):
        with tempfile.TemporaryDirectory() as directory:
            device = AndroidDevice("runtime-serial", directory)
            with patch.object(device, "_run", return_value="Error: Activity not found"):
                with self.assertRaises(AndroidDeviceError):
                    device.start_activity("calculator")

    @patch("android_device.discover_devices", return_value=[{"serial": "runtime-serial"}])
    def test_observe_supports_top_resumed_and_window_focus_fallback(self, _discover):
        with tempfile.TemporaryDirectory() as directory:
            device = AndroidDevice("runtime-serial", directory)
            calls = []

            def run(args, **kwargs):
                calls.append(args)
                if args[:3] == ["shell", "dumpsys", "activity"]:
                    return "topResumedActivity=ActivityRecord{a u0 com.example/.Main t1}"
                if args == ["shell", "wm", "size"]:
                    return "Physical size: 1080x2400"
                return None

            with patch.object(device, "_run", side_effect=run), \
                    patch.object(device, "save_screenshot", return_value="screen.png"), \
                    patch.object(device, "_hierarchy", return_value=[]):
                observation = device.observe()
            self.assertEqual(observation["foreground_activity"], "com.example/.Main")
            self.assertNotIn(["shell", "dumpsys", "window", "windows"], calls)

            def fallback_run(args, **kwargs):
                if args[:3] == ["shell", "dumpsys", "activity"]:
                    return "no activity record available"
                if args[:3] == ["shell", "dumpsys", "window"]:
                    return "mCurrentFocus=Window{abc u0 com.example/.Main}"
                if args == ["shell", "wm", "size"]:
                    return "Override size: 1080x2340"
                return None

            with patch.object(device, "_run", side_effect=fallback_run), \
                    patch.object(device, "save_screenshot", return_value="screen.png"), \
                    patch.object(device, "_hierarchy", return_value=[]):
                observation = device.observe()
            self.assertEqual(observation["foreground_activity"], "com.example/.Main")
            self.assertEqual(observation["screen"], {"width": 1080, "height": 2340})


if __name__ == "__main__":
    unittest.main()
