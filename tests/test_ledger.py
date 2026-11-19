"""The print ledger: the write journal, the folded view, the answers and the mesh tests behind them.
Synthetic STLs and a synthetic journal built here; neither Fusion nor Bambu Studio, no network."""
import contextlib, datetime, io, json, os, shutil, struct, sys, tempfile, threading, unittest, zipfile
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'ledger'))
import ledger, ledger_cache, ledger_mesh


def write_stl(path, tris, header=b'Fusion Demo Assembly v41 bracket'):
    with open(path, 'wb') as f:
        f.write(header.ljust(80, b' ')[:80] + struct.pack('<I', len(tris)))
        for t in tris:
            f.write(struct.pack('<3f', 0, 0, 0))
            for p in t: f.write(struct.pack('<3f', *p))
            f.write(b'\0\0')
    return path


def quad(a, b, c, d, flip=False):
    return [(a, b, d), (b, c, d)] if flip else [(a, b, c), (a, c, d)]


def block(size=(40.0, 30.0, 10.0), origin=(0.0, 0.0, 0.0), hole=None, flip_top=False):
    """A closed box as triangles. hole = (cx, cy, half width): a square hole through z, measured from the origin."""
    (ox, oy, oz), (sx, sy, sz) = origin, size
    x0, x1, y0, y1, z0, z1 = ox, ox + sx, oy, oy + sy, oz, oz + sz
    T = quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)) + quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)) \
        + quad((x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)) + quad((x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1))
    if hole is None:
        return T + quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), flip_top) + quad((x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0))
    cx, cy, h = hole
    a0, a1, b0, b1 = ox + cx - h, ox + cx + h, oy + cy - h, oy + cy + h
    for z, up in ((z1, True), (z0, False)):
        o = [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]
        i = [(a0, b0, z), (a1, b0, z), (a1, b1, z), (a0, b1, z)]
        for k in range(4):
            q = quad(o[k], o[(k + 1) % 4], i[(k + 1) % 4], i[k])
            T += q if up else [tuple(reversed(t)) for t in q]
    i0 = [(a0, b0, z0), (a1, b0, z0), (a1, b1, z0), (a0, b1, z0)]
    i1 = [(a0, b0, z1), (a1, b0, z1), (a1, b1, z1), (a0, b1, z1)]
    for k in range(4):
        T += [tuple(reversed(t)) for t in quad(i0[k], i0[(k + 1) % 4], i1[(k + 1) % 4], i1[k])]
    return T


BRACKET = dict(hole=(10.0, 15.0, 2.0))                   # 40 x 30 x 10, a hole near the left end
BRACKET_HOLE_MOVED = dict(hole=(13.0, 15.0, 2.0))        # the same block, the hole 3 mm further
COVER = dict(size=(12.0, 30.0, 5.0), origin=(28.0, 0.0, 10.2))      # sits on the right end of the bracket, 0.2 above it
COVER_LOW = dict(size=(12.0, 30.0, 5.0), origin=(28.0, 0.0, 9.6))   # 0.4 into the bracket
KNOB = dict(size=(5.0, 5.0, 5.0), origin=(100.0, 100.0, 0.0))       # far from everything


class Clock(object):
    """ledger._now(), one minute further at every call: the order of events is the order of the calls."""
    def __init__(self): self.t = datetime.datetime(2026, 3, 4, 10, 0, 0)
    def __call__(self):
        self.t += datetime.timedelta(minutes=1)
        return self.t.strftime('%Y-%m-%dT%H:%M:%SZ')


