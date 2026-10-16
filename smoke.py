#!/usr/bin/env python3
"""Explicitly scripted real-device adapter check. Does NOT call Jev."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid

from android_device import AndroidDevice
from journeys import available_actions, execute, stage_verified
from report import write_report
from run import DATA, now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--label', default='Android device')
    args = parser.parse_args()
    os.umask(0o077)
    directory = DATA / 'runs' / ('adapter-smoke-' + uuid.uuid4().hex[:8])
    d = AndroidDevice(args.serial, directory)
    summary = {'journey': 'Calculator and cross-app adapter check', 'driver': 'adapter-smoke',
               'device_label': args.label, 'started_at': now(), 'status': 'running', 'stages': []}
    steps, evidence = [], {}

    def observe(stage):
        start = time.monotonic()
        o = d.observe()
        steps.append({'step': len(steps), 'stage': stage, 'observation': o,
                      'observation_ms': round(1000 * (time.monotonic() - start))})
        return o

    try:
        d.start_activity('calculator')
        o = observe('calculate')
        # Resample bounds after every action, like the actual Jev runner.
        for rid in ['clr', 'digit_1', 'digit_7', 'op_mul', 'digit_2', 'digit_3', 'eq']:
            action = next(a for a in available_actions(o, 'calculate').values() if a.get('resource_id') == rid)
            steps[-1]['action'] = action
            execute(d, action, o, evidence)
            o = observe('calculate')
        if not stage_verified('calculate', o, evidence):
            raise RuntimeError('Actual calculator result did not verify as 391 after 17×23 and equals')
        summary['stages'].append({'stage': 'calculate', 'status': 'passed'})
        d.start_activity('settings')
        o = observe('settings')
        if not stage_verified('settings', o, evidence):
            raise RuntimeError('Settings did not become foreground')
        summary['stages'].append({'stage': 'settings', 'status': 'passed'})
        actions = available_actions(o, 'battery')
        battery = next((a for a in actions.values() if a['kind'] == 'tap'), None)
        if battery:
            steps[-1]['action'] = battery
            execute(d, battery, o, evidence)
            o = observe('battery')
            if not stage_verified('battery', o, evidence):
                raise RuntimeError('Battery page did not verify after navigation')
            summary['stages'].append({'stage': 'battery', 'status': 'passed'})
        else:
            raise RuntimeError('Battery navigation row not visible')
        d.start_activity('calculator')
        o = observe('return')
        if not stage_verified('return', o, evidence):
            raise RuntimeError('Calculator result did not survive switching apps')
        summary['stages'].append({'stage': 'return', 'status': 'passed'})
        summary['status'] = 'passed'
    except Exception as exc:
        summary.update(status='failed', error=str(exc))
    summary['finished_at'] = now()
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2))
    (directory / 'steps.json').write_text(json.dumps(steps, indent=2))
    report = write_report(directory, summary, steps)
    print(json.dumps({'status': summary['status'], 'error': summary.get('error'), 'report': str(report)}))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
