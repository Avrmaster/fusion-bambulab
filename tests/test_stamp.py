"""Stamps: how one is built and stored, every way one dies, and the export that earns one. No Fusion: the Fusion unit,
the host probes and the plan are played by fakes; the registry is the shipped one."""
import contextlib, copy, io, os, shutil, struct, sys, tempfile, types, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
    if d not in sys.path: sys.path.insert(0, d)
import gate_common, gate_features, gate_judges, gate_params, gate_registry, gate_stamp
import gate_attest, gate_fusion_rpc, gate_plan, gate_report
from probes import stl_mesh, stl_walls
from gate_common import GateError
import meshid

TARGET = {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'}
FINE = {'nozzle': 0.4, 'line_width': 0.42, 'material': 'PETG'}
SIG1, SIG2, SIG3 = 'a1' * 32, 'b2' * 32, 'c3' * 32


def plate(hx=10.0, hy=7.0, d=4.0, size=(30.0, 20.0, 5.0)):
    """A closed plate with a square through hole of side d centred at (hx, hy)."""
    X, Y, Z = size; h = d / 2
    o = [(0, 0), (X, 0), (X, Y), (0, Y)]; i = [(hx - h, hy - h), (hx + h, hy - h), (hx + h, hy + h), (hx - h, hy + h)]
    V = [(float(x), float(y), float(z)) for z in (0, Z) for x, y in o + i]; F = []
    for k in range(4):
        a, b = k, (k + 1) % 4
        F += [(a, 4 + a, 4 + b), (a, 4 + b, b), (8 + a, 12 + b, 12 + a), (8 + a, 8 + b, 12 + b),
              (a, b, 8 + b), (a, 8 + b, 8 + a), (4 + a, 12 + b, 4 + b), (4 + a, 12 + a, 12 + b)]
    return V, F


def write_stl(path, V, F, header=b'Demo Assembly v41'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(header.ljust(80, b'\0') + struct.pack('<I', len(F)))
        for t in F: f.write(struct.pack('<12fH', 0, 0, 0, *[c for i in t for c in V[i]], 0))
    return path


def read(path):
    with open(path) as f: return f.read()


def changed_judge(facts, params, ctx):
    """Stands in for a judge whose source text was edited after a stamp was written."""
    return []


class Gated(unittest.TestCase):
    """A gated project in a temp folder: one document, one .3mf project with a declared target, one generator."""
    mode = 'observe'

    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d, True)
        self.set_project(self.mode)
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {
            'documents': [{'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': 'stl'}],
            'projects': [{'key': 'main', 'file': 'bambulab/Demo.3mf', 'stl_dir': 'stl'}],
            'generators': [{'part': 'demo/shim', 'stl': 'stl/shim.stl', 'source': 'tools/make_shim.py'}]})
        self.reg = gate_registry.load_default(); self.resolved = gate_params.resolve(self.reg, None, TARGET)

    def set_project(self, mode, targets=None):
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'),
                               {'mode': mode, 'not_printed': ['^ref '], 'targets': {'main': TARGET} if targets is None else targets})

    def stl(self, name, **kw): return write_stl(os.path.join(self.d, 'stl', name + '.stl'), *plate(**kw))

    def census(self, part, sig, **more):
        c = gate_common.load_census(self.d)
        c['parts'][part] = dict({'doc': part.split('/')[0], 'name': part.split('/')[1], 'source': 'fusion', 'sig': sig, 'prev_sig': None, 'gone': False,
                                 'component_id': 'c-1', 'occurrence': 'Bracket:1', 'body': 'Body1'}, **more)
        gate_common.write_json(gate_common.census_path(self.d), c)

    def judged(self, source='fusion', reg=None, resolved=None, **findings):
        """A clean judgement of every rule a stamp of such a part needs; findings: rule id with '_' for '.' -> [finding]."""
        reg, resolved = reg or self.reg, resolved or self.resolved
        return {rid: {'judge_hash': gate_judges.judge_hash(reg['rules'][rid]['check']['judge']),
                      'params': gate_params.values(resolved, reg['rules'][rid]['params']), 'findings': findings.get(rid.replace('.', '_'), [])}
                for rid in gate_stamp.applicable_rules(reg, {'source': source})}

    def stamp(self, part='demo/bracket', sig=SIG1, name='bracket', target=TARGET, census=True, write=True, time=None, reg=None, **kw):
        """Export-in-short: an STL, a census entry, a clean judgement, the stamp."""
        path = os.path.join(self.d, 'stl', name + '.stl')
        if kw or not os.path.exists(path): self.stl(name, **kw)
        if census: self.census(part, sig, stl='stl/%s.stl' % name)
        s = gate_stamp.build(self.d, reg or self.reg, self.resolved if target == TARGET else gate_params.resolve(self.reg, None, target),
                             part, sig, self.judged(reg=reg, resolved=None if target == TARGET else gate_params.resolve(self.reg, None, target)),
                             path, target, doc_version=41, run='20260304T101500Z-3fa2')
        if time: s['time'] = time
        if write: gate_stamp.write(self.d, s, supersede=True)
        return s

    def verify(self, stamp, **kw): return gate_stamp.verify(self.d, self.reg, stamp, **kw)


