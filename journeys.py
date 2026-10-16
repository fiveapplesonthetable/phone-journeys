"""Small, bounded journeys. Code verifies outcomes independently of Jev."""
from __future__ import annotations

import re

CALCULATOR_PACKAGES = {'com.google.android.calculator', 'com.android.calculator2'}

JOURNEYS = {
    'calculator': [('calculate', 'Open Calculator. Clear any old expression, enter 17 multiplied by 23, then press equals. Do not type the answer directly.')],
    'battery': [('battery', 'Open Settings and navigate to its Battery page. Read only; do not change any settings.')],
    'multitask': [
        ('calculate', 'Open Calculator. Clear any old expression, enter 17 multiplied by 23, then press equals. Do not type the answer directly.'),
        ('settings', 'Switch to the Settings application. Keep Calculator running with its result intact.'),
        ('return', 'Return to Calculator without clearing it. Verify the previous calculation survived switching apps.'),
    ],
}


def package(observation):
    return (observation.get('foreground_activity') or '').split('/')[0]


def calculator_values(observation):
    return {n['resource_id'].rsplit('/', 1)[-1]: n['text'] for n in observation['nodes']
            if 'calculator' in n['resource_id'] and n['text']}


def normalized(value):
    return re.sub(r'\s+', '', value).replace('*', '×').replace(',', '')


def immediate_goal(stage, observation, overall_goal, evidence=None):
    """Decompose a known skill into one decision; never select an action here."""
    if stage == 'calculate':
        if package(observation) not in CALCULATOR_PACKAGES:
            return 'Open Calculator. Do not perform any other task yet.'
        if not (evidence or {}).get('calculator_initialized'):
            return 'Clear the previous Calculator expression/result using all clear before starting the new calculation.'
        values = calculator_values(observation)
        formula = normalized(values.get('formula', ''))
        target = '17×23'
        if formula == target:
            return 'The exact requested expression 17×23 is entered. Press equals to submit it.'
        if target.startswith(formula):
            remaining = target[len(formula):]
            symbol = 'multiply (×)' if remaining[0] == '×' else remaining[0]
            return (f'Target expression is {target}. Already entered prefix is {formula!r}. '
                    f'Remaining input is {remaining!r}. Enter only the next symbol: {symbol}. '
                    'Keep the existing prefix; do not re-enter it. Do not enter the computed answer.')
        return (f'Target expression is {target}, but current expression {formula!r} diverges. '
                'Delete the incorrect trailing input, or clear if necessary, to restore a valid prefix.')
    if stage == 'battery':
        if package(observation) != 'com.android.settings':
            return 'Open the Settings application to begin viewing its Battery page.'
        return ('Open the Battery page by selecting its navigation row. This only views a page; '
                'it does not change any setting. Available tap actions refer to clickable row containers, '
                'even when their child text label is not itself clickable.')
    return overall_goal


def stage_verified(stage, observation, evidence):
    pkg = package(observation)
    if stage in ('calculate', 'return'):
        values = calculator_values(observation)
        # Result preview while typing is insufficient: equals must have been sent
        # after the exact requested expression, then independently observed.
        result = any(normalized(v) == '391' for k, v in values.items() if 'result' in k)
        return pkg in CALCULATOR_PACKAGES and result and evidence.get('equals_on_expression', False)
    if stage == 'settings':
        return pkg == 'com.android.settings'
    if stage == 'battery':
        # A dashboard row labelled Battery alone does not establish arrival.
        headings = [(n['text'] or n['content_desc']).strip().lower() for n in observation['nodes']
                    if n['resource_id'].endswith(('/collapsing_toolbar', '/action_bar_title', '/toolbar_title'))]
        return pkg == 'com.android.settings' and ('Battery' in observation.get('foreground_activity', '')
                                                  or 'battery' in headings)
    return False


def available_actions(observation, stage):
    actions = {
        'open_calculator': {'kind': 'launch', 'target': 'calculator', 'label': 'Open or return to Calculator; preserve its state'},
        'open_settings': {'kind': 'launch', 'target': 'settings', 'label': 'Open Settings; read only'},
        'back': {'kind': 'back', 'label': 'Navigate Back'},
        'home': {'kind': 'home', 'label': 'Navigate Home'},
        'stop': {'kind': 'stop', 'label': 'Stop if the goal is blocked or no suitable action is available'},
    }
    pkg = package(observation)
    for i, n in enumerate(observation['nodes']):
        if not n['enabled'] or not n['clickable'] or n['password'] or not n['bounds']:
            continue
        rid = n['resource_id'].rsplit('/', 1)[-1]
        allowed = False
        if pkg in CALCULATOR_PACKAGES and stage == 'calculate':
            allowed = bool(re.fullmatch(r'digit_[0-9]|op_mul|eq|clr|del', rid))
        if pkg == 'com.android.settings' and stage == 'battery':
            # Only the Battery navigation row, never toggle/charging controls.
            descendants = [c for c in observation['nodes'] if c['bounds'] and
                           c['bounds']['top'] >= n['bounds']['top'] and
                           c['bounds']['bottom'] <= n['bounds']['bottom'] and
                           c['bounds']['left'] >= n['bounds']['left'] and
                           c['bounds']['right'] <= n['bounds']['right']]
            allowed = (n['class'] not in ('android.widget.Switch', 'android.widget.CheckBox')
                       and any(c['text'].strip().lower() == 'battery' for c in descendants))
        if allowed:
            label = 'Open Battery page' if pkg == 'com.android.settings' else 'Tap Calculator button: ' + (n['text'] or n['content_desc'] or rid)
            actions[f'tap_{i}'] = {'kind': 'tap', 'label': label,
                                  'resource_id': rid, 'x': n['bounds']['center_x'], 'y': n['bounds']['center_y']}
    return actions


def model_observation(observation):
    # Never transmit serial, local paths, or arbitrary foreground app contents.
    pkg = package(observation)
    permitted = pkg == 'com.android.settings' or pkg in CALCULATOR_PACKAGES
    nodes = []
    if permitted:
        for n in observation['nodes']:
            if n['password']:
                continue
            rid = n['resource_id'].rsplit('/', 1)[-1]
            if pkg in CALCULATOR_PACKAGES and not re.fullmatch(r'digit_[0-9]|op_mul|eq|clr|del|formula|result_preview|result_final', rid):
                continue
            if pkg == 'com.android.settings' and (n['text'] or n['content_desc']).strip().lower() not in {
                'settings', 'battery', 'battery usage', 'battery saver', 'adaptive battery', 'battery percentage'
            }:
                continue
            nodes.append({k: n[k] for k in ('text', 'content_desc', 'resource_id')})
    return {'foreground_package': pkg, 'ui': nodes}


def execute(device, action, observation, evidence):
    kind = action['kind']
    if kind == 'launch':
        device.start_activity(action['target'])
    elif kind in ('home', 'back'):
        getattr(device, kind)()
    elif kind == 'tap':
        if package(observation) in CALCULATOR_PACKAGES and action.get('resource_id') == 'clr':
            evidence['calculator_initialized'] = True
        if action.get('resource_id') == 'eq':
            values = calculator_values(observation)
            formula = next((v for k, v in values.items() if 'formula' in k), '')
            evidence['equals_on_expression'] = normalized(formula) == '17×23'
        elif package(observation) in CALCULATOR_PACKAGES:
            evidence['equals_on_expression'] = False
        device.tap(action['x'], action['y'])
    elif kind != 'stop':
        raise ValueError('Unsupported action')
