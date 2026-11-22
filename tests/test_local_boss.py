"""inserts.local_boss (0.3.3): a heat-set pocket whose wall of the target stands only on a round boss is asked why the
whole wall did not grow. The judge reads the rays inserts.wall already stores; its cases are the fixtures
tests/fixtures/facts/inserts_local_boss__*.json. Here: the labels the facts hold against, and that the rule adds no
Fusion work (it reads the facts of the unit inserts.wall reads, so no unit is measured for it). Names are invented."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_attest, gate_judges, gate_registry


class LocalBoss(unittest.TestCase):
    def setUp(self):
        self.reg = gate_registry.load_default()
        self.rule = self.reg['rules']['inserts.local_boss']
        self.labels = self.rule['attest']['labels']

    def question(self, by=(), text='pocket M3x4x5 at (0, 0, 10): its wall is 4.05 only on a local round boss'):
        return {'rule': 'inserts.local_boss', 'part': 'demo/bracket', 'by': list(by), 'finding_text': text, 'anchor': {'kind': 'insert_pocket'}}

    def test_a_named_neighbour_is_needed_for_the_first_label(self):
        why = gate_attest.label_contradicted(None, self.reg, self.question(), self.labels[0])
        self.assertIn('the row names none', why)
        self.assertIsNone(gate_attest.label_contradicted(None, self.reg, self.question(by=['ref bearing 6808 top']), self.labels[0]))

    def test_the_boss_is_the_function_only_with_a_bought_part_on_its_axis(self):
        why = gate_attest.label_contradicted(None, self.reg, self.question(by=['ref bearing 6808 top']), self.labels[2])
        self.assertIn('a bought part on its axis', why)
        ok = self.question(text='pocket M3x4x5 at (0, 0, 10): its wall is 5.8 only on a local round boss ... On its axis: ref tensioner bearing 625.')
        self.assertIsNone(gate_attest.label_contradicted(None, self.reg, ok, self.labels[2]))

    def test_the_swept_path_label_holds_against_nothing_and_there_is_no_label_for_a_wider_wall(self):
        self.assertIsNone(gate_attest.label_contradicted(None, self.reg, self.question(), self.labels[1]))
        self.assertFalse(any('wider wall is possible' in l for l in self.labels))

    def test_it_reads_the_unit_inserts_wall_reads_and_asks_per_finding(self):
        wall = self.reg['rules']['inserts.wall']
        self.assertEqual((self.rule['check']['probe'], self.rule['level'], self.rule['attest']['per']), (wall['check']['probe'], 'attest', 'finding'))
        self.assertIn('inserts_local_boss', gate_judges.JUDGES)
        self.assertEqual(self.rule['fix_order'], wall['fix_order'])


if __name__ == '__main__':
    unittest.main()