class Build(Gated):
    def test_a_stamp_holds_every_block_rule_of_design_and_export_and_the_geometry(self):
        s = self.stamp()
        self.assertEqual(sorted(s), sorted(gate_stamp.STAMP_KEYS))
        self.assertEqual(sorted(s['checks']), gate_stamp.applicable_rules(self.reg, {'source': 'fusion'}))
        self.assertIn('inserts.wall', s['checks']); self.assertIn('mesh.closed', s['checks']); self.assertNotIn('project.mesh_stale', s['checks'])
        c = s['checks']['inserts.wall']
        self.assertEqual((c['result'], c['judge_hash'], c['params']['inserts.wall_target']), ('pass', gate_judges.judge_hash('inserts_wall'), 4.0))
        ids = meshid.stl_ids(os.path.join(self.d, 'stl', 'bracket.stl'))
        self.assertEqual((s['stl'], s['stl_sha256'], s['geom_hash']), ('stl/bracket.stl', ids['sha256'], ids['geom_hash']))
        self.assertEqual((s['verdict'], s['attested'], s['target'], s['doc_version'], s['part'], s['sig']), ('pass', 0, TARGET, 41, 'demo/bracket', SIG1))
        self.assertEqual(gate_stamp.name_of(s), 'demo/bracket@a1a1a1a1a1a1')

    def test_a_generated_part_needs_only_the_rules_that_work_on_an_stl(self):
        rules = gate_stamp.applicable_rules(self.reg, {'source': 'generated'})
        self.assertIn('walls.min_material', rules); self.assertNotIn('inserts.wall', rules)
        self.assertEqual(gate_stamp.applicable_rules(self.reg, {}), gate_stamp.applicable_rules(self.reg, {'source': 'fusion'}))
        self.assertEqual([r for r in gate_stamp.applicable_rules(self.reg, {}, stages=('project',)) if not r.startswith('project.')],
                         ['print.overhang_angle', 'print.slice_support_growth', 'walls.placed_stl'])      # judged on the .3mf as it lies on its plates
        path = self.stl('shim'); sig = meshid.stl_ids(path)['geom_hash']
        s = gate_stamp.build(self.d, self.reg, self.resolved, 'demo/shim', sig, self.judged('generated'), path, TARGET)      # not in the census: the layout names it
        self.assertEqual(sorted(s['checks']), rules)
        gate_stamp.write(self.d, s); self.assertEqual(self.verify(s, geom_hash=sig)[0], 'valid')

    def test_no_stamp_without_a_judgement_or_with_an_open_finding(self):
        path = self.stl('bracket'); self.census('demo/bracket', SIG1)
        j = self.judged(); del j['inserts.wall']
        with self.assertRaisesRegex(GateError, 'inserts.wall has no judgement'): gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, j, path, TARGET)
        f = gate_judges.finding('inserts.wall', 'demo/bracket', 'GROW', 'pocket at (12, -30, 40): wall 2.5 under 4.0', measure=2.5, limit=4.0)
        with self.assertRaisesRegex(GateError, '1 open finding.*wall 2.5 under 4.0'):
            gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, self.judged(inserts_wall=[f]), path, TARGET)
        info = gate_judges.finding('inserts.wall', 'demo/bracket', 'INFO', 'a note')
        self.assertEqual(gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, self.judged(inserts_wall=[info]), path, TARGET)['checks']['inserts.wall']['result'], 'pass')
        with self.assertRaisesRegex(GateError, 'does not exist'): gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, self.judged(), path + '.none', TARGET)
        with self.assertRaises(GateError): gate_stamp.build(self.d, self.reg, self.resolved, 'bracket', SIG1, self.judged(), path, TARGET)

    def test_a_judgement_by_an_older_judge_or_with_other_numbers_is_refused(self):
        path = self.stl('bracket'); self.census('demo/bracket', SIG1)
        j = self.judged(); j['inserts.wall']['judge_hash'] = '0' * 16
        with self.assertRaisesRegex(GateError, 'older judge'): gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, j, path, TARGET)
        j = self.judged(); j['inserts.wall']['params']['inserts.wall_target'] = 2.0
        with self.assertRaisesRegex(GateError, 'other values of inserts.wall_target'): gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, j, path, TARGET)

    def test_a_waived_finding_is_waived_and_an_answered_one_is_attested(self):
        path = self.stl('bracket'); self.census('demo/bracket', SIG1)
        a = gate_features.anchor('insert_pocket', (0, 0, 1), (12, -30, 40))
        f = gate_judges.finding('inserts.wall', 'demo/bracket', 'BLOCKED', 'wall 1.8 under 4.0', anchor=a, measure=1.8, limit=4.0, by=['Bearing 6005'])
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'waivers.json'), {'waivers': [
            {'id': 'W-1', 'rule': 'inserts.wall', 'part': 'demo/bracket', 'anchor': a, 'context': f['context'],
             'reason': 'the bore of the bought bearing is 1.8 mm from the pocket and cannot move', 'quote': 'leave these two as they are', 'date': '2026-03-04'}]})
        s = gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, self.judged(inserts_wall=[f]), path, TARGET)
        self.assertEqual((s['checks']['inserts.wall']['result'], s['verdict']), ('waived', 'pass'))
        q = gate_judges.finding('access.hole_covered', 'demo/bracket', 'FAIL', 'hole covered by Cover', anchor=a, by=['Cover'], ask='assembly.order')
        j = self.judged(access_hole_covered=[q])
        with mock.patch.object(gate_attest, 'load_answers', return_value=[]), mock.patch.object(gate_attest, 'closes', return_value=False):
            with self.assertRaisesRegex(GateError, 'open finding'): gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, j, path, TARGET)
        with mock.patch.object(gate_attest, 'load_answers', return_value=[{'rule': 'assembly.order'}]), mock.patch.object(gate_attest, 'closes', return_value=True):
            s = gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, j, path, TARGET)
        self.assertEqual((s['checks']['access.hole_covered']['result'], s['verdict'], s['attested']), ('attested', 'pass_attested', 1))
        s = gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, self.judged(), path, TARGET, attested=3)
        self.assertEqual((s['verdict'], s['attested']), ('pass_attested', 3))


