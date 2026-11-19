"""Feature anchors, context hashes and waiver states. No Fusion."""
import math, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_features as gf

A = gf.anchor('insert_pocket', (0, 0, 1), (12.0, -30.0, 40.0))


def finding(rule='inserts.wall', part='demo/bracket', anchor=A, context='c' * 16, **kw):
    return dict({'rule': rule, 'part': part, 'anchor': anchor, 'context': context, 'verdict': 'GROW'}, **kw)


def waiver(**kw):
    w = {'rule': 'inserts.wall', 'part': 'demo/bracket', 'anchor': A, 'context': 'c' * 16, 'date': '2026-03-04',
         'reason': 'the bore of the bought bearing is 1.8 mm from the pocket on that side and cannot move',
         'quote': 'leave these two as they are'}
    w.update(kw)
    return w


class Anchors(unittest.TestCase):
    def test_rounding_to_the_grid(self):
        self.assertEqual(gf.round_to(0.26), 0.25); self.assertEqual(gf.round_to(0.28), 0.3); self.assertEqual(gf.round_to(-0.01), 0.0)
        self.assertEqual(gf.round_to(39.66), 39.65); self.assertEqual(gf.round_to(0.1 + 0.2), 0.3); self.assertEqual(gf.round_to(7.449, 0.1), 7.4)
        self.assertEqual(str(gf.round_to(-0.0)), '0.0')

    def test_anchor_is_normalised(self):
        a = gf.anchor('hole', (0, 0, 5), (12.02, -30.03, 39.99))
        self.assertEqual(a, {'kind': 'hole', 'axis': [0.0, 0.0, 1.0], 'point': [12.0, -30.05, 40.0]})
        self.assertEqual(gf.anchor('hole', (1, 1, 0), (0, 0, 0))['axis'], [0.7071, 0.7071, 0.0])
        self.assertEqual(gf.anchor('hole', (0, -0.0, -1), (0, 0, 0))['axis'], [0.0, 0.0, -1.0])
        self.assertEqual(str(gf.anchor('hole', (0, -1e-9, -1), (-0.001, 0, 0))), "{'kind': 'hole', 'axis': [0.0, 0.0, -1.0], 'point': [0.0, 0.0, 0.0]}")

    def test_part_anchor(self):
        self.assertEqual(gf.anchor('part', (1, 0, 0), (5, 5, 5)), gf.part_anchor())
        self.assertEqual(gf.part_anchor(), {'kind': 'part', 'axis': [0.0, 0.0, 0.0], 'point': [0.0, 0.0, 0.0]})

    def test_bad_anchor(self):
        with self.assertRaises(ValueError): gf.anchor('pocket', (0, 0, 1), (0, 0, 0))
        with self.assertRaises(ValueError): gf.anchor('hole', (0, 1), (0, 0, 0))

    def test_key_is_stable(self):
        self.assertEqual(gf.anchor_key(A), 'insert_pocket|0.0,0.0,1.0|12.0,-30.0,40.0')
        self.assertEqual(gf.anchor_key({'kind': 'insert_pocket', 'axis': [0, 0, 2], 'point': [12.01, -30.0, 40.0]}), gf.anchor_key(A))

    def test_match_within_tolerance_across_the_axis(self):
        self.assertTrue(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, 1), (12.3, -30.0, 40.0))))
        self.assertFalse(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, 1), (12.35, -30.0, 40.0))))
        self.assertTrue(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, 1), (12.2, -30.2, 40.0))))
        self.assertFalse(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, 1), (12.25, -30.25, 40.0))))

    def test_a_recut_mouth_keeps_the_anchor(self):
        # a 0.54 mm melt relief or a 1.0 mm landing cut moves the mouth along the axis only
        for dz in (0.1, 0.54, 1.0, 2.0):
            self.assertTrue(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, 1), (12.0, -30.0, 40.0 - dz))), dz)
        self.assertFalse(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, 1), (12.0, -30.0, 37.9))))

    def test_a_moved_pocket_is_another_feature(self):
        self.assertFalse(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, 1), (13.2, -30.0, 40.0))))

    def test_kind_and_direction_matter(self):
        self.assertFalse(gf.anchors_match(A, gf.anchor('hole', (0, 0, 1), (12.0, -30.0, 40.0))))
        self.assertFalse(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, -1), (12.0, -30.0, 40.0))))      # the pocket from the other face
        tilt = lambda deg: (math.sin(math.radians(deg)), 0, math.cos(math.radians(deg)))
        self.assertTrue(gf.anchors_match(A, gf.anchor('insert_pocket', tilt(1.5), (12.0, -30.0, 40.0))))
        self.assertFalse(gf.anchors_match(A, gf.anchor('insert_pocket', tilt(3.0), (12.0, -30.0, 40.0))))

    def test_part_anchors_and_missing_anchors(self):
        self.assertTrue(gf.anchors_match(gf.part_anchor(), gf.part_anchor()))
        self.assertFalse(gf.anchors_match(gf.part_anchor(), A)); self.assertFalse(gf.anchors_match(A, None)); self.assertFalse(gf.anchors_match(None, None))

    def test_custom_tolerances(self):
        b = gf.anchor('insert_pocket', (0, 0, 1), (12.5, -30.0, 40.0))
        self.assertFalse(gf.anchors_match(A, b)); self.assertTrue(gf.anchors_match(A, b, tol=0.5))
        self.assertFalse(gf.anchors_match(A, gf.anchor('insert_pocket', (0, 0, 1), (12.0, -30.0, 39.4)), tol_along=0.3))

    def test_distance(self):
        across, along = gf.anchor_distance(A, gf.anchor('insert_pocket', (0, 0, 1), (12.3, -30.4, 39.0)))
        self.assertAlmostEqual(across, 0.5); self.assertAlmostEqual(along, 1.0)

    def test_find_takes_the_nearest(self):
        far = {'id': 'far', 'anchor': gf.anchor('insert_pocket', (0, 0, 1), (12.25, -30.0, 40.0))}
        near = {'id': 'near', 'anchor': gf.anchor('insert_pocket', (0, 0, 1), (12.05, -30.0, 40.0))}
        other = {'id': 'other', 'anchor': gf.anchor('insert_pocket', (0, 0, 1), (30.0, -30.0, 40.0))}
        self.assertEqual(gf.find(A, [far, other, near, {'no': 'anchor'}])['id'], 'near')
        self.assertIsNone(gf.find(A, [other])); self.assertIsNone(gf.find(A, [])); self.assertIsNone(gf.find(A, None))


