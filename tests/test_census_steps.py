"""fusion_census cut into small steps: on a toothed pulley with a flange (fake_toothed.py, a fake adsk that counts its
API reads), the facts of a unit measured over many calls are the facts of one call, also when every step and every
point test is a call of its own; no call reads the API more than one small step past its budget; the timing block
counts what the fake counts. And the streaming form of opening_shape() in _geom.py gives exactly its answer."""
import json, math, os, random, sys, time, types, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_fusion_rpc                                  # noqa: E402
from probes import _geom as G                           # noqa: E402
import fake_toothed as T                                # noqa: E402
from test_geom import TABLES, PARAMS                    # noqa: E402

COMPARED = ('faces', 'neighbours', 'stacks', 'openings')     # the stages whose reads the probe counts itself


class FakeTime:
    """time.time() of a Fusion whose cost is its API: `api_s` a read, `pc_s` a point test (what the fake counts)"""
    def __init__(self, api_s=0.00015, pc_s=0.001):
        self.api_s, self.pc_s, self.base = api_s, pc_s, 1000.0

    def time(self):
        return self.base + sum(T.COUNTER.api.values()) * self.api_s + sum(T.COUNTER.pc.values()) * self.pc_s

    def __getattr__(self, name):
        return getattr(time, name)


class Unit:
    """The census unit, its text executed once, probe(job) called once per call the way run() calls it."""

    def __init__(self, occs, clock=None, deadline_s=30, **kw):
        self.occs = occs
        self.job = gate_fusion_rpc.new_job(unit_id='u-0001', run='r1', probe='fusion_census', doc='Demo Assembly', doc_key='demo',
                                           out=os.path.join(ROOT, 'unused.json'), part='demo/pulley', component_id='c-pulley',
                                           occurrence='Pulley:1', body='Body1', params=PARAMS, tables=TABLES, not_printed=['^ref '],
                                           deadline_s=deadline_s, **kw)
        self.g = {'JOB': self.job}
        with T.fake_adsk(occs):
            exec(compile(gate_fusion_rpc.unit_text(self.job), 'fusion_census.py', 'exec'), self.g)
        if clock is not None:
            self.g['time'] = clock
        self.clock = clock or time
        T.COUNTER.stage = lambda: (self.g.get('_CEN_RUN') or {}).get('stage', 'prelude')

    def call(self, cursor=None, args=None):
        job = dict(self.job, cursor=cursor, args=args or {})
        g = self.g
        g['JOB'] = job
        T.COUNTER.stage = lambda: (g.get('_CEN_RUN') or {}).get('stage', 'prelude')      # this unit's stage, whatever unit came before
        g['_GATE_STARTED'] = self.clock.time()
        g['_GATE_CLOCK'].update(last=None, gap=0.0)
        del g['_GATE_NOTES'][:]
        with T.fake_adsk(self.occs):
            try:
                out = g['probe'](job)
            except g['GateNotYet'] as e:                # what run() answers while a large body is signed
                out = {'facts': {'signing': e.args[0]}, 'done': False, 'cursor': cursor}
        out = json.loads(json.dumps(out))               # what goes back to the host: plain JSON
        out['notes'] = list(g['_GATE_NOTES'])
        out['elapsed'] = self.clock.time() - g['_GATE_STARTED']
        return out

    def whole(self, args=None, before=None, cap=20000):
        """every call until done: (facts joined as the host joins them, the answers of every call)"""
        facts, calls, cursor = None, [], None
        while True:
            if before is not None:
                before(len(calls), cursor)
            out = self.call(cursor, args)
            calls.append(out)
            facts = gate_fusion_rpc.merge_facts(facts, out['facts'])
            if out['done']:
                return facts, calls
            cursor = out['cursor']
            assert len(calls) < cap, 'the unit never ends'


def small_scene(**kw):
    return T.scene(teeth=12, per=6, filler=(60, 90), cross=(6, 20), **kw)


