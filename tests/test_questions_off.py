"""gate/project.json "questions": "off": the gate decides by measurement alone. No question is asked, a finding that
would ask is an open row, `attest answer` is refused, and the same input gives the same verdict every time."""
import json, os, sys, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_attest, gate_common, gate_plan
from test_attest import Asked, BRACKET, UNKNOWN_HOLE, COVERED


class QuestionsOff(Asked):
    def switch(self, word):
        path = gate_common.gate_path(self.root, 'project.json')
        data = gate_common.read_json(path, {})
        data['questions'] = word
        gate_common.write_json(path, data)

    def test_on_by_default_and_off_asks_nothing(self):
        self.assertFalse(gate_common.questions_off(self.root))
        self.assertTrue(self.verdict()['questions'])
        self.switch('off')
        self.assertTrue(gate_common.questions_off(self.root))
        v = self.verdict()
        self.assertEqual((v['questions'], v['ask_questions'], v['asked']), ([], [], []))
        self.assertNotEqual(v['verdict'], 'attest')
        self.assertEqual(gate_attest.open_questions(self.root, self.reg, [BRACKET], ('design', 'export')), [])

    def test_no_probe_is_planned_only_for_a_question(self):
        on = gate_plan._question_probes(self.reg, 'fusion')
        self.switch('off')
        self.assertEqual(gate_plan._question_probes(self.reg, 'fusion'), set())
        self.assertIsInstance(on, set)

    def test_a_wrong_word_is_refused(self):
        self.switch('maybe')
        with self.assertRaises(gate_common.GateError):
            gate_common.load_project(self.root)

    def test_a_finding_that_would_ask_is_an_open_row(self):
        self.demo.facts('fusion_census', BRACKET, UNKNOWN_HOLE); self.demo.facts('fusion_openings', BRACKET, COVERED)
        self.switch('off')
        v = self.geometry('v2')
        self.assertEqual(v['asked'], [])
        self.assertEqual(sorted(f['rule'] for f in v['open']), ['access.hole_covered'])          # an INFO that asked stays INFO
        self.assertIn('census.unknown_hole', [f['rule'] for f in v['info']])
        self.assertEqual(v['verdict'], 'fail')

    def test_attest_answer_is_refused(self):
        self.switch('off')
        class A: action = 'answer'; question = 'Q-000000'; label = na = param = shot = None; target = None
        self.assertEqual(gate_attest.cmd_attest(self.root, A()), 4)

    def test_the_same_input_gives_the_same_verdict(self):
        self.switch('off')
        a, b = self.verdict(), self.verdict()
        self.assertEqual(json.dumps(a, sort_keys=True, default=str), json.dumps(b, sort_keys=True, default=str))


if __name__ == '__main__':
    unittest.main()
