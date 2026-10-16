import unittest
from journeys import available_actions, execute, immediate_goal, model_observation, stage_verified


def node(rid, text='', clickable=True, cls='android.widget.Button'):
    return {'resource_id': rid, 'text': text, 'content_desc': text, 'enabled': True,
            'password': False, 'clickable': clickable, 'class': cls,
            'bounds': {'left': 0, 'top': 0, 'right': 100, 'bottom': 100, 'center_x': 50, 'center_y': 50}}


class JourneyTests(unittest.TestCase):
    def calc(self, *nodes):
        return {'foreground_activity': 'com.google.android.calculator/com.android.calculator2.Calculator', 'nodes': list(nodes)}

    def test_result_preview_does_not_pass_without_equals_on_expression(self):
        observation = self.calc(node('com.google.android.calculator:id/result_preview', '391'))
        self.assertFalse(stage_verified('calculate', observation, {}))
        self.assertTrue(stage_verified('calculate', observation, {'equals_on_expression': True}))

    def test_goal_uses_observed_prefix_and_handles_model_typo(self):
        def goal(formula):
            return immediate_goal('calculate', self.calc(node('com.google.android.calculator:id/formula', formula)), 'Compute',
                                  {'calculator_initialized': True})
        self.assertIn('next symbol: 2', goal('17×'))
        self.assertIn('Delete the incorrect', goal('17×1'))
        self.assertIn('Press equals', goal('17×23'))
        self.assertIn('all clear', immediate_goal('calculate', self.calc(), 'Compute'))

    def test_battery_goal_distinguishes_navigation_from_settings_changes(self):
        goal = immediate_goal('battery', {'foreground_activity': 'com.android.settings/.Settings'}, 'Read only')
        self.assertIn('clickable row containers', goal)
        self.assertIn('does not change any setting', goal)

    def test_typed_answer_cannot_establish_expression_evidence(self):
        class Device:
            def tap(self, x, y): pass
        evidence = {}
        for formula, expected in [('391', False), ('17×23', True)]:
            execute(Device(), {'kind': 'tap', 'resource_id': 'eq', 'x': 1, 'y': 1},
                    self.calc(node('com.google.android.calculator:id/formula', formula)), evidence)
            self.assertEqual(evidence['equals_on_expression'], expected)

    def test_settings_dashboard_row_does_not_pass_battery(self):
        observation = {'foreground_activity': 'com.android.settings/.Settings', 'nodes': [node('android:id/title', 'Battery')]}
        self.assertFalse(stage_verified('battery', observation, {}))
        observation['foreground_activity'] = 'com.android.settings/.Settings$PowerUsageSummaryActivity'
        observation['nodes'].append(node('com.android.settings:id/collapsing_toolbar', 'Battery'))
        self.assertTrue(stage_verified('battery', observation, {}))
        observation['nodes'][-1]['text'] = ''
        observation['nodes'][-1]['content_desc'] = 'Battery'
        self.assertTrue(stage_verified('battery', observation, {}))

    def test_only_known_calculator_controls_are_executable(self):
        actions = available_actions(self.calc(node('com.google.android.calculator:id/digit_1', '1'),
                                             node('android:id/unknown', 'Delete account')), 'calculate')
        taps = [a for a in actions.values() if a['kind'] == 'tap']
        self.assertEqual([a['resource_id'] for a in taps], ['digit_1'])

    def test_foreign_app_contents_and_local_metadata_not_transmitted(self):
        observation = {'serial': 'private', 'screenshot': '/private/screen.png',
                       'foreground_activity': 'com.example.messages/.Inbox', 'nodes': [node('id', 'private message')]}
        self.assertEqual(model_observation(observation), {'foreground_package': 'com.example.messages', 'ui': []})

    def test_settings_personal_values_not_transmitted(self):
        observation = {'foreground_activity': 'com.android.settings/.Settings',
                       'nodes': [node('android:id/title', 'Battery'), node('android:id/summary', 'private@example.com')]}
        self.assertEqual([n['text'] for n in model_observation(observation)['ui']], ['Battery'])

    def test_edits_invalidate_equation_evidence_but_settings_navigation_does_not(self):
        class Device:
            def tap(self, x, y): pass
        evidence = {'equals_on_expression': True}
        action = {'kind': 'tap', 'resource_id': 'title', 'x': 1, 'y': 1}
        execute(Device(), action, {'foreground_activity': 'com.android.settings/.Settings'}, evidence)
        self.assertTrue(evidence['equals_on_expression'])
        execute(Device(), dict(action, resource_id='digit_1'), self.calc(), evidence)
        self.assertFalse(evidence['equals_on_expression'])


if __name__ == '__main__':
    unittest.main()