class Base(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        os.makedirs(os.path.join(self.d, 'bambulab', 'ledger')); os.makedirs(os.path.join(self.d, 'stl'))
        self.proj = os.path.join(self.d, 'bambulab', 'Demo.3mf')
        self.clock = Clock()
        p = mock.patch.object(ledger, '_now', self.clock); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(ledger_cache, 'cache_db_path', lambda: None); p.start(); self.addCleanup(p.stop)   # never this machine's cache

    def stl(self, name, **shape):
        return write_stl(os.path.join(self.d, 'stl', name + '.stl'), block(**shape))

    def mesh(self, obj, plate='[TODO] base - ' , index=1, shift=(100.0, 90.0, 0.0), stl=None):
        """One entry of the list bambu3mf hands to record_writes: the STL moved to its place on the bed."""
        path = stl or os.path.join(self.d, 'stl', obj + '.stl')
        V, F = ledger_mesh.read_stl(path)
        return {'object': obj, 'plate': plate + obj, 'plate_index': index, 'printable': True, 'stl': path,
                'vertices': [(x + shift[0], y + shift[1], z + shift[2]) for x, y, z in V], 'triangles': [tuple(t) for t in F]}

    def write(self, *meshes, **kw):
        return ledger.record_writes(self.proj, list(meshes), **kw)

    def events(self, kind=None): return ledger.read_events(self.d, kind)

    def hash(self, name): return ledger_mesh.stl_ids(os.path.join(self.d, 'stl', name + '.stl'))['geom_hash']

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ledger.main(['--root', self.d] + list(argv))
        return code, out.getvalue(), err.getvalue()


class Mesh(Base):
    def test_a_moved_hole_changes_the_hash_and_not_the_four_numbers(self):
        a, b = ledger_mesh.stl_ids(self.stl('a', **BRACKET)), ledger_mesh.stl_ids(self.stl('b', **BRACKET_HOLE_MOVED))
        self.assertTrue(ledger_mesh.same_shape(a['shape_id'], b['shape_id']))          # triangles, volume, area, box: all equal
        self.assertNotEqual(a['geom_hash'], b['geom_hash'])

    def test_the_hash_is_of_the_frame_the_mesh_is_in(self):
        self.stl('a', **BRACKET)
        V, F = ledger_mesh.read_stl(os.path.join(self.d, 'stl', 'a.stl'))
        moved = [(x + 5, y, z) for x, y, z in V]
        self.assertNotEqual(ledger_mesh.geom_hash(V, len(F)), ledger_mesh.geom_hash(moved, len(F)))
        self.assertEqual(ledger_mesh.geom_hash(V, len(F)), ledger_mesh.geom_hash(list(reversed(V)), len(F)))

    def test_change_region_lands_on_the_moved_hole(self):
        r = ledger_mesh.change_region(self.stl('a', **BRACKET), self.stl('b', **BRACKET_HOLE_MOVED))
        self.assertTrue(r['changed']); self.assertTrue(r['sampled']); self.assertEqual(len(r['boxes']), 1)
        x0, y0, z0, x1, y1, z1 = r['boxes'][0]
        self.assertEqual((x0, x1, y0, y1), (8.0, 15.0, 13.0, 17.0))                    # old hole 8..12, new hole 11..15
        self.assertAlmostEqual(r['max_mm'], 3.0, 1)

    def test_a_face_cut_into_other_triangles_is_not_a_change(self):
        a, b = self.stl('a'), self.stl('b', flip_top=True)
        self.assertEqual(ledger_mesh.stl_ids(a)['vset_hash'], ledger_mesh.stl_ids(b)['vset_hash'])       # the same corners
        r = ledger_mesh.change_region(a, b)
        self.assertEqual((r['only_a'], r['only_b']), (2, 2)); self.assertFalse(r['changed']); self.assertEqual(r['boxes'], [])

    def test_mating_zone_by_proximity(self):
        bracket, cover, knob = self.stl('bracket', **BRACKET), self.stl('cover', **COVER), self.stl('knob', **KNOB)
        z = ledger_mesh.mating_zone(bracket, cover)
        self.assertTrue(z['touch']); self.assertAlmostEqual(z['min_gap'], 0.2, 2)
        self.assertTrue(all(b[0] >= 26.9 and b[5] == 10.0 for b in z['boxes']), z['boxes'])       # the right end of the top face
        self.assertFalse(ledger_mesh.mating_zone(bracket, knob)['touch'])
        self.assertFalse(ledger_mesh.mating_zone(bracket, cover, near=0.1)['touch'])

    def test_fit_is_sampled_and_says_what_it_does_not_know(self):
        bracket, cover, low, knob = self.stl('bracket', **BRACKET), self.stl('cover', **COVER), self.stl('low', **COVER_LOW), self.stl('knob', **KNOB)
        ok = ledger_mesh.fit(cover, bracket)
        self.assertEqual((ok['result'], ok['inside']), ('pass', 0)); self.assertAlmostEqual(ok['min_gap'], 0.2, 2)
        bad = ledger_mesh.fit(low, bracket)
        self.assertEqual(bad['result'], 'interference'); self.assertGreater(bad['inside'], 0); self.assertAlmostEqual(bad['depth'], 0.4, 1)
        self.assertEqual(ledger_mesh.fit(knob, bracket)['result'], 'apart')
        for r in (ok, bad):
            self.assertIn('model to model', r['label']); self.assertIn('shrink', r['label']); self.assertIn('hole compensation', r['label'])

    def test_inside_and_distance(self):
        V, F = ledger_mesh.read_stl(self.stl('a', **BRACKET)); g = ledger_mesh.Grid(V, F)
        self.assertTrue(g.inside((30, 5, 5))); self.assertFalse(g.inside((10, 15, 5)))             # in the plastic; in the hole
        self.assertFalse(g.inside((50, 5, 5))); self.assertFalse(g.inside((30, 5, 11)))
        self.assertAlmostEqual(g.distance((30, 15, 5), 10), 5.0); self.assertIsNone(g.distance((30, 15, 5), 1.0))

    def test_a_small_budget_makes_the_step_grow_and_says_so(self):
        r = ledger_mesh.change_region(self.stl('a', **BRACKET), self.stl('b', **BRACKET_HOLE_MOVED), budget=200)
        self.assertTrue(r['changed']); self.assertGreater(r['step'], 1.0); self.assertEqual(r['coverage'], 1.0)
        r = ledger_mesh.change_region(self.stl('a', **BRACKET), self.stl('b', **BRACKET_HOLE_MOVED), budget=40)
        self.assertLess(r['coverage'], 1.0)                                            # not every changed triangle was looked at, and the result says so


class Journal(Base):
    def test_off_without_the_folder(self):
        other = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, other)
        self.assertIsNone(ledger.find_root(other))
        self.assertEqual(ledger.find_root(self.proj), self.d); self.assertEqual(ledger.find_root(os.path.join(self.d, 'stl')), self.d)
        self.stl('bracket', **BRACKET)
        self.assertEqual(ledger.record_writes(os.path.join(other, 'x.3mf'), [self.mesh('bracket')]), [])
        self.assertEqual(os.listdir(other), [])

    def test_one_write_event_per_object(self):
        self.stl('bracket', **BRACKET); self.stl('cover', **COVER)
        out = self.write(self.mesh('bracket', index=2), self.mesh('cover', index=3), stamps={'bracket': 'demo/bracket@5b1fc0a1d2e3'}, tool='update_meshes')
        self.assertEqual([e['object'] for e in out], ['bracket', 'cover'])
        e = self.events('write')[0]
        self.assertEqual((e['v'], e['by'], e['kind'], e['project'], e['plate'], e['plate_index'], e['tool']),
                         (1, 'tool', 'write', 'bambulab/Demo.3mf', '[TODO] base - bracket', 2, 'update_meshes'))
        self.assertEqual(e['model'], self.hash('bracket')); self.assertNotEqual(e['pose'], e['model']); self.assertEqual(len(e['pose']), 40)
        self.assertEqual((e['stl'], e['stamp'], e['cad']), ('stl/bracket.stl', 'demo/bracket@5b1fc0a1d2e3', 'Fusion Demo Assembly v41'))
        self.assertEqual(len(e['stl_sha256']), 64); self.assertIsNone(self.events('write')[1]['stamp'])
        self.assertRegex(e['t'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')

    def test_an_unchanged_pose_is_not_written_again_and_a_moved_hole_is(self):
        self.stl('bracket', **BRACKET)
        self.assertEqual(len(self.write(self.mesh('bracket'))), 1); self.assertEqual(self.write(self.mesh('bracket')), [])
        self.stl('bracket', **BRACKET_HOLE_MOVED)
        self.assertEqual(len(self.write(self.mesh('bracket'))), 1)
        a, b = self.events('write')
        self.assertNotEqual(a['pose'], b['pose']); self.assertNotEqual(a['model'], b['model'])
        self.assertEqual(len(ledger.writes_of(self.d, 'bambulab/Demo.3mf', 'bracket')), 2); self.assertEqual(ledger.writes_of(self.d, obj='cover'), [])

    def test_a_carried_mesh_has_no_model(self):
        self.stl('bracket', **BRACKET)
        m = self.mesh('bracket'); m['stl'] = None
        e = self.write(m)[0]
        self.assertEqual((e['model'], e['stl'], e['stl_sha256'], e['cad']), (None, None, None, None)); self.assertEqual(len(e['pose']), 40)

    def test_record_writes_never_raises(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ledger.record_writes(self.proj, [{'object': 'broken'}]), [])
            self.assertEqual(ledger.record_writes(self.proj, None), [])
            self.assertEqual(ledger.record_writes(None, []), [])
        self.assertIn('not recorded', out.getvalue()); self.assertEqual(self.events(), [])

    def test_an_stl_outside_the_project_leaves_no_path_in_the_journal(self):
        other = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, other)
        path = write_stl(os.path.join(other, 'bracket.stl'), block(**BRACKET))
        e = self.write(self.mesh('bracket', stl=path))[0]
        self.assertEqual(e['stl'], 'bracket.stl')
        with open(ledger.events_file(self.d)) as f: self.assertNotIn(other, f.read())

    def test_the_stl_of_a_write_is_kept_in_the_local_pool(self):
        self.stl('bracket', **BRACKET); h = self.hash('bracket'); self.write(self.mesh('bracket'))
        self.stl('bracket', **BRACKET_HOLE_MOVED)                                      # the file moves on
        self.assertEqual(ledger_mesh.stl_ids(ledger.find_stl(self.d, h))['geom_hash'], h)
        local = os.path.join(self.d, 'bambulab', 'ledger', 'local')
        with open(os.path.join(local, '.gitignore')) as f: self.assertEqual(f.read(), '*\n')   # the folder ignores itself

    def test_who_may_write_what(self):
        ok = ledger.append_event(self.d, 'note', 'agent', object='bracket', text='slot filed wider at the bench')
        self.assertEqual((ok['kind'], ok['by']), ('note', 'agent'))
        for kind, by, fields in (('printed', 'tool', {}), ('note', 'stranger', {'object': 'a', 'text': 'x'}),
                                 ('said', 'owner', {'object': 'a', 'what': 'printed', 'quote': ' '}), ('said', 'agent', {'object': 'a', 'what': 'printed', 'quote': 'yes'}),
                                 ('said', 'owner', {'object': 'a', 'what': 'in-use', 'quote': 'yes'}), ('scrap', 'agent', {'object': 'a', 'quote': 'bin it'}),
                                 ('job', 'tool', {'id': 'J-1', 'basis': 'owner', 'objects': []}), ('job', 'tool', {'id': 'J-1', 'basis': 'hunch', 'objects': []}),
                                 ('write', 'tool', {'pose': 'x'})):
            with self.subTest(kind=kind, by=by, fields=fields):
                with self.assertRaises(ValueError): ledger.append_event(self.d, kind, by, **fields)
        self.assertEqual(len(self.events()), 1)

    def test_broken_lines_are_skipped_and_counted(self):
        ledger.note(self.d, 'bracket', 'one')
        with open(ledger.events_file(self.d), 'a') as f: f.write('not json\n\n{"kind": "rumour"}\n[1]\n')
        ledger.note(self.d, 'bracket', 'two')
        self.assertEqual([e['text'] for e in self.events()], ['one', 'two']); self.assertEqual(ledger.broken_lines(self.d), 3)

    def test_two_sessions_appending_at_once_lose_no_line(self):
        def work(k):
            for i in range(40): ledger.append_event(self.d, 'note', 'agent', object='part %d' % k, text='x' * 300 + ' %d' % i)
        threads = [threading.Thread(target=work, args=(k,)) for k in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(self.events('note')), 240); self.assertEqual(ledger.broken_lines(self.d), 0)

    def test_times(self):
        self.assertEqual(ledger.norm_time('2026-03-04 18:30'), '2026-03-04T18:30:00Z'); self.assertEqual(ledger.norm_time('2026-03-04T18:30:05Z'), '2026-03-04T18:30:05Z')
        with self.assertRaises(ValueError): ledger.norm_time('yesterday')
        e = ledger.append_event(self.d, 'note', 'agent', object='a', text='x', t='2026-01-02 03:04')
        self.assertEqual(e['t'], '2026-01-02T03:04:00Z')


class Status(Base):
    def test_no_evidence_is_never_called_not_printed(self):
        st = ledger.status(self.d, 'bracket')
        self.assertEqual((st['level'], st['prints'], st['journal_since']), ('never', [], None))
        self.assertIn('no evidence', st['why']); self.assertNotIn('not printed', st['why']); self.assertIn('says nothing about the past', st['why'])
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket'))
        st = ledger.status(self.d, 'bracket')
        self.assertEqual((st['level'], st['journal_since']), ('never', '2026-03-04T10:01:00Z'))
        self.assertIn('no evidence of a print since 2026-03-04T10:01:00Z', st['why']); self.assertNotIn('not printed', st['why'])
        code, out, _ = self.run_cli('status', 'bracket')
        self.assertEqual(code, 0); self.assertIn('never', out); self.assertIn('no evidence', out); self.assertNotIn('not printed', out)

    def test_only_the_owner_can_say_not_printed(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket'))
        ledger.say(self.d, 'bracket', 'not-printed', 'the bracket was never printed')
        st = ledger.status(self.d, 'bracket')
        self.assertEqual(st['level'], 'never'); self.assertIn('the owner said it is not printed', st['why']); self.assertIn('the bracket was never printed', st['why'])

    def test_a_statement_without_a_hash_is_printed_unknown_version(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket')); first = self.hash('bracket')
        self.stl('bracket', **BRACKET_HOLE_MOVED); self.write(self.mesh('bracket'))
        e = ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed and mounted')
        self.assertEqual((e['kind'], e['by'], e['what'], e['hash'], e['quote']), ('said', 'owner', 'printed', None, 'the bracket is printed and mounted'))
        st = ledger.status(self.d, 'bracket')
        self.assertEqual(st['level'], 'printed-unknown-version'); self.assertIsNone(st['prints'][0]['hash']); self.assertIsNone(st['prints'][0]['copy'])
        self.assertEqual(sorted(st['candidates']), sorted([first, self.hash('bracket')]))
        self.assertIn('not which version', st['why']); self.assertIn('the bracket is printed and mounted', st['why'])
        self.assertEqual(os.listdir(os.path.join(self.d, 'bambulab', 'ledger')).count('printed'), 0)          # no copy was guessed
        code, out, _ = self.run_cli('say', 'bracket', 'printed', '--quote', 'yes it is printed')
        self.assertEqual(code, 0); self.assertIn('VERSION UNKNOWN', out); self.assertIn(first[:8], out)

    def test_a_statement_needs_the_owners_words(self):
        for quote in ('', '   ', None):
            with self.assertRaises(ValueError): ledger.say(self.d, 'bracket', 'printed', quote)
        with self.assertRaises(ValueError): ledger.say(self.d, 'bracket', 'in-use', 'yes')
        with self.assertRaises(ValueError): ledger.snap(self.d, 'bracket', '')
        self.assertEqual(self.events(), [])

    def test_a_moved_hole_flips_printed_current_to_printed_changed(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket')); h = self.hash('bracket')
        e = ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed', h)
        self.assertEqual(e['copy'], 'bambulab/ledger/printed/bracket__%s.stl' % h[:8])
        st = ledger.status(self.d, 'bracket')
        self.assertEqual((st['level'], st['current'], st['current_from']), ('printed-current', h, 'stl'))
        self.assertEqual((st['prints'][0]['hash'], st['prints'][0]['basis'], st['prints'][0]['copy']), (h, 'owner', e['copy']))
        self.stl('bracket', **BRACKET_HOLE_MOVED)                                      # the model moves on: same triangles, volume, area, box
        st = ledger.status(self.d, 'bracket')
        self.assertEqual(st['level'], 'printed-changed'); self.assertEqual(st['current'], self.hash('bracket'))
        self.assertEqual(len(st['changed']['boxes']), 1); self.assertEqual(st['changed']['boxes'][0][:2], [8.0, 13.0]); self.assertTrue(st['changed']['sampled'])
        self.assertEqual(ledger.status(self.d, 'bracket', h)['level'], 'printed-current')          # the caller names today's hash
        self.assertEqual(ledger.status(self.d, 'bracket', self.hash('bracket'))['level'], 'printed-changed')
        code, out, _ = self.run_cli('status', 'bracket')
        self.assertIn('printed-changed', out); self.assertIn('moved since the print', out); self.assertIn('x 8.0..15.0', out); self.assertIn('model to model', out)
        code, out, _ = self.run_cli('status', '--json')
        self.assertEqual([s['level'] for s in json.loads(out)], ['printed-changed'])

    def test_an_as_printed_copy_is_never_rewritten(self):
        path = self.stl('bracket', **BRACKET); h = self.hash('bracket')
        copy = ledger.keep_printed_copy(self.d, 'bracket', path, h)
        with open(copy, 'rb') as f: before = f.read()
        self.stl('bracket', **BRACKET_HOLE_MOVED)
        with self.assertRaises(ValueError): ledger.keep_printed_copy(self.d, 'bracket', path, h)      # not the geometry that was printed
        self.assertEqual(ledger.keep_printed_copy(self.d, 'bracket', copy, h), copy)
        with open(copy, 'rb') as f: self.assertEqual(f.read(), before)
        self.assertTrue(ledger.printed_path(self.d, 'Motor plate (left)/v2', h).endswith('Motor_plate_left_v2__%s.stl' % h[:8]))

    def test_snap_keeps_the_copy_of_the_stl_as_it_is_now(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket')); h = self.hash('bracket')
        code, out, _ = self.run_cli('snap', 'bracket', '--quote', 'printing the bracket now')
        self.assertEqual(code, 0); self.assertIn(h[:8], out)
        self.assertTrue(os.path.isfile(ledger.printed_path(self.d, 'bracket', h)))
        self.assertEqual(self.events('said')[0]['hash'], h); self.assertEqual(ledger.status(self.d, 'bracket')['level'], 'printed-current')
        code, _, err = self.run_cli('snap', 'cover', '--quote', 'and the cover')
        self.assertEqual(code, 4); self.assertIn('no STL', err)

    def test_say_with_the_stl_or_a_hash_prefix(self):
        path = self.stl('bracket', **BRACKET); self.write(self.mesh('bracket')); h = self.hash('bracket')
        code, out, _ = self.run_cli('say', 'Bracket', 'printed', '--quote', 'the bracket is done', '--hash', h[:10])     # the name without regard to case
        self.assertEqual(code, 0); self.assertIn('as-printed copy', out); self.assertEqual(self.events('said')[0]['hash'], h); self.assertEqual(self.events('said')[0]['object'], 'bracket')
        code, _, err = self.run_cli('say', 'bracket', 'printed', '--quote', 'x', '--hash', 'ffffffff')
        self.assertEqual(code, 4); self.assertIn('fits 0 hashes', err)
        code, out, _ = self.run_cli('say', 'cover', 'printed', '--quote', 'the cover too', '--stl', self.stl('cover', **COVER))
        self.assertEqual(code, 0); self.assertIn('not an object of the write journal', out)
        self.assertTrue(os.path.isfile(ledger.printed_path(self.d, 'cover', self.hash('cover'))))

    def test_a_print_recorded_before_any_tool_wrote_the_part(self):
        stl = self.stl('bracket', **BRACKET); h = self.hash('bracket')                 # back-fill at adoption: the journal has no write yet
        e = ledger.say(self.d, 'bracket', 'printed', 'the bracket was printed last month', h, stl)
        self.assertEqual(e['stl'], 'stl/bracket.stl')                                  # where today's model of this object lies
        st = ledger.status(self.d, 'bracket')
        self.assertEqual((st['level'], st['current_from'], st['journal_since']), ('printed-current', 'stl', None))
        self.stl('bracket', **BRACKET_HOLE_MOVED)
        self.assertEqual(ledger.status(self.d, 'bracket')['level'], 'printed-changed')
        ledger.say(self.d, 'cover', 'printed', 'the cover too', 'c' * 40)              # a hash, but no file anywhere
        st = ledger.status(self.d, 'cover')
        self.assertEqual(st['level'], 'ask'); self.assertIn('give --stl', st['why']); self.assertTrue(st['need_stl'])
        new = write_stl(os.path.join(self.d, 'cover_new.stl'), block(**COVER))
        r = ledger.may_change(self.d, 'cover', new)                                    # with the STL the answer is about geometry, not a shrug
        self.assertEqual((r['verdict'], r['level']), ('ASK-OWNER', 'printed-changed')); self.assertIn("'bracket'", r['why'])
        r = ledger.may_change(self.d, 'cover')
        self.assertEqual(r['verdict'], 'ASK-OWNER'); self.assertIn('no geometry', r['why'])

    def test_scrap_and_a_reprint(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket')); h = self.hash('bracket')
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed', h)
        code, out, _ = self.run_cli('scrap', 'bracket', '--quote', 'the bracket cracked, bin it')
        self.assertEqual(code, 0); self.assertEqual(self.events('scrap')[0]['quote'], 'the bracket cracked, bin it')
        st = ledger.status(self.d, 'bracket')
        self.assertEqual(st['level'], 'never'); self.assertIn('scrapped', st['why']); self.assertEqual(st['ignored'][0]['hash'], h)
        ledger.say(self.d, 'bracket', 'printed', 'printed it again', h)
        self.assertEqual(ledger.status(self.d, 'bracket')['level'], 'printed-current')
        self.assertEqual(self.run_cli('say', 'bracket', 'scrapped', '--quote', 'that one broke too')[0], 0)
        self.assertEqual(ledger.status(self.d, 'bracket')['level'], 'never'); self.assertEqual(len(self.events('scrap')), 2)

    def test_the_level_follows_the_last_print_and_names_the_older_ones(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket')); old = self.hash('bracket')
        ledger.say(self.d, 'bracket', 'printed', 'first bracket printed', old)
        self.stl('bracket', **BRACKET_HOLE_MOVED); self.write(self.mesh('bracket')); new = self.hash('bracket')
        ledger.say(self.d, 'bracket', 'printed', 'reprinted with the hole moved', new)
        st = ledger.status(self.d, 'bracket')
        self.assertEqual(st['level'], 'printed-current'); self.assertIn(old[:8], st['why']); self.assertIn('say scrapped', st['why'])
        ledger.say(self.d, 'bracket', 'scrapped', 'the first one is in the bin', old)
        self.assertNotIn('older prints', ledger.status(self.d, 'bracket')['why'])

    def test_a_job_without_the_owners_word_is_a_question(self):
        self.stl('bracket', **BRACKET); w = self.write(self.mesh('bracket', index=2))[0]
        ledger.append_event(self.d, 'job', 'agent', id='J-20260304-1830-p2', source='studio_cache', sent_at='2026-03-04T18:30:00Z', project='bambulab/Demo.3mf',
                            plate_index=2, objects=[{'object': 'bracket', 'pose': w['pose']}], basis='picture')
        st = ledger.status(self.d, 'bracket')
        self.assertEqual(st['level'], 'ask'); self.assertIn('A sent job is not a kept part', st['why']); self.assertFalse(st['prints'][0]['sure'])
        self.assertEqual(st['prints'][0]['hash'], self.hash('bracket'))                # the model hash comes from the write with that pose
        self.assertEqual(ledger.may_change(self.d, 'bracket')['verdict'], 'ASK-OWNER')
        self.clock.t = datetime.datetime(2026, 3, 5, 8, 0, 0)
        ledger.say(self.d, 'bracket', 'printed', 'yes, that bracket is on the frame')    # his word, no version: the one job gives it
        st = ledger.status(self.d, 'bracket')
        self.assertEqual((st['level'], len(st['prints']), st['prints'][0]['basis']), ('printed-current', 1, 'owner+job'))
        ledger.say(self.d, 'bracket', 'not-printed', 'no wait, that job was cancelled')
        self.assertEqual(ledger.status(self.d, 'bracket')['level'], 'never')

    def test_a_statement_after_another_version_was_written_does_not_take_the_old_hash(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket')); old = self.hash('bracket')
        ledger.say(self.d, 'bracket', 'printed', 'bracket printed', old)
        self.stl('bracket', **BRACKET_HOLE_MOVED); self.write(self.mesh('bracket'))
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed')             # which one? the journal cannot know
        self.assertEqual(ledger.status(self.d, 'bracket')['level'], 'printed-unknown-version')

    def test_notes_show_in_the_status(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket'))
        self.assertEqual(self.run_cli('note', 'bracket', 'vent slot filed wider at the bench')[0], 0)
        self.assertEqual(self.events('note')[0]['by'], 'agent')
        self.assertIn('vent slot filed wider', self.run_cli('status', 'bracket')[1])


class Plates(Base):
    def project(self, plates):
        """A .3mf with only what the ledger reads: plates and object names."""
        objs, cfg, n = {}, ['<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'], 1
        for _, names in plates:
            for name in names:
                n += 1; objs[name] = n
                cfg.append('  <object id="%d">\n    <metadata key="name" value="%s"/>\n    <metadata key="extruder" value="1"/>\n'
                           '    <part id="1" subtype="normal_part">\n      <metadata key="name" value="%s.stl"/>\n    </part>\n  </object>\n' % (n, name.replace('&', '&amp;'), name))
        for i, (plate, names) in enumerate(plates):
            cfg.append('  <plate>\n    <metadata key="plater_id" value="%d"/>\n    <metadata key="plater_name" value="%s"/>\n%s  </plate>\n' % (
                i + 1, plate, ''.join('    <model_instance>\n      <metadata key="object_id" value="%d"/>\n    </model_instance>\n' % objs[x] for x in names)))
        with zipfile.ZipFile(self.proj, 'w') as z: z.writestr('Metadata/model_settings.config', ''.join(cfg) + '</config>\n')

    def test_plates_1_2_are_printed_becomes_a_question_and_not_rows(self):
        self.stl('bracket', **BRACKET); self.stl('cover', **COVER); self.stl('knob', **KNOB)
        self.write(self.mesh('bracket', index=1), self.mesh('cover', index=2), self.mesh('knob', index=3))
        self.write(self.mesh('knob', index=2, shift=(50.0, 50.0, 0.0)), self.mesh('cover', index=3, shift=(50.0, 50.0, 0.0)))    # later the knob took plate 2
        self.project([('[TODO] base - bracket', ['bracket']), ('[TODO] base - knob', ['knob']), ('[TODO] base - cover', ['cover'])])   # renumbered in the slicer since
        code, out, _ = self.run_cli('say', '--plates', '1,2', '--project', 'bambulab/Demo.3mf', '--quote', 'plates 1, 2 are printed')
        self.assertEqual(code, 2)
        self.assertEqual(self.events('said'), []); self.assertEqual(self.events('job'), [])
        self.assertIn('nothing was recorded as printed', out); self.assertIn('which OBJECTS', out)
        for name in ('bracket', 'cover', 'knob'): self.assertIn(repr(name), out)       # every candidate of plates 1 and 2, then and now
        self.assertIn("in the file now: 'knob'", out); self.assertIn('until', out)
        n = self.events('note')
        self.assertEqual(len(n), 1); self.assertIn('plates 1, 2 are printed', n[0]['text']); self.assertEqual(n[0]['plates'], [1, 2])
        for name in ('bracket', 'cover', 'knob'): self.assertEqual(ledger.status(self.d, name)['level'], 'never')

    def test_a_quote_about_plate_numbers_is_not_booked_to_an_object_the_agent_picked(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket', index=1))
        code, out, _ = self.run_cli('say', 'bracket', 'printed', '--quote', 'Plates 01, 02 are printed')
        self.assertEqual(code, 2); self.assertEqual(self.events('said'), []); self.assertIn('speaks of plate numbers', out)
        self.assertEqual(self.run_cli('say', 'plates:1,2', 'printed', '--quote', 'they are done')[0], 2); self.assertEqual(self.events('said'), [])
        code, out, _ = self.run_cli('say', 'bracket', 'printed', '--quote', 'the bracket on plate 1 is printed')      # he names the object
        self.assertEqual(code, 0); self.assertEqual(len(self.events('said')), 1)

    def test_read_plates(self):
        self.project([('[TODO] base - bracket', ['bracket']), ('pair', ['cover', 'nut & bolt'])])
        self.assertEqual(ledger.read_plates(self.proj), [{'plate_index': 1, 'plate': '[TODO] base - bracket', 'objects': ['bracket']},
                                                         {'plate_index': 2, 'plate': 'pair', 'objects': ['cover', 'nut & bolt']}])

    def test_read_plates_of_a_project_the_plugin_built(self):
        for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
            if d not in sys.path: sys.path.insert(0, d)
        import bambu3mf
        from test_tools import settings, PS
        tpl = os.path.join(self.d, 'template.3mf')
        with zipfile.ZipFile(tpl, 'w') as z:
            z.writestr('[Content_Types].xml', '<Types/>'); z.writestr('_rels/.rels', '<Relationships/>'); z.writestr(PS, json.dumps(settings()))
        self.stl('bracket', **BRACKET); self.stl('cover', **COVER)
        proj = os.path.join(self.d, 'built.3mf')
        with contextlib.redirect_stdout(io.StringIO()):
            bambu3mf.new_project(tpl, proj, 'PLA')
            bambu3mf.build(proj, os.path.join(self.d, 'stl'), [('[TODO] base - bracket', [('Bracket', 'bracket.stl', False)]), ('[TODO] lid', [('Cover', 'cover.stl', False)])])
        self.assertEqual([(p['plate_index'], p['plate'], p['objects']) for p in ledger.read_plates(proj)],
                         [(1, '[TODO] base - bracket', ['Bracket']), (2, '[TODO] lid', ['Cover'])])
        # the writer told the ledger what it wrote (bambu3mf.save -> ledger.record_writes): one line per object
        ws = self.events('write')
        self.assertEqual([(w['object'], w['plate'], w['plate_index'], w['project'], w['stl'], w['tool']) for w in ws],
                         [('Bracket', '[TODO] base - bracket', 1, 'built.3mf', 'stl/bracket.stl', 'build'), ('Cover', '[TODO] lid', 2, 'built.3mf', 'stl/cover.stl', 'build')])
        self.assertEqual([w['model'] for w in ws], [self.hash('bracket'), self.hash('cover')])
        self.assertEqual([r['status'] for r in ledger.plate_status(self.d, proj)], ['[TODO]', '[TODO]'])
        ledger.snap(self.d, 'Bracket', 'the bracket is printing now')
        self.assertEqual([r['status'] for r in ledger.plate_status(self.d, proj)], ['[OK]', '[TODO]'])

    def test_plate_status_is_a_proposal_from_the_record(self):
        self.stl('bracket', **BRACKET); self.stl('cover', **COVER); self.stl('knob', **KNOB); self.stl('lever', **KNOB)
        self.write(self.mesh('bracket'), self.mesh('cover', index=2), self.mesh('knob', index=3), self.mesh('lever', index=4))
        ledger.say(self.d, 'bracket', 'printed', 'bracket printed', self.hash('bracket'))
        ledger.say(self.d, 'cover', 'printed', 'cover printed', self.hash('cover'))
        ledger.say(self.d, 'lever', 'printed', 'the lever is printed')                 # no version
        self.stl('cover', size=(12.0, 30.0, 6.0), origin=(28.0, 0.0, 10.2))            # the cover changes after its print
        self.project([('[TODO] base - bracket', ['bracket']), ('[OK] lid', ['cover']), ('[TODO] knob', ['knob']), ('[OK] lever', ['lever']), ('mixed', ['bracket', 'knob'])])
        rows = ledger.plate_status(self.d, self.proj)
        self.assertEqual([(r['plate_index'], r['status'], r['now']) for r in rows],
                         [(1, '[OK]', '[TODO]'), (2, '[CHANGED]', '[OK]'), (3, '[TODO]', '[TODO]'), (4, None, '[OK]'), (5, '[TODO]', None)])
        self.assertEqual(rows[0]['objects'], [{'object': 'bracket', 'level': 'printed-current'}])
        self.assertIn("'lever'", rows[3]['ask']); self.assertNotIn('ask', rows[0])
        code, out, _ = self.run_cli('plates', 'bambulab/Demo.3mf')
        self.assertEqual(code, 0); self.assertIn('[CHANGED]', out); self.assertIn('ASK', out); self.assertIn('a proposal only', out); self.assertIn('no evidence of a print', out)
        with open(self.proj, 'rb') as f: before = f.read()
        self.assertEqual(json.loads(self.run_cli('plate-status', self.proj, '--json')[1])[1]['status'], '[CHANGED]')
        with open(self.proj, 'rb') as f: self.assertEqual(f.read(), before)             # read only

    def test_without_the_file_the_plates_come_from_the_write_journal(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket', index=7))
        rows = ledger.plate_status(self.d, self.proj)
        self.assertEqual([(r['plate_index'], r['status'], r['source']) for r in rows], [(7, '[TODO]', 'write-journal')])


class MayChange(Base):
    """bracket: 40 x 30 x 10 with a hole at x 10. cover: lies on its right end (x 28..40), 0.2 above. knob: far away."""

    def setUp(self):
        Base.setUp(self)
        self.stl('bracket', **BRACKET); self.stl('cover', **COVER); self.stl('knob', **KNOB)
        self.write(self.mesh('bracket'), self.mesh('cover', index=2), self.mesh('knob', index=3))
        self.new = lambda **shape: write_stl(os.path.join(self.d, 'new.stl'), block(**shape))

    def test_free_nothing_printed(self):
        r = ledger.may_change(self.d, 'bracket')
        self.assertEqual((r['verdict'], r['level']), ('FREE', 'never')); self.assertIn('no evidence of a print', r['why'])
        self.assertEqual([z['object'] for z in r['zones']], ['cover'])                 # the neighbour is seen, it is just not printed
        self.assertEqual(self.run_cli('may-change', 'bracket')[0], 0)
        self.assertEqual(ledger.may_change(self.d, 'bracket', self.new(**BRACKET_HOLE_MOVED))['verdict'], 'FREE')

    def test_free_reprint_printed_and_the_change_is_away_from_every_interface(self):
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed', self.hash('bracket'))
        before = ledger.may_change(self.d, 'bracket')                                  # read before the edit: the keep-out boxes
        self.assertEqual(before['verdict'], 'FREE-REPRINT'); self.assertIn('keep-out', '\n'.join(before['rows'])); self.assertIn('Run again with --stl', before['why'])
        r = ledger.may_change(self.d, 'bracket', self.new(**BRACKET_HOLE_MOVED))       # the hole at x 10 moves 3 mm: the cover is at x 28..40
        self.assertEqual((r['verdict'], r['level']), ('FREE-REPRINT', 'printed-current'))
        self.assertEqual(len(r['changed']['boxes']), 1); self.assertIn('reprint', r['why']); self.assertIn('not touched', '\n'.join(r['rows']))
        code, out, _ = self.run_cli('may-change', 'bracket', '--stl', os.path.join(self.d, 'new.stl'))
        self.assertEqual(code, 0); self.assertIn('FREE-REPRINT', out)
        self.assertEqual(ledger.may_change(self.d, 'bracket', self.new(**BRACKET))['verdict'], 'FREE')        # the geometry that was printed

    def test_ask_owner_printed_and_the_change_touches_its_interface(self):
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed', self.hash('bracket'))
        r = ledger.may_change(self.d, 'bracket', self.new(hole=(34.0, 15.0, 2.0)))     # the hole now lies under the cover
        self.assertEqual(r['verdict'], 'ASK-OWNER'); self.assertIn("'cover'", r['why']); self.assertIn('reprint it', r['why'])
        code, out, _ = self.run_cli('may-change', 'bracket', '--stl', os.path.join(self.d, 'new.stl'))
        self.assertEqual(code, 2); self.assertIn('ASK-OWNER', out); self.assertIn('TOUCHED by the change', out)
        self.stl('bracket', hole=(34.0, 15.0, 2.0))                                    # the same edit already in the model, no --stl
        self.assertEqual(ledger.may_change(self.d, 'bracket')['verdict'], 'ASK-OWNER')

    def test_ask_owner_when_the_version_is_unknown(self):
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed')
        r = ledger.may_change(self.d, 'bracket', self.new(**BRACKET_HOLE_MOVED))
        self.assertEqual((r['verdict'], r['level']), ('ASK-OWNER', 'printed-unknown-version')); self.assertIn('which version', r['why'])
        self.assertEqual(self.run_cli('may-change', 'bracket')[0], 2)

    def test_fit_a_part_that_meets_a_printed_neighbour(self):
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed', self.hash('bracket'))
        before = ledger.may_change(self.d, 'cover')                                    # before the edit: it meets a printed part
        self.assertEqual((before['verdict'], before['level']), ('FIT', 'never')); self.assertIn('AS-PRINTED', before['why'])
        self.assertEqual([(z['object'], z['printed']) for z in before['zones']], [('bracket', True)])
        r = ledger.may_change(self.d, 'cover', self.new(**COVER_LOW))                  # the cover moves 0.6 down: into the printed bracket
        self.assertEqual(r['verdict'], 'FIT')
        f = r['fits'][0]
        self.assertEqual((f['object'], f['result']), ('bracket', 'interference')); self.assertGreater(f['inside'], 0)
        self.assertIn('model to model', f['label']); self.assertIn('model to model', '\n'.join(r['rows'])); self.assertIn('hole compensation', '\n'.join(r['rows']))
        self.assertEqual(self.run_cli('may-change', 'cover', '--stl', os.path.join(self.d, 'new.stl'))[0], 2)
        far = ledger.may_change(self.d, 'cover', self.new(size=(12.0, 30.0, 8.0), origin=(28.0, 0.0, 10.2)))      # only the top grows: 5 mm above the interface
        self.assertEqual(far['verdict'], 'FREE'); self.assertIn('touches no zone', far['why'])
        self.assertEqual(ledger.may_change(self.d, 'knob')['verdict'], 'FREE')         # nothing printed near the knob

    def test_fit_is_against_the_as_printed_copy_not_todays_model(self):
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed', self.hash('bracket'))
        self.stl('bracket', size=(40.0, 30.0, 9.0), **BRACKET)                         # after the print the model got 1 mm lower
        r = ledger.may_change(self.d, 'cover', self.new(size=(12.0, 30.0, 5.0), origin=(28.0, 0.0, 9.5)))       # fits the new model, not the part on the desk
        self.assertEqual(r['verdict'], 'FIT')
        f = r['fits'][0]
        self.assertEqual(f['result'], 'interference'); self.assertEqual(f['model']['result'], 'pass'); self.assertAlmostEqual(f['model']['min_gap'], 0.5, 1)
        self.assertIn("'bracket' printed differs from its model", r['why'])

    def test_a_guess_about_a_neighbour_still_asks_for_the_fit(self):
        w = ledger.writes_of(self.d, obj='bracket')[0]
        ledger.append_event(self.d, 'job', 'tool', id='J-20260304-1830-p1', source='studio_cache', sent_at='2026-03-04T18:30:00Z', project='bambulab/Demo.3mf',
                            plate_index=1, objects=[{'object': 'bracket', 'pose': w['pose'], 'model': w['model']}], basis='write-journal')
        ledger.keep_printed_copy(self.d, 'bracket', os.path.join(self.d, 'stl', 'bracket.stl'), w['model'])
        r = ledger.may_change(self.d, 'cover')
        self.assertEqual(r['verdict'], 'FIT'); self.assertIn('a guess', '\n'.join(r['rows']))

    def test_a_printed_neighbour_without_a_copy_goes_to_the_owner(self):
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed')             # no version, so no copy to test against
        r = ledger.may_change(self.d, 'cover', self.new(**COVER_LOW))
        self.assertEqual(r['verdict'], 'ASK-OWNER'); self.assertIn('which version', r['why'])

    def test_the_answer_names_its_limit(self):
        doc = ledger.may_change.__doc__
        self.assertIn('exported pose', doc); self.assertIn('neither shrink nor hole compensation', doc)


class CommandLine(Base):
    def test_off_here_is_exit_4(self):
        other = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, other)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = ledger.main(['--root', other, 'status'])
        self.assertEqual(code, 4); self.assertIn('the ledger is off here', err.getvalue())

    def test_wrong_arguments_are_exit_4_never_2(self):
        for argv in ([], ['frobnicate'], ['say', 'bracket', 'printed'], ['say', 'bracket', 'maybe', '--quote', 'x'], ['may-change'], ['tie', 'J-1'], ['scrap', 'bracket']):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 4); self.assertIn('LEDGER ERROR', err)
        self.assertEqual(self.events(), [])

    def test_a_name_the_journal_does_not_know_is_not_answered_as_free(self):
        self.stl('bracket left', **BRACKET); self.stl('bracket right', **COVER)
        self.write(self.mesh('bracket left'), self.mesh('bracket right', index=2))
        code, _, err = self.run_cli('may-change', 'braket left')                       # a typo must not come back as FREE
        self.assertEqual(code, 4); self.assertIn("no object named 'braket left'", err); self.assertIn("'bracket left'", err)
        code, _, err = self.run_cli('status', 'bracket')
        self.assertEqual(code, 4); self.assertIn('fits several objects', err)
        code, out, _ = self.run_cli('status', 'gizmo')
        self.assertEqual(code, 0); self.assertIn("the journal has no object named 'gizmo'", out)
        self.assertEqual(self.run_cli('status', 'LEFT')[0], 0)                         # one object contains it
        new = write_stl(os.path.join(self.d, 'gizmo.stl'), block(**KNOB))              # a part that is new comes with its STL
        code, out, _ = self.run_cli('may-change', 'gizmo', '--stl', new)
        self.assertEqual(code, 0); self.assertIn('FREE', out)
        code, _, err = self.run_cli('plates', 'bambulab/Missing.3mf')
        self.assertEqual(code, 4); self.assertIn('not a file', err)
        code, _, err = self.run_cli('say', 'gizmo', 'printed', '--quote', 'done', '--stl', os.path.join(self.d, 'nothere.stl'))
        self.assertEqual(code, 4); self.assertEqual(self.events('said'), [])

    def test_the_vendored_flat_copy_runs_as_a_script(self):
        import subprocess
        tools = os.path.join(self.d, 'bambulab', 'tools'); os.makedirs(tools)
        for src in ('tools/ledger/ledger.py', 'tools/ledger/ledger_mesh.py', 'tools/ledger/ledger_cache.py', 'tools/bambu/meshid.py'):
            shutil.copy(os.path.join(ROOT, src), tools)
        stl = self.stl('bracket', **BRACKET)
        run = lambda *a, **kw: subprocess.run([sys.executable, os.path.join(tools, 'ledger.py')] + list(a), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              cwd=kw.get('cwd', os.path.join(self.d, 'stl')), env=dict(os.environ, PYTHONPATH=''))
        r = run('snap', 'bracket', '--stl', stl, '--quote', 'the bracket is printing now')      # the root is found from the folder it is run in
        self.assertEqual((r.returncode, r.stderr.decode()), (0, '')); self.assertIn('as-printed copy bambulab/ledger/printed/bracket__', r.stdout.decode())
        r = run('status', 'bracket', '--stl', stl)
        self.assertEqual(r.returncode, 0); self.assertIn('printed-current', r.stdout.decode())
        r = run('may-change', 'bracket', '--stl', write_stl(os.path.join(self.d, 'new.stl'), block(**BRACKET_HOLE_MOVED)))
        self.assertEqual(r.returncode, 0); self.assertIn('FREE-REPRINT', r.stdout.decode())
        self.assertEqual(run('jobs').returncode, 0); self.assertEqual(run('doctor').returncode, 0)
        other = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, other)
        r = run('status', cwd=other)
        self.assertEqual(r.returncode, 4); self.assertIn('the ledger is off here', r.stderr.decode())

    def test_init_turns_the_ledger_on_once(self):
        other = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, other)
        with open(os.path.join(other, '.gitignore'), 'w') as f: f.write('__pycache__/')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ledger.main(['--root', other, 'init']), 0)
        self.assertIn('created bambulab/ledger/', out.getvalue()); self.assertEqual(ledger.find_root(other), other)
        with open(os.path.join(other, '.gitignore')) as f: self.assertEqual(f.read(), '__pycache__/\nbambulab/ledger/local.json\nbambulab/ledger/local/\n')
        with open(os.path.join(other, '.gitattributes')) as f: self.assertEqual(f.read(), 'bambulab/ledger/events.jsonl merge=union\n')
        self.assertEqual(ledger.init(other), ['the ledger was already on here'])
        with open(os.path.join(other, '.gitignore')) as f: self.assertEqual(f.read().count('local.json'), 1)

    def test_doctor(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket')); h = self.hash('bracket')
        ledger.say(self.d, 'bracket', 'printed', 'the bracket is printed', h); ledger.say(self.d, 'cover', 'printed', 'the cover is printed')
        code, out, _ = self.run_cli('doctor')
        self.assertEqual(code, 0)
        for text in ('1 write, 0 job, 2 said', 'first write 2026-03-04T10:01:00Z', 'cache reader is OFF', '1 as-printed STL', 'owner    cover', '"no evidence" is not "not printed"', 'PROBLEMS 0'):
            self.assertIn(text, out)
        write_stl(ledger.printed_path(self.d, 'bracket', h), block(**BRACKET_HOLE_MOVED))          # somebody overwrote a copy
        with open(ledger.events_file(self.d), 'a') as f: f.write('half a li')
        code, out, _ = self.run_cli('doctor')
        self.assertIn('an as-printed copy was rewritten', out); self.assertIn('not an event', out); self.assertIn('PROBLEMS 2', out)

    def test_doctor_warns_about_an_account_id_in_the_project_file_without_printing_it(self):
        self.stl('bracket', **BRACKET); self.write(self.mesh('bracket'))
        with zipfile.ZipFile(self.proj, 'w') as z:
            z.writestr('3D/3dmodel.model', '<model><metadata name="Application">Slicer</metadata><metadata name="DesignerUserId">4815162342</metadata></model>')
        out = self.run_cli('doctor')[1]
        self.assertIn('bambulab/Demo.3mf carries the slicer account', out); self.assertNotIn('4815162342', out)
        with zipfile.ZipFile(self.proj, 'w') as z: z.writestr('3D/3dmodel.model', '<model><metadata name="DesignerUserId"></metadata></model>')
        self.assertNotIn('DesignerUserId', self.run_cli('doctor')[1])

    def test_the_skill_names_commands_that_exist_and_the_limits(self):
        import re
        with open(os.path.join(ROOT, 'skills', 'print-ledger', 'SKILL.md'), encoding='utf-8') as f: skill = f.read()
        commands = set(ledger.parser()._subparsers._group_actions[0].choices)
        used = set(re.findall(r'ledger\.py ([a-z][a-z-]+)', skill))
        self.assertTrue(used); self.assertEqual(used - commands, set())
        for name in ('say', 'scrap', 'note', 'status', 'may-change', 'snap', 'jobs', 'tie', 'plates', 'doctor'): self.assertIn(name, commands)
        for level in ledger.LEVELS + ledger.MAY_CHANGE: self.assertIn('`%s`' % level, skill)
        for text in ('"No evidence" is not "not printed"', 'A sent job is not a kept part', 'model to model', 'never starts, stops or changes a print',
                     'off by default', 'Only the owner switches it on', 'becomes a question, not rows'):
            self.assertIn(text, skill)
        self.assertFalse(any('\u0400' <= c <= '\u04ff' for c in skill))

    def test_the_tools_say_what_they_never_do(self):
        self.assertIn('never\nstarts, stops or changes a print', ledger.__doc__)
        with open(os.path.join(ROOT, 'tools', 'ledger', 'ledger.py'), encoding='utf-8') as f: text = f.read()
        for word in ('socket', 'urllib', 'http.client', 'requests', 'mqtt'): self.assertNotIn(word, text)       # no network code at all


if __name__ == '__main__':
    unittest.main()