class Context(unittest.TestCase):
    def test_same_facts_same_hash(self):
        a = gf.context_hash({'kind': 'M3x4x5', 'wall': 2.61, 'blocked_by': ['ref bearing 6005']})
        b = gf.context_hash({'blocked_by': ['ref bearing 6005'], 'wall': 2.6, 'kind': 'M3x4x5'})
        self.assertEqual(a, b); self.assertEqual(len(a), 16); int(a, 16)

    def test_a_changed_fact_changes_the_hash(self):
        base = gf.context_hash({'wall': 2.6, 'blocked_by': []})
        self.assertNotEqual(base, gf.context_hash({'wall': 2.4, 'blocked_by': []}))
        self.assertNotEqual(base, gf.context_hash({'wall': 2.6, 'blocked_by': ['Cover']}))
        self.assertNotEqual(gf.context_hash([1, 2]), gf.context_hash([2, 1]))

    def test_step_and_types(self):
        self.assertEqual(gf.context_hash({'w': 2.64}, step=0.1), gf.context_hash({'w': 2.56}, step=0.1))
        self.assertNotEqual(gf.context_hash({'w': 2.64}), gf.context_hash({'w': 2.56}))
        self.assertEqual(gf.context_hash({'a': (1, 2), 'b': None, 'c': True}), gf.context_hash({'a': [1.0, 2.0], 'b': None, 'c': True}))
        with self.assertRaises(TypeError): gf.context_hash({'a': object()})

    def test_ids(self):
        f = finding()
        self.assertRegex(gf.finding_id(f), r'^F-[0-9a-f]{6}$')
        self.assertEqual(gf.finding_id(f), gf.finding_id(finding(anchor={'kind': 'insert_pocket', 'axis': [0, 0, 1], 'point': [12.01, -30, 40]})))
        self.assertNotEqual(gf.finding_id(f), gf.finding_id(finding(context='d' * 16)))
        self.assertNotEqual(gf.finding_id(f), gf.finding_id(finding(part='demo/cover')))
        self.assertEqual(gf.feature_id('Q', 'print.bed_face', 'demo/bracket', None, 'abc')[:2], 'Q-')


class Reasons(unittest.TestCase):
    def test_an_export_order_is_not_a_reason(self):
        for bad in ('yes, export', 'Yes, export.', 'ok print it', 'export it, the owner said so', 'approved, go ahead', 'just ship it please',
                    'waive it', 'fine', 'OK', 'the owner said: export this part now', 'go', 'accepted', 'yes yes export export export'):
            self.assertIsNotNone(gf.reason_error(bad), bad)
        self.assertIn('order to export', gf.reason_error('yes, export'))

    def test_empty_and_too_short(self):
        for bad in ('', '   ', None, 5, 'too thin'):
            self.assertIsNotNone(gf.reason_error(bad), repr(bad))
        self.assertIn('too short', gf.reason_error('too thin'))

    def test_a_real_reason_passes(self):
        for good in ('the bore of the bought bearing is 1.8 mm from the pocket on that side and cannot move',
                     'bridge layer under a bed-facing counterbore, 0.3 mm on purpose',
                     'motor flange blocks the wall here; melt this insert last and briefly',
                     'export of this bracket is fine because the thin rim is a sacrificial bridge layer'):
            self.assertIsNone(gf.reason_error(good), good)


