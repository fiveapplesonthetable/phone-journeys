# Phone Journeys

A small Android journey runner driven by TypeSafe AI's Jev decision API. Jev chooses actions from an explicit menu generated from the current Android UI hierarchy. Python executes the selected action and checks the outcome independently.

This is an initial prototype, not a general Android agent. It supports physical phones connected through ADB, including a remote ADB server. It does not install software on the phone or change the existing streaming services.

## Journeys

| Journey | Independent success check |
| --- | --- |
| `calculator` | The runner observes `17×23` before Jev presses equals, then observes result `391`. |
| `battery` | Settings' actual Battery page is visible; a dashboard row alone is insufficient. |
| `multitask` | Calculate the expression, switch to Settings, return to Calculator, verify the result survived. |

The first version uses Android's UI hierarchy, not image perception. It offers only Calculator digits/multiply/clear/delete/equals, Battery-page navigation, opening Calculator/Settings, Home, Back, and Stop. It cannot execute arbitrary model-generated shell commands or text. It cannot send messages, modify accounts, or make purchases. App switching currently uses Android activity launch; it does not test Recents gestures, split screen, or process death.

## Requirements

- Python 3.10+ on Linux; no Python dependencies.
- Android platform-tools (`adb`).
- An unlocked phone with USB debugging enabled and authorized.
- Google Calculator or an app resolving `APP_CALCULATOR`, with the supported UI resource IDs.
- A [TypeSafe API key](https://console.typesafe.ai/keys), or a Venice key with Jev access.

## Credentials

Run in Bash. The prompt does not echo the key; the key does not appear in shell history:

```bash
mkdir -p ~/.config/phone-journeys
chmod 700 ~/.config/phone-journeys
read -rsp 'TypeSafe API key: ' jev_key
printf '\n'
(umask 077; printf 'TYPESAFE_API_KEY=%s\n' "$jev_key" > ~/.config/phone-journeys/jev.env)
unset jev_key
```

Alternatively set `TYPESAFE_API_KEY` in the process environment. For Venice use `VENICE_API_KEY` and add `--provider venice`. The config file is parsed as data, never executed as shell code. Keys stay outside this repository. Provider endpoints are fixed, and HTTP redirects are rejected.

## Run

For a remote ADB server, set `ADB_SERVER_SOCKET` to your existing lab's socket, e.g. `tcp:ADB_HOST:5037`. Omit it for a local server. If your `padb` command is a shell alias, Python does not inherit the alias: export its `ADB_SERVER_SOCKET` value instead.

```bash
python3 run.py devices
python3 run.py doctor
python3 run.py run --serial DEVICE_SERIAL --label 'Test phone' --journey calculator
python3 run.py run --serial DEVICE_SERIAL --label 'Test phone' --journey battery
python3 run.py run --serial DEVICE_SERIAL --label 'Test phone' --journey multitask
```

`doctor` makes one small real API request. Runs send a real request for each decision; there is no silent simulation or model fallback. A default run permits at most 20 actions, configurable up to 40, and stops below confidence 0.6. Confidence is a gating signal, not a guarantee of correctness. Action menus and observed state drive the model; they do not force a predetermined answer.

Only one of this runner's processes can control a given device at a time. The lock does **not** coordinate unrelated ADB tools: reserve the phone in your lab before running. Do not run a journey while somebody else is interacting with the phone. The runner leaves the final app visible for inspection.

Every run writes screenshots, observations, decisions, timings, verification checkpoints, a JSON summary, and an HTML report to `~/.local/share/phone-journeys/runs/`. Open the printed `index.html` locally. Artifacts remain private and are not committed. Screenshots may contain visible personal data. Only task-relevant Calculator fields and known Settings labels are sent to Jev; password nodes, serials, local paths, and other apps' UI contents are excluded from model state. Your chosen provider processes the submitted observations.

Watch the device using your existing phone viewer while it runs. The runner uses ADB for observation/control; it does not restart the viewer or create another phone video encoder. UIAutomator dumps and screenshots take time: video-stream latency and agent-step latency are separate measurements.

## Verification

```bash
python3 -m unittest -v
python3 smoke.py --serial DEVICE_SERIAL --label 'Test phone'
```

`smoke.py` is explicitly a **scripted adapter check**. It exercises Calculator, Battery navigation, and app-state preservation on a real device. It does not call Jev and its report is labelled `adapter-smoke`. Passing it establishes the action/observation path, not Jev's navigation ability. Unit tests use fixtures and mocked transport, not live API calls.

## Architecture

```text
ADB screenshot + UI hierarchy
             |
      structured observation + allowed actions
             |
        Jev Choice API
             |
      validate answer + confidence
             |
       execute selected action
             |
       fresh observation + code assertion
             |
        next step / run report
```

Official API documentation: [TypeSafe](https://docs.typesafe.ai/api), [Choice primitive](https://docs.typesafe.ai/primitives/choice), [Venice integration](https://venice.ai/lp/jev).

## Next steps

App-specific adapters, richer recovery, UI observation caching, run scheduling, lab device leases, recording timestamp links, Perfetto capture, and image-based perception are future work. Real Jev completion rates and costs must be measured on authenticated runs; this repository makes no model-performance claims.