class Store(Gated):
    def test_write_load_head_and_find(self):
        s = self.stamp()
        p = gate_common.stamp_path(self.d, 'demo/bracket', SIG1)
        self.assertEqual(gate_common.rel(self.d, p), 'gate/stamps/demo/bracket/a1a1a1a1a1a1.json'); self.assertEqual(gate_common.read_json(p), s)
        self.assertEqual(gate_stamp.load(self.d, 'demo/bracket', SIG1), s); self.assertIsNone(gate_stamp.load(self.d, 'demo/bracket', SIG2))
        self.assertIsNone(gate_stamp.load(self.d, 'demo/cover', SIG1))
        self.assertEqual(gate_stamp.head(self.d, 'demo/bracket'), s)
        self.assertEqual(gate_stamp.find_by_geom(self.d, s['geom_hash']), [s]); self.assertEqual(gate_stamp.find_by_geom(self.d, 'f' * 40), [])
        self.assertEqual(gate_stamp.find_by_geom(self.d, None), [])
        self.census('demo/bracket', SIG2)                                   # the model moved on: that geometry has no stamp
        self.assertIsNone(gate_stamp.head(self.d, 'demo/bracket'))

    def test_a_stamp_is_never_overwritten(self):
        s = self.stamp(); p = gate_common.stamp_path(self.d, 'demo/bracket', SIG1); before = read(p)
        self.assertEqual(gate_stamp.write(self.d, dict(s, time='2031-01-01T00:00:00Z', run='another')), p)       # the same statement: left alone
        self.assertEqual(read(p), before)
        other = dict(s, target=FINE)
        with self.assertRaisesRegex(GateError, 'never overwritten'): gate_stamp.write(self.d, other)
        self.assertEqual(read(p), before)
        p2 = gate_stamp.write(self.d, other, supersede=True)
        self.assertEqual(os.path.basename(p2), 'a1a1a1a1a1a1.2.json'); self.assertEqual(read(p), before)
        self.assertEqual(gate_stamp.write(self.d, other, supersede=True), p2)                                     # again: nothing new
        p3 = gate_stamp.write(self.d, dict(s, target=None), supersede=True); self.assertEqual(os.path.basename(p3), 'a1a1a1a1a1a1.3.json')
        self.assertEqual(len(gate_stamp.all_stamps(self.d, 'demo/bracket')), 3)
        self.assertEqual(gate_stamp.load(self.d, 'demo/bracket', SIG1)['target'], None)                           # same second: the later file wins

    def test_what_is_not_a_pass_is_not_written(self):
        s = self.stamp(write=False)
        for bad in (dict(s, verdict='fail'), {k: v for k, v in s.items() if k != 'geom_hash'}, dict(s, checks={'inserts.wall': {'result': 'open'}})):
            with self.assertRaises(GateError): gate_stamp.write(self.d, bad)
        self.assertEqual(gate_stamp.all_stamps(self.d), [])

    def test_all_stamps_newest_first_and_a_broken_file_is_no_stamp(self):
        a = self.stamp(time='2026-03-04T10:00:00Z')
        b = self.stamp('demo/cover', SIG2, 'cover', time='2026-03-05T10:00:00Z', d=6.0)
        with open(os.path.join(os.path.dirname(gate_common.stamp_path(self.d, 'demo/cover', SIG2)), 'ffffffffffff.json'), 'w') as f: f.write('{half a file')
        with open(gate_common.gate_path(self.d, 'stamps', 'notes.json'), 'w') as f: f.write('["not a stamp"]')
        self.assertEqual(gate_stamp.all_stamps(self.d), [b, a]); self.assertEqual(gate_stamp.all_stamps(self.d, 'demo/bracket'), [a])
        self.assertEqual(gate_stamp.all_stamps(self.d, 'demo/none'), [])
        shutil.rmtree(gate_common.gate_path(self.d, 'stamps')); self.assertEqual(gate_stamp.all_stamps(self.d), [])


