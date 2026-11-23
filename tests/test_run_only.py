"""gate.py run --only PROBES: a partial run measures only the named probes (a fast check of one kind of change)."""
import os, sys, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse, gate_common, gate_plan
from test_attest import Asked, BRACKET


class RunOnly(Asked):
    def test_names_with_and_without_the_prefix(self):
        both = gate_plan.only_probes(self.reg, ['thin', 'fusion_inserts'])
        self.assertEqual(both, {'fusion_thin', 'fusion_inserts'})

    def test_an_unknown_probe_is_refused(self):
        with self.assertRaises(gate_common.GateError):
            gate_plan.only_probes(self.reg, ['nonsense'])

    def test_a_partial_plan_holds_only_those_units(self):
        full = gate_plan.new_plan(self.root, self.reg, [BRACKET])
        part = gate_plan.new_plan(self.root, self.reg, [BRACKET], only={'fusion_thin'})
        self.assertGreater(len(full['units']), 1)
        self.assertEqual({u['probe'] for u in part['units']}, {'fusion_thin'})

    def test_the_request_tells_a_partial_run_from_a_full_one(self):
        a = argparse.Namespace(part=[BRACKET], all=False, audit=False, only=None)
        b = argparse.Namespace(part=[BRACKET], all=False, audit=False, only=['thin'])
        self.assertNotEqual(gate_plan._request(a), gate_plan._request(b))
        self.assertEqual(gate_plan._request(b)['only'], ['thin'])


if __name__ == '__main__':
    unittest.main()