class Steps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        T.COUNTER.reset()
        cls.occs, cls.pulley = small_scene()
        cls.unit = Unit(cls.occs)
        cls.one, (cls.only,) = cls.unit.whole()

    def test_the_part_is_what_the_tests_need(self):
        f = self.one
        self.assertGreater(self.pulley.m, 32)                  # the toothed loop takes the path of a large loop (_CEN_SMALL)
        self.assertEqual(len(f['holes']), 7)
        seat = [h for h in f['holes'] if (h['class'] or {}).get('role') == 'bearing_seat']
        self.assertEqual([c['name'] for c in seat[0]['coaxial']], ['ref bearing 6801', 'ref shaft'])
        (o,) = f['openings']                                   # the flange round the teeth reads as one window, 1.88 thick
        self.assertEqual((o['id'], o['shape'], o['through'], o['wall']), ('o1', 'window', True, 1.88))
        self.assertNotIn('coaxial_skipped', f)

    def test_many_calls_give_the_facts_of_one_call(self):
        for args in ({'limit': 1}, {'limit': 1, 'tape_limit': 1}, {'tape_limit': 7}, {'limit': 5, 'tape_limit': 40}):
            facts, calls = self.unit.whole(args)
            self.assertEqual(facts, self.one, args)
            self.assertGreater(len(calls), 20, args)

    def test_every_step_is_a_call_of_its_own(self):
        """With one step and one point test per call, no call reads more than one step's worth of the API: one face of
        the part (its edges and its ring of point tests), one occurrence, one face of a neighbour, one edge of the long
        loop, one stack."""
        stages = []
        T.COUNTER.reset()
        last = [dict(), dict()]
        worst = {}
        facts, calls = None, 0
        cursor = None
        while True:
            out = self.unit.call(cursor, {'limit': 1, 'tape_limit': 1})
            calls += 1
            facts = gate_fusion_rpc.merge_facts(facts, out['facts'])
            for s in COMPARED:
                api = T.COUNTER.api.get(s, 0) - last[0].get(s, 0)
                pc = T.COUNTER.pc.get(s, 0) - last[1].get(s, 0)
                was = worst.get(s, (0, 0))
                worst[s] = (max(was[0], api), max(was[1], pc))
            last = [dict(T.COUNTER.api), dict(T.COUNTER.pc)]
            stages.append((out['cursor'] or {}).get('stage'))
            if out['done']:
                break
            cursor = out['cursor']
        self.assertEqual(facts, self.one)
        self.assertEqual({s: worst[s][1] for s in COMPARED}, {'faces': 1, 'neighbours': 0, 'stacks': 1, 'openings': 1})   # one point test a call
        for s in COMPARED:
            self.assertLess(worst[s][0], 80, (s, worst[s]))                                     # one small step of reads
        self.assertEqual(set(stages), {'faces', 'near', 'nbfaces', 'stacks', 'openings', None})

    def test_the_timing_block_counts_what_the_fake_counts(self):
        T.COUNTER.reset()
        facts, calls = self.unit.whole({'limit': 4, 'tape_limit': 30})
        t = calls[-1]['timing']
        self.assertEqual(t['calls'], len(calls))
        for s in COMPARED:
            self.assertEqual((t['stages'][s]['api'], t['stages'][s]['pc']), (T.COUNTER.api.get(s, 0), T.COUNTER.pc.get(s, 0)), s)
        self.assertEqual(sorted(t['stages']), sorted(['prelude', 'faces', 'neighbours', 'state', 'stacks', 'openings', 'shape']))
        self.assertTrue(1 <= len(t['slowest']) <= 5)
        self.assertEqual(sorted(t['slowest'][0]), ['api', 'face', 'pc', 's'])
        self.assertTrue(all(c['timing']['gap_s'] >= c['timing']['call_gap_s'] for c in calls))
        self.assertTrue(any(n.startswith('timing: %d call(s), longest stretch without the clock' % len(calls)) for n in calls[-1]['notes']))
        self.assertNotIn('timing', facts)                                              # beside the facts, never in them
        self.assertEqual(self.only['timing']['calls'], 1)

    def test_a_cursor_of_a_later_version_is_refused(self):
        first = self.unit.call(None, {'limit': 3})
        with self.assertRaises(ValueError) as e:
            self.unit.call(dict(first['cursor'], v=3))
        self.assertIn('another version of the census', str(e.exception))

    def test_a_unit_the_census_before_0_2_2_left_half_done_goes_on(self):
        """A cursor of 0.2.0 / 0.2.1 ({"sig", "face", "cyl", "stack", "oface", "n_open", "counts"}, "tape" in the openings)
        at each of its three places: the unit goes on and its facts are those of one call. "coaxial_skipped", which
        those versions wrote in every call that measured a stack, is not written again once a stack was measured."""
        args = {'neighbour_faces': 70, 'limit': 1}
        one, _ = self.unit.whole({'neighbour_faces': 70})
        self.assertEqual(one['coaxial_skipped'], ['ref bearing 6801'])
        facts, cursor, seen = None, None, []                   # (facts so far, cursor) after every call of one step a call
        while True:
            out = self.unit.call(cursor, args)
            facts = gate_fusion_rpc.merge_facts(facts, out['facts'])
            if out['done']:
                break
            cursor = out['cursor']
            seen.append((json.loads(json.dumps(facts)), cursor))
        n = next(c['face'] for _, c in seen if c['stage'] == 'near')
        cyl = next(c['cyl'] for _, c in seen if c['stage'] == 'stacks')

        def old(c, **kw):
            return dict({'sig': c['sig'], 'face': n, 'cyl': cyl, 'stack': 0, 'oface': 0, 'n_open': 0, 'counts': c['counts']}, **kw)
        picks = {
            'faces': next((f, old(c, face=c['face'], cyl=c['cyl'])) for f, c in seen
                          if c['stage'] == 'faces' and c.get('fs') is None and c['cyl'] and c['face'] > n // 2),
            'stack 0': next((f, old(c)) for f, c in seen if c['stage'] == 'near' and c['near']['o'] == 0),
            'stack 2': next((f, old(c, stack=2)) for f, c in seen if c['stage'] == 'stacks' and c['stack'] == 2),
            'openings': next((f, old(c, stack=len(G.stack_holes(cyl)), oface=c['oface'], n_open=c['n_open'], tape={'n': 3, 'hex': '5'}, again=1))
                             for f, c in seen if c['stage'] == 'openings' and c.get('of') is None and c['oface'] > 0)}
        self.assertTrue(any('coaxial_skipped' in f for f, _ in seen))
        for where, (before, cursor) in picks.items():
            self.assertEqual('coaxial_skipped' in before, where in ('stack 2', 'openings'), where)
            facts, notes = before, []
            while True:
                out = self.unit.call(cursor, args)
                notes += out['notes']
                facts = gate_fusion_rpc.merge_facts(facts, out['facts'])
                if out['done']:
                    break
                cursor = out['cursor']
                self.assertEqual(cursor['v'], 2)
            self.assertEqual(facts, one, where)
            self.assertEqual(len([x for x in notes if x.startswith('a cursor of the census before 0.2.2 was taken over at')]), 1, where)

    def test_a_neighbour_that_changed_between_two_calls_is_read_again(self):
        occs, _ = small_scene()
        unit = Unit(occs)
        one, _ = unit.whole()
        shaft = occs[1].component.bRepBodies.item(0)
        seen = []

        def edit(n, cursor):                                    # the shaft (2nd smallest neighbour) is edited while its faces are read
            if cursor and cursor.get('stage') == 'nbfaces' and cursor['nb']['i'] == 1 and cursor['nb']['f'] > 0 and not seen:
                object.__setattr__(shaft, 'revisionId', shaft.revisionId + '-edited')
                seen.append(n)
        facts, calls = unit.whole({'limit': 2}, before=edit)
        self.assertTrue(seen)
        self.assertEqual(facts, one)                            # the walk began again: no face of two states joined
        notes = [n for c in calls for n in c['notes'] if 'bodies round the part changed' in n]
        self.assertEqual(len(notes), 1)
        self.assertIn('(ref shaft)', notes[0])

    def test_a_neighbour_that_moved_between_two_calls_is_read_again(self):
        """Only its place changed (revision, faces, volume and area are the same): the walk starts again, and the facts
        are those of one call on the moved scene, not the bearing at its old place on the axis of the seat"""
        occs, _ = small_scene()
        moved_occs, _ = small_scene()
        object.__setattr__(moved_occs[2], 'transform2', T.Shifted(3.0))
        moved, _ = Unit(moved_occs).whole()
        seat = [h for h in moved['holes'] if (h['class'] or {}).get('role') == 'bearing_seat']
        self.assertEqual([c['name'] for c in seat[0]['coaxial']], ['ref shaft'])
        seen = []

        def move(n, cursor):                                    # the bearing (3rd smallest neighbour) moves while its faces are read
            if cursor and cursor.get('stage') == 'nbfaces' and cursor['nb']['i'] == 2 and cursor['nb']['f'] > 0 and not seen:
                object.__setattr__(occs[2], 'transform2', T.Shifted(3.0))
                seen.append(n)
        facts, calls = Unit(occs).whole({'limit': 2}, before=move)
        self.assertTrue(seen)
        self.assertEqual(facts, moved)
        notes = [n for c in calls for n in c['notes'] if 'bodies round the part changed' in n]
        self.assertEqual(len(notes), 1)
        self.assertIn('(ref bearing 6801)', notes[0])

    def test_a_neighbour_with_no_state_from_fusion(self):
        """No revision id and no volume: its face count and box are its state, and an edit of it is still seen. With
        nothing readable at all, every call says that its faces are joined unchecked."""
        occs, _ = small_scene()
        unit = Unit(occs)
        one, _ = unit.whole()
        shaft = occs[1].component.bRepBodies.item(0)
        unit.g['gate_body_state'] = lambda b, mesh=False: None if b is shaft else 'state'
        seen = []

        def edit(n, cursor):
            if cursor and cursor.get('stage') == 'nbfaces' and cursor['nb']['i'] == 1 and cursor['nb']['f'] > 0 and not seen:
                object.__setattr__(shaft, 'boundingBox', T.BBox((-7.9, -7.9, -30.0), (7.9, 7.9, 29.0)))
                seen.append(n)
        facts, calls = unit.whole({'limit': 2}, before=edit)
        self.assertTrue(seen)
        self.assertEqual(facts, one)
        notes = [n for c in calls for n in c['notes'] if 'bodies round the part changed' in n]
        self.assertEqual(len(notes), 1)
        self.assertIn('(ref shaft)', notes[0])
        unit.g['_cen_nb_state'] = lambda b: None if b is shaft else 'state'
        facts, calls = unit.whole({'limit': 2})
        self.assertEqual(facts, one)
        joined = [c for c in calls if any('ref shaft round the part cannot be read' in n for n in c['notes'])]
        self.assertGreater(len(joined), 10)

    def test_restarts_of_the_neighbours_count_only_in_a_row(self):
        """Three edits far apart, each seen by the next call: three new walks, no error. An edit before every call: the
        third restart in a row ends the unit."""
        occs, _ = small_scene()
        unit = Unit(occs)
        one, _ = unit.whole()
        shaft = occs[1].component.bRepBodies.item(0)
        edits = []

        def apart(n, cursor):
            if (cursor and cursor.get('stage') == 'nbfaces' and '1' in cursor['nb']['states'] and len(edits) < 3
                    and (not edits or n - edits[-1] >= 4)):
                object.__setattr__(shaft, 'revisionId', shaft.revisionId + '-e')
                edits.append(n)
        facts, calls = unit.whole({'limit': 1}, before=apart)
        self.assertEqual(len(edits), 3)
        self.assertEqual(facts, one)
        self.assertEqual(len([n for c in calls for n in c['notes'] if 'bodies round the part changed' in n]), 3)

        def always(n, cursor):
            if cursor and cursor.get('stage') == 'nbfaces' and cursor['nb']['states']:
                for o in occs[1:]:
                    b = o.component.bRepBodies.item(0)
                    object.__setattr__(b, 'revisionId', b.revisionId + '-e')
        with self.assertRaises(RuntimeError) as e:
            unit.whole({'limit': 1}, before=always)
        self.assertIn('in three calls in a row', str(e.exception))

    def test_the_names_left_out_are_written_once(self):
        occs, _ = small_scene()
        unit = Unit(occs)
        one, _ = unit.whole({'neighbour_faces': 70})
        self.assertEqual(one['coaxial_skipped'], ['ref bearing 6801'])           # the larger of the two neighbours
        many, calls = unit.whole({'neighbour_faces': 70, 'limit': 1})
        self.assertGreater(len(calls), 50)
        self.assertEqual(many, one)

    def test_the_demo_part_in_one_step_a_call(self):
        """test_geom's demo part through this harness too: one call, then one step a call"""
        import test_geom
        app = test_geom.demo()
        g = {}
        job = gate_fusion_rpc.new_job(unit_id='u-0001', run='r1', probe='fusion_census', doc='Demo Assembly', doc_key='demo',
                                      out=os.path.join(ROOT, 'unused.json'), part='demo/bracket', component_id='c-bracket',
                                      occurrence='Bracket:1', body='Body1', params=test_geom.PARAMS, tables=TABLES,
                                      not_printed=test_geom.NOT_PRINTED, deadline_s=30)
        with test_geom.fake_adsk(app):
            g['JOB'] = job
            exec(compile(gate_fusion_rpc.unit_text(job), 'fusion_census.py', 'exec'), g)
            runs = []
            for args in ({}, {'limit': 1, 'tape_limit': 1}):
                facts, cursor, n = None, None, 0
                while True:
                    g['_GATE_STARTED'] = time.time()
                    out = json.loads(json.dumps(g['probe'](dict(job, cursor=cursor, args=args))))
                    facts, n = gate_fusion_rpc.merge_facts(facts, out['facts']), n + 1
                    if out['done']:
                        break
                    cursor = out['cursor']
                runs.append((facts, n))
        self.assertEqual(runs[0][0], runs[1][0])
        self.assertEqual(runs[0][1], 1)
        self.assertGreater(runs[1][1], 200)


class Budget(unittest.TestCase):
    """The pulley of the live case at full size (84 teeth of 30 edges: a loop of 2520 edges in the flange face), under a
    cost model of Fusion: 0.15 ms an API read, 1 ms a point test, a budget of 20 s a call."""

    def test_no_call_runs_past_its_budget(self):
        T.COUNTER.reset()
        occs, pulley = T.scene()
        clock = FakeTime()
        unit = Unit(occs, clock=clock, deadline_s=20)
        facts, calls = unit.whole()
        self.assertEqual(pulley.m, 2520)
        self.assertGreater(len(calls), 2)
        worst = max(c['elapsed'] for c in calls)
        self.assertLess(worst, 20.0 - 2.5 + 0.5, [round(c['elapsed'], 2) for c in calls])     # stops at the reserve, give or take one step
        self.assertLess(max(c['timing']['call_gap_s'] for c in calls), 0.5)
        (o,) = facts['openings']
        self.assertEqual((o['shape'], o['size'], o['wall']), ('window', [79.94, 79.94], 1.88))
        self.assertEqual(len(facts['holes']), 7)


class LargeLoops(unittest.TestCase):
    """The outline points of a large loop live in the memory of the Fusion process, not in the cursor."""

    @staticmethod
    def memory():
        mod = sys.modules.get('_gate_census_outline')
        return mod.loops if mod is not None else {}

    def test_a_round_outline_keeps_its_points_out_of_the_cursor(self):
        """A rim with no teeth (depth 0, 720 edges): the outline in the flange face is convex, so hull_keep() keeps
        every one of its 3240 points. The cursor stays small, the facts are those of one call, and
        each direction asks a few dozen of them instead of all of them (rect_direction's hint)."""
        occs, pulley = T.scene(teeth=24, per=30, filler=(60, 90), cross=(6, 20), depth=0.0)
        unit = Unit(occs)
        one, _ = unit.whole()
        kept = [0]

        def look(n, cursor):
            kept[0] = max([kept[0]] + [len(m['K']) for m in self.memory().values()])
        facts, calls = unit.whole({'limit': 100}, before=look)
        self.assertEqual(facts, one)
        self.assertEqual(pulley.m, 720)
        self.assertGreater(kept[0], 3000)                       # the case: nearly every point kept
        sizes = [len(json.dumps(c['cursor'])) for c in calls if not c['done']]
        self.assertLess(max(sizes), 8000)
        self.assertEqual([c['timing']['cursor_bytes'] for c in calls], sizes + [0])
        self.assertTrue(any(c['cursor'] and (c['cursor'].get('of') or {}).get('lp') for c in calls[:-1]))

        asked = []                                              # the points each direction asked (rect_direction's hint)
        real = unit.g['_rect_argext']
        unit.g['_rect_argext'] = lambda a, ids: asked.append(len(ids)) or real(a, ids)
        try:
            facts, _ = unit.whole({'limit': 100})
        finally:
            unit.g['_rect_argext'] = real
        self.assertEqual(facts, one)
        n_points = 7 * pulley.m // 2 + 2 * pulley.m // 2         # 7 points on an arc edge, 2 on a line edge
        self.assertGreater(len(asked), n_points)                 # every direction went through the hint ...
        self.assertLess(sum(asked) / 2, n_points * kept[0] / 20)   # ... and asked far fewer than every point

        timed = Unit(occs, deadline_s=3.6)                      # the real clock
        facts, calls = timed.whole()
        self.assertEqual(facts, one)
        self.assertLess(max(c['timing']['call_gap_s'] for c in calls), 1.0, [c['timing']['call_gap_s'] for c in calls])

    def test_a_loop_whose_points_are_gone_is_read_again(self):
        """Fusion restarted in the middle of a large loop: the loop is read again from its first edge, the facts are
        those of one call. Gone in three calls in a row: the unit ends in an error."""
        occs, _ = small_scene()
        unit = Unit(occs)
        one, _ = unit.whole()
        lost = []

        def forget(n, cursor):
            lp = ((cursor or {}).get('of') or {}).get('lp') or {}
            if lp.get('phase') == 'p2' and lp['e'] > 3 and not lost:
                self.memory().clear()
                lost.append(n)
        facts, calls = unit.whole({'limit': 1}, before=forget)
        self.assertTrue(lost)
        self.assertEqual(facts, one)
        self.assertEqual(calls[lost[0]]['cursor'].get('lost'), 1)
        self.assertNotIn('lost', calls[lost[0] + 1]['cursor'])

        cursor, facts, again = None, None, None             # the host sends a cursor again (its answer was lost): the
        while True:                                         # memory has moved on past it, so the loop is read again
            out = unit.call(cursor, {'limit': 1})
            lp = ((out['cursor'] or {}).get('of') or {}).get('lp') or {}
            if again is None and lp.get('phase') == 'p1' and lp['e'] == 5:
                again = out['cursor']
                unit.call(again, {'limit': 1})              # this answer never reached the host
                out = unit.call(again, {'limit': 1})
                self.assertEqual(out['cursor'].get('lost'), 1)
            facts = gate_fusion_rpc.merge_facts(facts, out['facts'])
            if out['done']:
                break
            cursor = out['cursor']
        self.assertTrue(again)
        self.assertEqual(facts, one)

        def always(n, cursor):
            lp = ((cursor or {}).get('of') or {}).get('lp') or {}
            if lp.get('phase') == 'p1' and lp['e'] > 0:
                self.memory().clear()
        with self.assertRaises(RuntimeError) as e:
            unit.whole({'limit': 1}, before=always)
        self.assertIn('was not in the memory of Fusion', str(e.exception))


class Outline(unittest.TestCase):
    """The streaming form of opening_shape() (hull_keep, rect_direction, shape_of_rect) against opening_shape() itself."""

    @staticmethod
    def stream(points, normal, arcs, edges, buf):
        if len(points) < 3:
            return None
        u, v = G.axis_frame(normal)
        o = points[0]
        kept, b, qs = [], [], []
        for p in points:
            q = G.outline_q(p, o, u, v)
            b.append(q)
            qs.append(q)
            if len(b) >= buf:
                kept, b = G.hull_keep(kept + b), []
        kept = [tuple(p) for p in json.loads(json.dumps(G.hull_keep(kept + b)))]     # as it comes back from a cursor
        best, prev, hint = None, qs[-1], {}
        for q in qs:
            best, prev = G.rect_direction(best, prev, q, kept, hint), q
        if best is None:
            return None
        rnd = False
        if G.round_wanted(arcs, edges, len(points)):
            cx, cy = G.rect_centre(best)
            rnd = G.spread_round(max(math.hypot(x - cx, y - cy) for x, y in kept), min(math.hypot(x - cx, y - cy) for x, y in qs))
        return G.shape_of_rect(best, o, normal, arcs, rnd)

    def test_the_same_answer_as_opening_shape(self):
        rnd = random.Random(7)
        for trial in range(600):
            kind, n = trial % 6, rnd.randint(3, 120)
            nrm = G.v_unit((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))) if trial % 3 else (0.0, 0.0, 1.0)
            u, v = G.axis_frame(nrm)
            c = (rnd.uniform(-100, 100), rnd.uniform(-100, 100), rnd.uniform(-100, 100))
            at = lambda x, y: G.v_add(c, G.v_add(G.v_scale(u, x), G.v_scale(v, y)))
            if kind == 0:                                   # a circle
                r = rnd.uniform(0.5, 30)
                pts = [at(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)) for k in range(n)]
            elif kind == 1:                                 # a rectangle with points along its sides
                w, h, m = rnd.uniform(0.5, 50), rnd.uniform(0.5, 50), max(1, n // 4)
                pts = [at(*[(t * w, 0), (w, t * h), (w - t * w, h), (0, h - t * h)][s]) for s in range(4) for t in [k / m for k in range(m)]]
            elif kind == 2:                                 # a cloud
                pts = [at(rnd.uniform(-10, 10), rnd.uniform(-3, 3)) for _ in range(n)]
            elif kind == 3:                                 # teeth
                R, Z = rnd.uniform(5, 40), rnd.randint(3, 40)
                pts = [at((R - 1.2 * (0.5 - 0.5 * math.cos(2 * math.pi * ((k / n * Z) % 1)))) * math.cos(2 * math.pi * k / n),
                          (R - 1.2 * (0.5 - 0.5 * math.cos(2 * math.pi * ((k / n * Z) % 1)))) * math.sin(2 * math.pi * k / n)) for k in range(n)]
            elif kind == 4:                                 # a slot, every point twice (the end of one edge, the start of the next)
                L, W = rnd.uniform(3, 30), rnd.uniform(1, 5)
                pts = [p for k in range(n) for p in [at((L / 2) * math.copysign(1, math.cos(2 * math.pi * k / n)) + (W / 2) * math.cos(2 * math.pi * k / n),
                                                        (W / 2) * math.sin(2 * math.pi * k / n))] * 2]
            else:                                           # a grid: exact ties everywhere
                pts = [at(float(rnd.randint(0, 4)), float(rnd.randint(0, 4))) for _ in range(n)]
            arcs = rnd.choice([0, 1, 5, n])
            edges = rnd.choice([None, n, arcs])
            self.assertEqual(self.stream(pts, nrm, arcs, edges, rnd.choice([3, 17, 256])), G.opening_shape(pts, nrm, arcs, edges), (trial, kind, n))

    def test_the_same_answer_on_large_convex_outlines(self):
        """Hundreds of points, nearly all of them kept: the hint of rect_direction() decides most directions from a few
        dozen points, and the answer is still opening_shape()'s, float for float."""
        rnd = random.Random(11)
        for trial in range(160):
            kind, n = trial % 4, rnd.randint(70, 360)
            nrm = G.v_unit((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))) if trial % 2 else (0.0, 0.0, 1.0)
            u, v = G.axis_frame(nrm)
            c = (rnd.uniform(-100, 100), rnd.uniform(-100, 100), rnd.uniform(-100, 100))
            at = lambda x, y: G.v_add(c, G.v_add(G.v_scale(u, x), G.v_scale(v, y)))
            rx, ry, ph = rnd.uniform(0.5, 60), rnd.uniform(0.5, 60), rnd.uniform(0, 6.3)
            if kind == 0:                                   # a circle, maybe with every point twice
                pts = [at(rx * math.cos(2 * math.pi * k / n + ph), rx * math.sin(2 * math.pi * k / n + ph)) for k in range(n)]
                pts = [p for p in pts for _ in range(rnd.choice([1, 2]))]
            elif kind == 1:                                 # an ellipse, unevenly sampled
                ts = sorted(rnd.uniform(0, 2 * math.pi) for _ in range(n))
                pts = [at(rx * math.cos(t), ry * math.sin(t)) for t in ts]
            elif kind == 2:                                 # a stadium (a slot) of many points
                L, W = rnd.uniform(3, 60), rnd.uniform(1, 10)
                pts = [at((L / 2) * math.copysign(1, math.cos(2 * math.pi * k / n)) + (W / 2) * math.cos(2 * math.pi * k / n),
                          (W / 2) * math.sin(2 * math.pi * k / n)) for k in range(n)]
            else:                                           # a regular polygon with points along its sides: ties
                m = rnd.randint(3, 12)
                corners = [(rx * math.cos(2 * math.pi * j / m), rx * math.sin(2 * math.pi * j / m)) for j in range(m)]
                per = max(1, n // m)
                pts = [at(corners[j][0] + (corners[(j + 1) % m][0] - corners[j][0]) * t / per,
                          corners[j][1] + (corners[(j + 1) % m][1] - corners[j][1]) * t / per) for j in range(m) for t in range(per)]
            arcs = rnd.choice([0, len(pts)])
            self.assertEqual(self.stream(pts, nrm, arcs, len(pts), rnd.choice([17, 256])), G.opening_shape(pts, nrm, arcs, len(pts)), (trial, kind, n))

    def test_a_toothed_outline_keeps_few_points(self):
        p = T.Pulley(84, 30)
        pts = [q for k in range(p.m) for q in ([p.at(k, 0.0)] + ([p.at(k + j / 6.0, 0.0) for j in range(1, 6)] if k % 2 == 0 else []) + [p.at(k + 1, 0.0)])]
        u, v = G.axis_frame((0.0, 0.0, 1.0))
        qs = [G.outline_q(x, pts[0], u, v) for x in pts]
        kept = G.hull_keep(qs)
        self.assertEqual(len(pts), 11340)
        self.assertLess(len(kept), 400)
        for k in range(0, len(qs), 97):                     # every direction: the same largest and smallest
            d = (math.cos(k), math.sin(k))
            self.assertEqual((max(x * d[0] + y * d[1] for x, y in kept), min(x * d[0] + y * d[1] for x, y in kept)),
                             (max(x * d[0] + y * d[1] for x, y in qs), min(x * d[0] + y * d[1] for x, y in qs)))

    def test_a_cursor_keeps_every_float_and_the_picks(self):
        xs = [0.0, -0.0, 1e-300, -2.5, math.pi, 1.0 / 3.0, 123456.789, 0.1 + 0.2]
        self.assertEqual([x.hex() for x in json.loads(json.dumps(xs))], [x.hex() for x in xs])
        self.assertEqual(G.outline_picks(10), ([0, 3, 6], [0, 2, 4, 6]))
        self.assertEqual(G.outline_picks(2), ([0, 1], [0, 1]))


if __name__ == '__main__':
    unittest.main()