class Dies(Gated):
    """One test per way a stamp dies, and what does not kill it."""
    def test_alive(self):
        s = self.stamp()
        self.assertEqual(self.verify(s)[0], 'valid'); self.assertIn('demo/bracket@a1a1a1a1a1a1', self.verify(s)[1])
        self.assertEqual(self.verify(s, geom_hash=s['geom_hash'], target=TARGET, census=gate_common.load_census(self.d))[0], 'valid')
        self.assertEqual(self.verify(None), ('none', 'no stamp'))

    def test_dies_by_signature_when_the_part_changed_in_the_model(self):
        s = self.stamp(); self.census('demo/bracket', SIG2, prev_sig=SIG1)
        state, why = self.verify(s, geom_hash=s['geom_hash'])
        self.assertEqual(state, 'stale'); self.assertIn('signature b2b2b2b2b2b2 now, a1a1a1a1a1a1 stamped', why)

    def test_dies_by_geometry_hash_when_the_mesh_is_not_the_stamped_one(self):
        s = self.stamp(); moved = meshid.stl_ids(self.stl('moved', hx=10.05))['geom_hash']
        state, why = self.verify(s, geom_hash=moved)
        self.assertEqual(state, 'stale'); self.assertIn('not the geometry that was stamped', why)

    def test_dies_by_a_changed_judge_and_names_only_that_rule(self):
        s = self.stamp()
        with mock.patch.dict(gate_judges.JUDGES, {'inserts_wall': changed_judge}):
            state, why = self.verify(s)
        self.assertEqual(state, 'stale'); self.assertIn('the judge of inserts.wall changed', why)
        self.assertEqual(self.verify(s)[0], 'valid')                        # the old judge back: nothing else was touched
        with mock.patch.dict(gate_judges.JUDGES, {'project_mesh_stale': changed_judge}):       # a judge no stamp of a part rests on
            self.assertEqual(self.verify(s)[0], 'valid')
        with mock.patch.dict(gate_judges.JUDGES):
            del gate_judges.JUDGES['mesh_closed']
            self.assertEqual(self.verify(s), ('stale', 'the judge of mesh.closed (mesh_closed) does not exist'))

    def test_dies_by_a_block_rule_that_is_not_in_it(self):
        older = copy.deepcopy(self.reg); del older['rules']['bearings.holding']            # the registry before that rule existed
        s = self.stamp(reg=older)
        self.assertNotIn('bearings.holding', s['checks']); self.assertEqual(gate_stamp.verify(self.d, older, s)[0], 'valid')
        state, why = self.verify(s)
        self.assertEqual(state, 'stale'); self.assertIn('block rule bearings.holding is not in the stamp', why)

    def test_dies_by_the_wrong_target(self):
        s = self.stamp()
        state, why = self.verify(s, target=FINE)
        self.assertEqual(state, 'stale'); self.assertIn('stamped for another target (0.6 mm nozzle', why); self.assertIn('0.4 mm nozzle', why)
        self.assertEqual(self.verify(s, target=dict(TARGET, material='PLA'))[0], 'stale')
        self.assertEqual(self.verify(s, target={'material': 'PETG', 'line_width': 0.62, 'nozzle': 0.60})[0], 'valid')
        self.assertEqual(self.verify(dict(s, target=None), target=TARGET)[0], 'stale')
        self.assertEqual(self.verify(s, target=None)[0], 'valid')            # nobody asked for a target

    def test_dies_by_not_being_the_newest_signature_of_the_part(self):
        old = self.stamp(sig=SIG1, time='2026-03-04T10:00:00Z', census=False)
        self.assertEqual(self.verify(old)[0], 'valid')
        new = self.stamp(sig=SIG2, time='2026-03-06T10:00:00Z', census=False, hx=11.0)     # the part was exported again later
        state, why = self.verify(old, geom_hash=old['geom_hash'])
        self.assertEqual(state, 'stale'); self.assertIn('not the newest signature of the part: b2b2b2b2b2b2 was stamped later', why)
        self.assertEqual(self.verify(new, geom_hash=new['geom_hash'])[0], 'valid')
        self.assertEqual(gate_stamp.head(self.d, 'demo/bracket'), new)
        self.census('demo/bracket', SIG1)                                    # even when the census still shows the old one
        self.assertEqual(self.verify(old)[0], 'stale')

    def test_dies_when_the_part_is_gone_from_its_document(self):
        s = self.stamp(); self.census('demo/bracket', SIG1, gone=True)
        self.assertEqual(self.verify(s), ('stale', 'demo/bracket is gone from its document (census)'))

    def test_a_file_that_is_not_a_complete_stamp_is_stale(self):
        s = self.stamp()
        for bad in (dict(s, verdict='fail'), dict(s, checks=None), dict(s, sig='')):
            self.assertEqual(self.verify(bad)[0], 'stale')
        self.assertEqual(self.verify(dict(s, checks=dict(s['checks'], **{'mesh.closed': {'result': 'open'}})))[0], 'stale')

    def test_a_plugin_version_bump_alone_does_not_kill_it(self):
        s = self.stamp()
        gate_common.write_json(gate_common.gate_path(self.d, 'LOCK.json'), {'plugin': 'fusion-bambulab', 'version': '9.9.9', 'time': '2031-01-01T00:00:00Z',
                                                                            'files': {'tools/gate.py': 'f' * 64, 'rules/registry/inserts.json': 'e' * 64}})
        newer = copy.deepcopy(self.reg); newer['sha256'] = 'd' * 64
        newer['rules']['inserts.wall']['statement'] += ' Reworded in the new version.'; newer['rules']['inserts.wall']['fix_order'].append('One more hint.')
        newer['rules']['demo.advice'] = {'id': 'demo.advice', 'level': 'advice', 'stage': 'design', 'check': None, 'params': [], 'category': 'process'}
        newer['rules']['demo.question'] = {'id': 'demo.question', 'level': 'attest', 'stage': 'export', 'check': None, 'params': [], 'category': 'process'}
        newer['rules']['project.demo'] = {'id': 'project.demo', 'level': 'block', 'stage': 'project', 'check': {'probe': 'project_consistency', 'judge': 'project_demo', 'needs': '3mf'},
                                          'params': [], 'category': 'project'}
        self.assertEqual(gate_stamp.verify(self.d, newer, s, geom_hash=s['geom_hash'], target=TARGET)[0], 'valid')
        self.assertNotIn('version', s); self.assertNotIn('plugin', s)