class Waivers(unittest.TestCase):
    def test_validate(self):
        self.assertEqual(gf.validate_waiver(waiver()), [])
        self.assertIn('waiver: missing "quote"', gf.validate_waiver({k: v for k, v in waiver().items() if k != 'quote'}))
        self.assertTrue(any('order to export' in e for e in gf.validate_waiver(waiver(reason='yes, export'))))
        self.assertTrue(any('"quote" must hold' in e for e in gf.validate_waiver(waiver(quote=' '))))
        self.assertTrue(any('YYYY-MM-DD' in e for e in gf.validate_waiver(waiver(date='yesterday'))))
        self.assertTrue(any('"anchor" must be' in e for e in gf.validate_waiver(waiver(anchor={'kind': 'pocket'}))))
        self.assertTrue(any('"context" must be' in e for e in gf.validate_waiver(waiver(context=''))))
        self.assertEqual(gf.validate_waiver('x'), ['a waiver must be an object'])

    def test_valid(self):
        f = finding()
        self.assertEqual(gf.waiver_state(waiver(), [f]), ('valid', f))

    def test_valid_after_an_unrelated_change_elsewhere(self):
        other = finding(anchor=gf.anchor('insert_pocket', (0, 0, 1), (60.0, 0.0, 40.0)), context='e' * 16)
        f = finding()
        self.assertEqual(gf.waiver_state(waiver(), [other, f]), ('valid', f))

    def test_expired_when_the_context_changed(self):
        # a relief was cut at the mouth: same feature (0.54 lower), other measured facts
        f = finding(anchor=gf.anchor('insert_pocket', (0, 0, 1), (12.0, -30.0, 39.45)), context='d' * 16)
        self.assertEqual(gf.waiver_state(waiver(), [f]), ('expired', f))

    def test_expired_when_the_feature_is_there_and_the_finding_is_gone(self):
        self.assertEqual(gf.waiver_state(waiver(), [], features=[{'id': 'h1', 'anchor': A}]), ('expired', None))

    def test_orphan_when_nothing_has_the_anchor(self):
        moved = finding(anchor=gf.anchor('insert_pocket', (0, 0, 1), (13.2, -30.0, 40.0)))
        self.assertEqual(gf.waiver_state(waiver(), [moved]), ('orphan', None))
        self.assertEqual(gf.waiver_state(waiver(), []), ('orphan', None))
        self.assertEqual(gf.waiver_state(waiver(), [], features=[{'id': 'h9', 'anchor': moved['anchor']}]), ('orphan', None))

    def test_other_rule_or_part_never_counts(self):
        self.assertEqual(gf.waiver_state(waiver(), [finding(rule='inserts.floor')])[0], 'orphan')
        self.assertEqual(gf.waiver_state(waiver(), [finding(part='demo/cover')])[0], 'orphan')

    def test_apply(self):
        f1, f2 = finding(), finding(anchor=gf.anchor('insert_pocket', (0, 0, 1), (60.0, 0.0, 40.0)), context='e' * 16)
        open_, waived, rows = gf.apply_waivers([f1, f2], [waiver(id='W-1')])
        self.assertEqual(open_, [f2]); self.assertEqual(len(waived), 1); self.assertEqual(waived[0]['waiver'], 'W-1')
        self.assertEqual(rows, [{'waiver': 'W-1', 'rule': 'inserts.wall', 'part': 'demo/bracket', 'state': 'valid', 'finding': f1}])
        self.assertNotIn('waiver', f1)

    def test_an_expired_or_invalid_waiver_waives_nothing(self):
        f = finding(context='d' * 16)
        open_, waived, rows = gf.apply_waivers([f], [waiver(id='W-1')])
        self.assertEqual((open_, waived, rows[0]['state']), ([f], [], 'expired'))
        f = finding()
        open_, waived, rows = gf.apply_waivers([f], [waiver(id='W-2', reason='yes, export')])
        self.assertEqual((open_, waived, rows[0]['state']), ([f], [], 'invalid')); self.assertTrue(rows[0]['errors'])

    def test_waivers_of_other_parts_are_skipped(self):
        open_, waived, rows = gf.apply_waivers([finding()], [waiver(part='demo/cover')])
        self.assertEqual((len(open_), waived, rows), (1, [], []))
        open_, waived, rows = gf.apply_waivers([], [waiver(), waiver(part='demo/cover')], part='demo/bracket')
        self.assertEqual([r['state'] for r in rows], ['orphan'])

    def test_waiver_without_id_gets_its_index(self):
        _, waived, rows = gf.apply_waivers([finding()], [waiver()])
        self.assertEqual((waived[0]['waiver'], rows[0]['waiver']), (0, 0))


if __name__ == '__main__':
    unittest.main()
