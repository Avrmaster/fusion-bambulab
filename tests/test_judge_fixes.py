"""Helpers of the judges that the 0.3.1 fixes added or changed, tested on their own (the judges themselves are tested
by the fixtures in tests/fixtures/facts)."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_judges as J


class PinNames(unittest.TestCase):
    """fits.bought_part_pocket reads the size of a pin from its name: never from the size of a key, never from a note."""
    def test_a_keyed_shaft_is_its_diameter_not_its_key(self):
        self.assertEqual(J._pin_size('GM20 shaft D8h7 (key 3 x 3 x 14)'), (8.0, None))
        self.assertEqual(J._pin_size('bought gear motor / GM20 shaft D8h7 (key 3 x 3 x 14)'), (8.0, None))
        self.assertEqual(J._pin_size('shaft D8 key 3 x 3 x 14'), (8.0, None))

    def test_the_sizes_that_worked_before_still_do(self):
        for name, want in (('pin DIN 6325 5m6 x 50', (5.0, 50.0)), ('pin DIN 6325 5m6x50', (5.0, 50.0)), ('pin D5 x 20', (5.0, 20.0)), ('4m6x32', (4.0, 32.0)),
                           ('Rods (steel D6) / Rod', (6.0, None)), ('dowel 6x20', (6.0, 20.0)), ('pin 3h8 x 12', (3.0, 12.0)), ('M3x12', (None, None))):
            self.assertEqual(J._pin_size(name), want, name)

    def test_a_bearing_whose_note_names_a_shaft_is_no_pin(self):
        params = {'fits.pin_ref_pattern': r'(?i)(?<![a-z])(?:pins?|dowels?|rods?|shafts?|axles?)(?![a-z])', 'bearings.ref_pattern': '(?i)bearing'}
        hole = {'coaxial': [{'name': 'ref shaft A: pin 5m6x50 + sleeves / bearing 686 6x13x5 (shaft A inner, on sleeve a)', 'kind': 'bought'},
                            {'name': 'ref shaft A: pin 5m6x50 + sleeves / pin 5m6x50', 'kind': 'bought'}]}
        self.assertEqual(J._pins_on_axis(hole, params), ['ref shaft A: pin 5m6x50 + sleeves / pin 5m6x50'])
        self.assertEqual(J._pins_on_axis(hole, {'fits.pin_ref_pattern': params['fits.pin_ref_pattern']}), ['ref shaft A: pin 5m6x50 + sleeves / pin 5m6x50'])

    def test_a_gearbox_shaft_is_told_from_a_stock_pin(self):
        self.assertTrue(J._assembly_shaft(['bought gear motor / GM20 shaft D8h7 (key 3 x 3 x 14)']))
        self.assertFalse(J._assembly_shaft(['Dowel pins / dowel D5 x 20']))
        self.assertFalse(J._assembly_shaft(['ref shaft A / shaft pin 5m6x50']))


if __name__ == '__main__':
    unittest.main()