class Fakes:
    """What the other packages do in an export, reduced to their contracts."""
    def __init__(self, case):
        self.case, self.calls, self.design, self.export_findings, self.export_verdict = case, [], 'pass', {}, None
        self.env = {'error': None, 'done': True, 'written': True, 'sig': None, 'why_not': None}

    def part_verdict(self, root, reg, part, sig=None, stages=('design',)):
        self.calls.append(('verdict', part, tuple(stages)))
        v = self.design if tuple(stages) == ('design',) else (self.export_verdict or 'pass')
        base = {'part': part, 'sig': sig, 'verdict': v, 'open': [], 'asked': [], 'answered': [], 'waived': [], 'info': [], 'not_run': [], 'waivers': [], 'questions': [], 'attested': 0}
        if v == 'fail': base['open'] = [gate_judges.finding('walls.min_material', part, 'FAIL', 'wall 0.9 under 1.5', measure=0.9, limit=1.5)]
        if v == 'attest': base['questions'] = [{'q': 'Q-51b0c2', 'rule': 'print.bed_face', 'question': 'Which face is on the bed?'}]
        if v == 'incomplete': base['not_run'] = ['inserts.wall']
        if v == 'pass_attested': base['attested'] = 2
        return base

    def judge_part(self, root, reg, resolved, part, sig=None, stage='design', intent=None, target=None):
        self.calls.append(('judge', part, stage, target))
        for rid, j in self.case.judged(source='generated' if part == 'demo/shim' else 'fusion',
                                       resolved=gate_params.resolve(reg, None, target) if target else None).items():
            if reg['rules'][rid]['stage'] == stage:
                gate_common.save_judged(root, part, sig, rid, j['judge_hash'], j['params'], self.export_findings.get(rid, []))
        return {}

    def run_unit(self, job, url, transport=None, pinger=None):
        self.calls.append(('unit', job['probe'], job['part'], job['args']))
        if job['args'].get('sig_only'):                                  # the question "is the part still what was stamped"
            return {'job': job['id'], 'doc': job['doc'], 'doc_version': 42, 'part': job['part'], 'sig': self.env.get('sig_now') or self.env['sig'] or job['sig'],
                    'probe': 'fusion_thin', 'facts': {'sig_only': True}, 'done': True, 'cursor': None, 'elapsed_s': 0.1,
                    'error': self.env.get('sig_error'), 'open_documents': ['Demo Assembly']}
        if self.env['written']: write_stl(job['args']['stl'], *plate(hx=12.0))
        return {'job': job['id'], 'doc': job['doc'], 'doc_version': 42, 'part': job['part'], 'sig': self.env['sig'] or job['sig'], 'probe': 'fusion_export',
                'facts': {'stl': job['args']['stl'] if self.env['written'] else None, 'written': self.env['written'], 'why_not': self.env['why_not']},
                'done': self.env['done'], 'cursor': None, 'elapsed_s': 1.0, 'error': self.env['error'], 'open_documents': ['Demo Assembly']}

    def stl_mesh(self, path):
        self.calls.append(('stl_mesh', os.path.basename(path)))
        return {'stl': path, 'sha256': gate_common.sha256_file(path), 'triangles': 32, 'degenerate': 0, 'open_edges': 0, 'non_manifold_edges': 0, 'shells': 1,
                'volume': 2920.0, 'bbox': [[0, 0, 0], [30, 20, 5]], 'geom_hash': meshid.stl_ids(path)['geom_hash']}

    def stl_walls(self, path, report_under, budget_s=60.0, seed=1):
        self.calls.append(('stl_walls', os.path.basename(path), report_under))
        return {'stl': path, 'triangles': 32, 'coverage': 1.0, 'report_under': report_under, 'regions': []}


class Export(Gated):
    def setUp(self):
        super().setUp(); self.f = Fakes(self)
        for target, name, fn in ((gate_plan, 'part_verdict', self.f.part_verdict), (gate_plan, 'judge_part', self.f.judge_part), (gate_plan, 'acquire_lock', lambda root: None),
                                 (gate_plan, 'release_lock', lambda root: None), (gate_report, 'inherited_block', lambda root, part: []),
                                 (gate_fusion_rpc, 'run_unit', self.f.run_unit), (stl_mesh, 'measure', self.f.stl_mesh), (stl_walls, 'measure', self.f.stl_walls),
                                 # the scan before an export and the test of what the part was checked against have tests of their own (test_deps)
                                 (gate_stamp, 'scan_for_export', lambda *a, **k: None), (gate_stamp, '_checked_against', lambda *a, **k: None)):
            p = mock.patch.object(target, name, fn); p.start(); self.addCleanup(p.stop)
        self.census('demo/bracket', SIG1, stl='stl/bracket.stl', name='Bracket')
        for rid, j in self.judged().items():                                # the design run stored its judgements
            if self.reg['rules'][rid]['stage'] == 'design': gate_common.save_judged(self.d, 'demo/bracket', SIG1, rid, j['judge_hash'], j['params'], [])

    def export(self, part='demo/bracket', **kw):
        resolved = gate_params.resolve(self.reg, None, FINE) if kw.get('target_key') == 'fine' else self.resolved    # as cmd_export resolves: for the target
        return gate_stamp.export_part(self.d, self.reg, resolved, part, **kw)

    def quarantine(self): return gate_common.gate_path(self.d, 'run', 'quarantine', 'demo__bracket.stl')

    def test_a_part_that_passes_goes_from_quarantine_into_the_stl_folder_with_its_stamp(self):
        r = self.export(); dest = os.path.join(self.d, 'stl', 'bracket.stl')
        self.assertEqual((r['exit'], r['stl'], r['why']), (0, dest, None))
        self.assertTrue(os.path.isfile(dest)); self.assertFalse(os.path.exists(self.quarantine()))
        s = gate_stamp.load(self.d, 'demo/bracket', SIG1)
        self.assertEqual(r['stamp'], gate_common.stamp_path(self.d, 'demo/bracket', SIG1))
        self.assertEqual((s['stl'], s['stl_sha256'], s['geom_hash'], s['doc_version'], s['target']),
                         ('stl/bracket.stl', gate_common.sha256_file(dest), meshid.stl_ids(dest)['geom_hash'], 42, TARGET))
        self.assertEqual(self.verify(s, geom_hash=s['geom_hash'], target=TARGET)[0], 'valid')
        kinds = [c[0] for c in self.f.calls]
        self.assertEqual(kinds, ['judge', 'verdict', 'unit', 'stl_mesh', 'stl_walls', 'judge', 'verdict'])   # the design stage is judged again for this target first
        self.assertEqual([c[2:] for c in self.f.calls if c[0] == 'judge'], [('design', TARGET), ('export', TARGET)])
        unit = [c for c in self.f.calls if c[0] == 'unit'][0]
        self.assertEqual((unit[1], unit[2], unit[3]), ('fusion_export', 'demo/bracket', {'stl': self.quarantine(), 'name': 'Bracket'}))
        self.assertEqual([c for c in self.f.calls if c[0] == 'judge'][-1][2:], ('export', TARGET))
        self.assertEqual([c for c in self.f.calls if c[0] == 'stl_walls'][0][2], 2 * max(1.5, 2 * 0.62, 0.3))
        probes = gate_common.load_facts(self.d, 'demo/bracket', SIG1)['probes']
        self.assertEqual(sorted(probes), ['fusion_export', 'stl_mesh', 'stl_walls'])
        self.f.calls.clear(); again = self.export()                          # a second export of a stamped part asks Fusion ONE thing:
        self.assertEqual(again['exit'], 0); self.assertIn('already stamped', again['why']); self.assertIn('Fusion confirms', again['why'])
        self.assertEqual([c[0] for c in self.f.calls], ['judge', 'verdict', 'unit'])  # is the part still the geometry of the stamp
        self.assertEqual(self.f.calls[2][1:], ('fusion_thin', 'demo/bracket', {'sig_only': True}))

    def test_a_part_changed_since_the_last_scan_is_never_already_stamped(self):
        self.assertEqual(self.export()['exit'], 0)
        dest = os.path.join(self.d, 'stl', 'bracket.stl'); before = gate_common.sha256_file(dest)
        self.f.env['sig_now'] = SIG2                                         # edited in Fusion; nobody ran a scan, the census still says SIG1
        self.f.calls.clear(); r = self.export()
        self.assertEqual(r['exit'], 1); self.assertIn('changed since its stamp', r['why']); self.assertIn('OLD', r['why']); self.assertIsNone(r['stamp'])
        self.assertEqual([c[0] for c in self.f.calls], ['judge', 'verdict', 'unit']); self.assertEqual(gate_common.sha256_file(dest), before)
        self.f.env['sig_now'] = None; self.f.env['sig_error'] = "KeyError: document 'Demo Assembly' is not open (open: Other)"
        r = self.export()                                                    # the document is closed: nothing can be confirmed
        self.assertEqual(r['exit'], 4); self.assertIn('could not be confirmed', r['why']); self.assertIn('is not open', r['why'])

    def test_a_tightened_limit_reopens_the_stamp_a_loosened_one_does_not(self):
        s = self.stamp()
        self.assertEqual(self.verify(s, geom_hash=s['geom_hash'], target=TARGET)[0], 'valid')
        def with_wall(v):
            gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'params.json'),
                                   {'inserts.wall_target': {'value': v, 'tag': 'U', 'source': 'owner', 'date': '2031-02-03'}})
            return self.verify(s, geom_hash=s['geom_hash'], target=TARGET)
        state, why = with_wall(6.0)                                          # judged at 4.0, the owner now asks for 6.0
        self.assertEqual(state, 'stale'); self.assertIn('inserts.wall', why); self.assertIn('inserts.wall_target = 4.0', why); self.assertIn('6.0', why)
        self.assertEqual(with_wall(3.0)[0], 'valid')                         # what passed 4.0 passes 3.0
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'params.json'),
                               {'bearings.ref_pattern': {'value': '(?i)bearing|ring', 'tag': 'U', 'source': 'owner', 'date': '2031-02-03'}})
        state, why = self.verify(s, geom_hash=s['geom_hash'], target=TARGET)  # a pattern has no order: any change re-opens the check
        self.assertEqual(state, 'stale'); self.assertIn('bearings.ref_pattern', why)
        self.assertEqual(gate_stamp.verify(self.d, self.reg, s, geom_hash=s['geom_hash'], target=TARGET, resolved=self.resolved)[0], 'valid')

    def test_a_check_without_a_stored_parameter_is_open(self):
        s = self.stamp(write=False)
        del s['checks']['inserts.wall']['params']['inserts.wall_target']
        state, why = self.verify(s)
        self.assertEqual(state, 'stale'); self.assertIn('inserts.wall_target = nothing', why)

    def test_a_waived_check_dies_with_its_waiver(self):
        f = gate_judges.finding('inserts.wall', 'demo/bracket', 'GROW', 'wall 2.5 under 4.0', measure=2.5, limit=4.0,
                                anchor=gate_features.anchor('insert_pocket', (0, 0, 1), (10, 0, 5)), context={'wall': 2.5})
        waiver = {'id': 'W-1', 'rule': 'inserts.wall', 'part': 'demo/bracket', 'anchor': f['anchor'], 'context': f['context'],
                  'reason': 'the boss beside it cannot move, the wall is held by the rib behind', 'quote': 'leave that one, the rib holds it', 'date': '2031-02-03'}
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'waivers.json'), {'waivers': [waiver]})
        path = self.stl('bracket'); self.census('demo/bracket', SIG1, stl='stl/bracket.stl')
        judged = self.judged(inserts_wall=[f])
        for rid, j in judged.items(): gate_common.save_judged(self.d, 'demo/bracket', SIG1, rid, j['judge_hash'], j['params'], j['findings'])
        s = gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, judged, path, TARGET)
        self.assertEqual(s['checks']['inserts.wall']['result'], 'waived'); self.assertEqual(self.verify(s)[0], 'valid')
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'waivers.json'), {'waivers': []})
        state, why = self.verify(s)
        self.assertEqual(state, 'stale'); self.assertIn('waived result of inserts.wall no longer holds', why)

    def test_a_tolerate_decision_leads_to_a_stamp_and_fix_does_not(self):
        f = gate_judges.finding('inserts.wall', 'demo/bracket', 'GROW', 'wall 2.5 under 4.0', measure=2.5, limit=4.0,
                                anchor=gate_features.anchor('insert_pocket', (0, 0, 1), (10, 0, 5)), context={'wall': 2.5})
        item = {'id': 'T-0001', 'rule': 'inserts.wall', 'part': 'demo/bracket', 'anchor': f['anchor'], 'context': f['context'], 'decision': 'tolerate'}
        path = self.stl('bracket'); self.census('demo/bracket', SIG1, stl='stl/bracket.stl')
        judged = self.judged(inserts_wall=[f])
        for decision, result in (('tolerate', 'tolerated'), ('rule_wrong', 'tolerated'), ('waive', 'tolerated')):
            gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'triage.json'), {'items': [dict(item, decision=decision)]})
            s = gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, judged, path, TARGET)
            self.assertEqual((s['checks']['inserts.wall']['result'], s['verdict'], s['tolerated'], s['attested']), (result, 'pass_attested', 1, 0))
        for decision in (None, 'fix'):
            gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'triage.json'), {'items': [dict(item, decision=decision)]})
            with self.assertRaises(GateError) as e: gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, judged, path, TARGET)
            self.assertIn('1 open finding(s) of inserts.wall', str(e.exception))
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'triage.json'), {'items': [dict(item, decision='tolerate', context='0' * 16)]})
        with self.assertRaises(GateError): gate_stamp.build(self.d, self.reg, self.resolved, 'demo/bracket', SIG1, judged, path, TARGET)   # the feature changed: a new finding

    def test_a_generator_entry_cannot_stamp_a_fusion_part(self):
        self.assertEqual(self.export()['exit'], 0)                           # demo/bracket: a Fusion part, exported to stl/bracket.stl
        layout = gate_common.load_layout(self.d)
        layout['generators'].append({'part': 'demo/bracket_stl', 'stl': 'stl/bracket.stl', 'source': 'tools/export_all.py'})
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        r = gate_stamp.export_generated(self.d, self.reg, self.resolved, 'stl/bracket.stl')
        self.assertEqual(r['exit'], 1); self.assertIn('is the export file of the Fusion part demo/bracket', r['why']); self.assertIsNone(r['stamp'])
        copy_ = os.path.join(self.d, 'stl', 'bracket_copy.stl'); shutil.copyfile(os.path.join(self.d, 'stl', 'bracket.stl'), copy_)
        layout['generators'].append({'part': 'demo/bracket_copy', 'stl': 'stl/bracket_copy.stl', 'source': 'tools/export_all.py'})
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        r = gate_stamp.export_generated(self.d, self.reg, self.resolved, 'stl/bracket_copy.stl')
        self.assertEqual(r['exit'], 1); self.assertIn('has the geometry of the Fusion part demo/bracket', r['why'])
        named = write_stl(os.path.join(self.d, 'stl', 'lid_export.stl'), *plate(hx=9.0), header=b'Fusion Demo Assembly v7 lid')
        layout['generators'].append({'part': 'demo/lid_export', 'stl': 'stl/lid_export.stl', 'source': 'tools/export_all.py'})
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        r = gate_stamp.export_generated(self.d, self.reg, self.resolved, 'stl/lid_export.stl')
        self.assertEqual(r['exit'], 1); self.assertIn('its header says it was exported from Fusion', r['why'])
        self.assertEqual(gate_stamp.all_stamps(self.d, 'demo/lid_export') + gate_stamp.all_stamps(self.d, 'demo/bracket_copy'), [])

    def test_a_part_that_fails_at_export_stays_in_quarantine_and_the_folder_keeps_the_old_file(self):
        old = self.stl('bracket'); before = gate_common.sha256_file(old)
        self.f.export_verdict = 'fail'
        r = self.export()
        self.assertEqual((r['exit'], r['stamp'], r['stl']), (1, None, None)); self.assertEqual(len(r['open']), 1)
        self.assertTrue(os.path.isfile(self.quarantine())); self.assertEqual(r['quarantine'], self.quarantine())
        self.assertEqual(gate_common.sha256_file(old), before); self.assertEqual(gate_stamp.all_stamps(self.d), [])

    def test_no_fusion_call_while_the_design_checks_are_not_a_pass(self):
        for verdict, code in (('fail', 1), ('incomplete', 1), ('attest', 2), ('unreachable', 4)):
            self.f.design = verdict; self.f.calls.clear()
            r = self.export()
            with self.subTest(verdict=verdict):
                self.assertEqual(r['exit'], code); self.assertTrue(r['why']); self.assertEqual([c[0] for c in self.f.calls], ['judge', 'verdict'])
        self.assertEqual(gate_stamp.all_stamps(self.d), [])

    def test_inherited_findings_without_a_decision_block_the_export(self):
        with mock.patch.object(gate_report, 'inherited_block', lambda root, part: [{'id': 'T-0001', 'decision': None}]):
            r = self.export()
        self.assertEqual(r['exit'], 1); self.assertIn('T-0001', r['why']); self.assertNotIn('unit', [c[0] for c in self.f.calls])

    def test_questions_of_the_export_stage_stop_the_stamp(self):
        self.f.export_verdict = 'attest'
        r = self.export()
        self.assertEqual((r['exit'], r['stamp']), (2, None)); self.assertEqual(r['questions'][0]['q'], 'Q-51b0c2')
        self.assertFalse(os.path.exists(os.path.join(self.d, 'stl', 'bracket.stl')))

    def test_answers_make_the_stamp_pass_attested(self):
        self.f.design = self.f.export_verdict = 'pass_attested'
        r = self.export(); s = gate_stamp.load(self.d, 'demo/bracket', SIG1)
        self.assertEqual((r['exit'], s['verdict'], s['attested']), (0, 'pass_attested', 2))

    def test_what_fusion_can_do_wrong(self):
        for change, code, text in (({'written': False, 'why_not': 'the mesh has 2 shells'}, 1, '2 shells'), ({'sig': SIG2}, 1, 'changed while it was exported'),
                                   ({'error': 'document "Demo Assembly" is not open'}, 4, 'is not open'), ({'done': False}, 3, 'did not finish')):
            self.f.env = dict({'error': None, 'done': True, 'written': True, 'sig': None, 'why_not': None}, **change)
            r = self.export()
            with self.subTest(change=change):
                self.assertEqual(r['exit'], code); self.assertIn(text, r['why']); self.assertIsNone(r['stamp'])
        with mock.patch.object(gate_fusion_rpc, 'run_unit', side_effect=gate_fusion_rpc.FusionDown('no answer')):
            self.assertEqual(self.export()['exit'], 4)
        self.assertEqual(gate_stamp.all_stamps(self.d), []); self.assertFalse(os.path.exists(os.path.join(self.d, 'stl', 'bracket.stl')))

    def test_parts_the_export_does_not_know(self):
        self.assertEqual(self.export('demo/nothing')['exit'], 4)
        self.census('demo/shim', 'd' * 40, source='generated', stl='stl/shim.stl')
        r = self.export('demo/shim'); self.assertEqual(r['exit'], 4); self.assertIn('--generated', r['why'])
        self.set_project('observe', targets={'main': TARGET, 'fine': FINE})
        r = self.export(); self.assertEqual(r['exit'], 4); self.assertIn('--for KEY', r['why'])
        self.assertEqual(self.export(target_key='fine')['exit'], 0)
        self.assertEqual(gate_stamp.load(self.d, 'demo/bracket', SIG1)['target'], FINE)
        self.assertEqual(self.export(wall_s=3.0, target_key='main')['exit'], 3)                 # no time left for a Fusion call

    def test_one_geometry_can_hold_a_stamp_for_two_targets(self):
        self.set_project('observe', targets={'main': TARGET, 'fine': FINE})
        self.assertEqual(self.export(target_key='main')['exit'], 0); r = self.export(target_key='fine')
        self.assertEqual(r['exit'], 0); self.assertEqual(os.path.basename(r['stamp']), 'a1a1a1a1a1a1.2.json')
        stamps = gate_stamp.all_stamps(self.d, 'demo/bracket')
        self.assertEqual(sorted(s['target']['nozzle'] for s in stamps), [0.4, 0.6])
        self.assertEqual(sorted(self.verify(s, target=FINE)[0] for s in stamps), ['stale', 'valid'])

    def test_an_stl_made_by_code_is_stamped_where_it_lies(self):
        path = self.stl('shim'); sig = meshid.stl_ids(path)['geom_hash']
        r = gate_stamp.export_generated(self.d, self.reg, self.resolved, 'stl/shim.stl')
        self.assertEqual((r['part'], r['exit'], r['stl']), ('demo/shim', 0, path))
        s = gate_stamp.load(self.d, 'demo/shim', sig)
        self.assertEqual((s['sig'], s['geom_hash'], s['stl']), (sig, sig, 'stl/shim.stl')); self.assertNotIn('inserts.wall', s['checks'])
        self.assertEqual(self.verify(s, geom_hash=sig, target=TARGET)[0], 'valid')
        self.assertEqual([c[0] for c in self.f.calls], ['stl_mesh', 'stl_walls', 'judge', 'verdict']); self.assertEqual(self.f.calls[-1][2], ('export',))
        self.stl('shim', d=5.0)                                              # the generator ran again: the old stamp is not this file's
        self.assertEqual(gate_stamp.find_by_geom(self.d, meshid.stl_ids(path)['geom_hash']), [])
        r = gate_stamp.export_generated(self.d, self.reg, self.resolved, os.path.join(self.d, 'stl', 'other.stl'))
        self.assertEqual(r['exit'], 4); self.assertIn('not the STL of a generator', r['why'])

    def test_the_command_sums_the_parts(self):
        self.census('demo/cover', SIG2, stl='stl/cover.stl', name='Cover')    # nothing judged for it: build() has no judgement
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate_stamp.cmd_export(self.d, types.SimpleNamespace(parts=['demo/bracket', 'demo/nothing'], generated=None, target=None))
        self.assertEqual(code, 4); self.assertIn('demo/bracket', out.getvalue()); self.assertIn('stamped -> stl/bracket.stl', out.getvalue())
        self.assertIn('NOT exported (exit 4)', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate_stamp.cmd_export(self.d, types.SimpleNamespace(parts=['demo/bracket'], generated=None, target='main')), 0)
            self.f.design = 'attest'
            self.assertEqual(gate_stamp.cmd_export(self.d, types.SimpleNamespace(parts=['demo/cover'], generated=None, target=None)), 2)
            self.f.design = 'fail'
            self.assertEqual(gate_stamp.cmd_export(self.d, types.SimpleNamespace(parts=['demo/cover', 'demo/bracket'], generated=None, target=None)), 1)
        with self.assertRaises(GateError): gate_stamp.cmd_export(self.d, types.SimpleNamespace(parts=[], generated=None, target=None))
        with self.assertRaises(GateError): gate_stamp.cmd_export(self.d, types.SimpleNamespace(parts=['demo/bracket'], generated=None, target='nope'))

    def test_promote_moves_the_file_and_makes_the_folder(self):
        src = write_stl(os.path.join(self.d, 'q', 'a.stl'), *plate()); dest = os.path.join(self.d, 'new', 'folder', 'a.stl')
        self.assertEqual(gate_stamp.promote(src, dest), dest); self.assertTrue(os.path.isfile(dest)); self.assertFalse(os.path.exists(src))


if __name__ == '__main__':
    unittest.main()
