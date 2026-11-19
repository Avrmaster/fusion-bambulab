"""One project from setup to the session-start digest, on the SHIPPED registry and the shipped judges.

A scratch project is made by tools/setup_project.py --gate. Fusion is tests/fake_fusion.FakeFusion: it answers each unit
with canned facts and writes the STL of an export. Everything else is the real code: the plan, the judges, the attest
questions, the owner commands, the STL probes, the stamps, the guard inside bambu3mf.save(), gate.py verify, the print
ledger and the hook. The project's own copy of the tools (gate/tools, bambulab/tools) runs the steps that need no
Fusion: the build script, verify, the ledger commands. No Fusion, no Bambu Studio. Every name and number is invented."""
import contextlib, io, json, os, re, shutil, struct, subprocess, sys, tempfile, unittest, zipfile
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
    if d not in sys.path: sys.path.insert(0, d)
import gate, gate_common, gate_features, gate_fusion_rpc, gate_hook, gate_plan, gate_registry, gate_report, gate_stamp, gate_vendor
from gate_guard import GuardRefusal
import bambu3mf, fake_fusion, meshid
import test_guard as G
import test_probes_host as H

SETUP = os.path.join(ROOT, 'tools', 'setup_project.py')
HOOK = os.path.join(ROOT, 'tools', 'gate', 'gate_hook.py')
DOC, DOC_KEY, PROJ, PROJ_KEY = 'Demo Assembly', 'demo_assembly', 'bambulab/DeskLamp.3mf', 'desklamp'
FOOT, HUB, CLIP = 'demo_assembly/lamp_foot', 'demo_assembly/turntable', 'demo_assembly/cable_clip'
NAMES = {FOOT: 'Lamp foot', HUB: 'Turntable', CLIP: 'Cable clip'}
PS, MS = 'Metadata/project_settings.config', 'Metadata/model_settings.config'
# what the slicer project prints with: the gate reads it into the declared target, the fits count as measured under it
SLICER = {'nozzle_diameter': ['0.4'], 'line_width': '0.42', 'filament_type': ['PETG'], 'layer_height': '0.2', 'initial_layer_print_height': '0.2', 'xy_hole_compensation': '0.1',
          'xy_contour_compensation': '0', 'elefant_foot_compensation': '0.15', 'wall_generator': 'classic',
          'enable_support': '0', 'support_type': 'normal(auto)', 'support_on_build_plate_only': '0', 'support_remove_small_overhang': '1'}
TARGET = {'nozzle': 0.4, 'line_width': 0.42, 'material': 'PETG', 'layer_height': 0.2, 'initial_layer_print_height': 0.2, 'xy_hole_compensation': 0.1, 'xy_contour_compensation': 0.0,
          'elefant_foot_compensation': 0.15, 'wall_generator': 'classic'}
PLATES = [('first plate', [('Lamp foot', 'lamp_foot.stl', False)]), ('second plate', [('Turntable', 'turntable.stl', False)])]
WITH_CLIP = PLATES + [('third plate', [('Cable clip', 'cable_clip.stl', False)])]
FREE_RING = 'a floating bearing: the bought axle carries a snap ring above the inner ring, and that ring closes this face'
OWNER_SAID = 'the snap ring holds it, leave the turntable as it is'

# ---- what the fake Fusion answers ------------------------------------------------------------------------------------------

POCKET = gate_features.anchor('insert_pocket', (0, 0, 1), (8, 8, 10))
STACK = [{'d': 7.5, 'len': 0.54}, {'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 5.0}]            # relief, lead-in, hole: the pocket of the table
CLEAN = {'fusion_census': {'holes': [], 'openings': [], 'counts': {}}, 'fusion_inserts': {'pockets': []}, 'fusion_openings': {'openings': [], 'closed': []},
         'fusion_screws': {'screws': [], 'washers': []}, 'fusion_bearings': {'bearings': [], 'seats_without_bearing': []},
         'fusion_gap': {'own_group': None, 'margin': 5.0, 'gaps': []}, 'fusion_thin': {'faces': [], 'width_cap': 2.0, 'lumps': 1, 'shells': 1},
         'fusion_near': {'margin': 10.0, 'bodies': []}}


def foot_census():
    """The foot has one heat-set pocket, of the table shape."""
    return {'holes': [{'id': 'h1', 'anchor': POCKET, 'segments': STACK, 'depth': 7.04, 'through': False, 'floor': 2.96, 'exit': None,
                       'class': {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'exact'}, 'coaxial': []}],
            'openings': [], 'counts': {'holes': 1, 'openings': 0, 'insert_pocket': 1, 'unknown': 0}}


def foot_pocket(wall):
    """The plastic round that pocket: `wall` mm at the lead-in with nothing near it on any side. The relief is 1.55 mm
    wider in radius than the lead-in, so that much less stays beside it."""
    w = lambda mm: {'min': round(mm, 2), 'dir': [-1, 0, 0], 'free_arc_deg': 360, 'blocked_by': []}
    return {'pockets': [{'hole': 'h1', 'anchor': POCKET, 'kind': 'M3x4x5', 'segments': STACK, 'relief': {'d': 7.5, 'depth': 0.54},
                         'relief_room': [{'d': 7.5, 'wall': round(wall - 1.55, 2)}, {'d': 6.5, 'wall': round(wall - 1.05, 2)}, {'d': 5.9, 'wall': round(wall - 0.75, 2)}],
                         'walls': {'lead_in': w(wall), 'insert': w(wall + 0.4), 'relief': w(wall - 1.55)}, 'floor': 2.96, 'ray': {'n': 48, 'step': 0.1, 'max': 8.0}}]}


def hub_bearing():
    """The turntable carries one 6203. Its outer ring sits on a shoulder and under six washers; the inner ring rests on
    a bought axle and has nothing on its upper face."""
    on = lambda name, kind, part=None, share=1.0, points=1: {'name': name, 'kind': kind, 'part': part, 'share': share, 'points': points, 'gap': 0.0, 'touches_other_ring': False}
    return {'bearings': [{'ref': 'ref bearing 6203', 'designation': '6203', 'by': 'geometry', 'd': 17.0, 'D': 40.0, 'B': 12.0,
                          'anchor': gate_features.anchor('bearing', (0, 0, 1), (0, 0, 8)),
                          'rings': {'inner': {'faces': [{'side': '+', 'covered': []}, {'side': '-', 'covered': [on('ref axle', 'bought')]}]},
                                    'outer': {'faces': [{'side': '+', 'covered': [on('ref washer M3 large (x6)', 'hardware', share=0.25, points=6)]},
                                                        {'side': '-', 'covered': [on('Turntable', 'printed', HUB)]}]}},
                          'clamps': [], 'seat': {'inner': None, 'outer': {'d': 40.0, 'part': HUB, 'len': 12.0, 'entry_d': 40.2}}}],
            'seats_without_bearing': []}


def stl_bytes(tris, name):
    """A binary STL as the exporter writes it: the header names the document, its version and the part."""
    out = [('Fusion %s v12 %s' % (DOC, name)).encode().ljust(80, b'\0'), struct.pack('<I', len(tris))]
    out += [struct.pack('<12fH', 0.0, 0.0, 0.0, *(c for p in t for c in p), 0) for t in tris]
    return b''.join(out)


# the meshes the fake exporter writes, and the box the scan reports for each part
MESHES = {'foot v2': lambda: H.box(44, 34, 10), 'foot v3': lambda: H.box(44, 34, 12),
          'hub': lambda: [tuple((x + 120.0, y, z) for x, y, z in t) for t in H.tube(20.0, 28.0, 14.0)],      # modelled 120 mm away from the foot
          'clip': H.SHAPES['neck_086']}                                                                    # two blocks joined by a 0.86 mm neck
BOXES = {FOOT: [[0, 0, 0], [44, 34, 12]], HUB: [[92, -28, 0], [148, 28, 14]], CLIP: [[0, 0, 0], [30, 20, 5]]}


def template(path):
    """A synthetic slicer template with the settings a saved project has."""
    G.template(path)
    with zipfile.ZipFile(path) as z: files = {n: z.read(n) for n in z.namelist()}
    files[PS] = json.dumps(dict(json.loads(files[PS]), **SLICER)).encode()
    with zipfile.ZipFile(path, 'w') as z:
        for n, data in files.items(): z.writestr(n, data)
    return path


def clean_env():
    env = dict(os.environ)
    for k in ('CLAUDE_PROJECT_DIR', 'CLAUDE_PLUGIN_ROOT', 'PYTHONPATH'): env.pop(k, None)
    return env


class Lamp(unittest.TestCase):
    """The scratch project and its helpers: a desk lamp with three printed parts. tests/test_walks.py walks it too."""

    def setUp(self):
        tmp = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        self.root = os.path.join(tmp, 'desk_lamp'); self.proj = self.path(*PROJ.split('/')); self.stl_dir = os.path.join(self.root, 'stl')
        bambu3mf._NOTES.clear(); bambu3mf._SETTING_NOTES.clear()
        template(self.path('bambulab', 'template.3mf'))
        r = subprocess.run([sys.executable, SETUP, self.root, '--name', 'Desk Lamp', '--fusion-doc', DOC, '--gate'], capture_output=True, text=True, env=clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.setup_out = r.stdout
        # no step of this test may reach a Fusion that happens to run on this machine: the project's endpoint is a dead port,
        # and the calls gate.py makes in this process go to the fake
        layout = self.read('gate', 'layout.json'); layout['fusion']['endpoint'] = 'http://127.0.0.1:1/mcp'
        gate_common.write_json(self.path('gate', 'layout.json'), layout)
        self.fusion = fake_fusion.FakeFusion({DOC: 12})
        for p in (mock.patch.object(gate_fusion_rpc, 'http_transport', self.fusion.transport), mock.patch.object(gate_fusion_rpc, 'http_ping', self.fusion.ping),
                  mock.patch.object(gate_plan, '_clock', self.fusion.clock)):
            p.start(); self.addCleanup(p.stop)
        self.reg = gate_registry.load_default()
        per_part = [r for r in gate_registry.rules(self.reg, level='attest', deferred=False) if r['attest']['per'] == 'part' and gate_registry.applies_to(r, 'fusion')]
        self.n_design = sum(1 for r in per_part if r['stage'] == 'design'); self.n_asked = len(per_part)       # questions one part is asked: in a run, and up to its stamp
        self.sigs = {}

    # ---- helpers ----
    def by_facts(self, part=None):
        """How many standing questions the gate answered itself from the facts (of one part, or of every part of the latest report)."""
        return sum(1 for q in self.latest()['questions'] if (q.get('answer') or {}).get('kind') == 'facts' and part in (None, q['part']))

    def path(self, *parts):
        os.makedirs(os.path.join(self.root, *parts[:-1]), exist_ok=True)
        return os.path.join(self.root, *parts)

    def read(self, *parts): return gate_common.read_json(os.path.join(self.root, *parts), None)

    def text(self, *parts):
        with open(os.path.join(self.root, *parts), encoding='utf-8') as f: return f.read()

    def gate(self, *argv):
        """gate.py of the plugin, in this process, so that its Fusion calls reach the fake. Returns (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try: code = gate.main(['--root', self.root] + list(argv))
            except SystemExit as e: code = e.code
        return code, out.getvalue(), err.getvalue()

    def own(self, *argv):
        """A tool of the project's own vendored copy, run as the agent runs it: from the project root. Only for tools that
        need no Fusion."""
        return subprocess.run([sys.executable] + list(argv), capture_output=True, text=True, cwd=self.root, env=clean_env(), timeout=120)

    def model(self, **words):
        """What the document holds now: one word per part stands for its geometry. A new word is a new signature.
        Returns the signatures of the parts named."""
        ids = {'foot': FOOT, 'hub': HUB, 'clip': CLIP}
        self.sigs.update({ids[k]: fake_fusion.sig_of('%s %s' % (ids[k], w)) for k, w in words.items()})
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_facts('fusion_scan', None, {'parts': [fake_fusion.scan_part(NAMES[p], s, bbox=BOXES[p]) for p, s in sorted(self.sigs.items())], 'refs': []})
        return [self.sigs[ids[k]] for k in words]

    def facts(self, part, **by_probe):
        """Clean facts for every unit of one part, with the named probes replaced."""
        holes = (by_probe.get('fusion_census') or {}).get('holes') or []
        if holes and 'fusion_openings' not in by_probe:      # the opening unit writes a row for every hole of the census: here each one is free
            free = lambda r, reach: {'r': r, 'reach': reach, 'free': reach, 'hits': []}       # noqa: E731
            by_probe['fusion_openings'] = {'closed': [], 'openings': [
                {'feature': h['id'], 'of': 'insert_pocket' if h['anchor'].get('kind') == 'insert_pocket' else 'hole', 'anchor': h['anchor'], 'end': 'mouth',
                 'paths': {'own': free(h['segments'][0]['d'] / 2.0, 60.0), 'iron': free(4.0, 30.0)}} for h in holes]}
        for probe, clean in CLEAN.items():
            self.fusion.clear(probe, part)
            self.fusion.set_facts(probe, part, json.loads(json.dumps(by_probe.get(probe, clean))))

    def run_gate(self, *argv):
        code, out, err = self.gate('run', '--wall', '600', *argv)
        self.assertEqual(err, '', out)
        return code, out

    def latest(self): return gate_report.load_latest(self.root)

    def rows(self, part, state='open'):
        return sorted((f['rule'], f['verdict']) for f in self.latest()['findings'] if f['part'] == part and f['state'] == state)

    def finding(self, part, rule):
        return next(f for f in self.latest()['findings'] if f['part'] == part and f['rule'] == rule)

    def questions(self):
        return re.findall(r'^(Q-[0-9a-f]{6})  (\S+)  (\S+)', self.gate('attest', 'list')[1], re.M)

    def answer_all(self, **labels):
        """Answer every open question: the first fixed answer of its rule that the facts of the part do not contradict
        (labels: another one, by rule id with '_' for '.'), or a picture taken now where the rule wants one. Returns how
        many were answered."""
        n, shot = 0, self.path('.shots', 'changed_zone.png')
        with open(shot, 'wb') as f: f.write(b'\x89PNG\r\n\x1a\n' + b'\0' * 32)
        for qid, part, rule in self.questions():
            a = self.reg['rules'][rule]['attest']
            tries = [['--label', x] for x in ([labels[rule.replace('.', '_')]] if rule.replace('.', '_') in labels else a['labels'])] if 'label' in a['evidence'] else [['--shot', shot]]
            for how in tries:                        # "every screw: the key swings free" is refused on a part without screws: the next label is the true one
                code, out, err = self.gate('attest', 'answer', qid, *how)
                if code != 4 or 'the facts' not in err: break
            self.assertIn(code, (0, 2), '%s %s: %s%s' % (rule, part, out, err)); n += 1
        return n

    def stamp(self, part, sig=None): return gate_common.read_json(gate_common.stamp_path(self.root, part, sig or self.sigs[part]), None)

    def exports(self): return len(self.fusion.calls_of('fusion_export'))

    def ledger(self, *argv):
        r = self.own(os.path.join('bambulab', 'tools', 'ledger.py'), *argv)
        self.assertEqual(r.stderr, '', r.stdout)
        return r.returncode, r.stdout

    def events(self): return gate_common.read_jsonl(gate_common.events_path(self.root))

    def raw(self):
        with open(self.proj, 'rb') as f: return f.read()

    def objects(self): return sorted(m['object'] for m in meshid.read_project(self.proj))

    def guard_log(self):
        p = gate_common.gate_path(self.root, 'reports', 'guard.log')
        return self.text('gate', 'reports', 'guard.log') if os.path.exists(p) else ''


class WholeChain(Lamp):
    # ---- the chain ----
    def test_from_setup_to_the_session_start_digest(self):
        self.step_0_setup_makes_a_gated_project()
        self.step_1_scan_discovers_three_parts()
        self.step_2_run_finds_the_thin_wall_and_the_open_ring_face()
        self.step_3_a_waiver_needs_a_reason_that_is_not_an_export_order()
        self.step_4_the_fixed_wall_is_a_new_geometry_and_its_questions_are_asked()
        self.step_5_export_checks_the_stl_before_it_stamps()
        self.step_6_the_guard_lets_stamped_meshes_in_and_nothing_else()
        self.step_7_verify_reads_the_project_file()
        self.step_8_the_ledger_knows_what_was_printed()
        self.step_9_the_session_start_digest_names_what_is_open()

    def step_0_setup_makes_a_gated_project(self):
        self.assertIn('TARGET    desklamp: nozzle 0.4, line width 0.42, PETG', self.setup_out); self.assertIn('PROPOSED', self.setup_out)
        project = self.read('gate', 'project.json'); layout = self.read('gate', 'layout.json')
        self.assertEqual((project['mode'], project['targets']), ('observe', {PROJ_KEY: TARGET}))           # read from the template, fit settings and all
        self.assertEqual([(d['key'], d['name'], d['stl_dir']) for d in layout['documents']], [(DOC_KEY, DOC, 'stl')])
        self.assertEqual([(p['key'], p['file'], p['build_table']) for p in layout['projects']], [(PROJ_KEY, PROJ, 'bambulab/build_3mf.py')])
        self.assertEqual(gate_vendor.lock_state(self.root, ROOT)['state'], 'intact')                       # the project's copy is the code this test runs
        self.assertEqual(self.reg['dir'], os.path.join(ROOT, 'rules')); self.assertGreater(len(self.reg['rules']), 200)      # the shipped registry, not a demo
        code, out, err = self.gate('status')
        self.assertEqual(code, 1); self.assertIn('nothing scanned yet', out)                               # a gate that has looked at nothing is not a pass

    def step_1_scan_discovers_three_parts(self):
        self.model(foot='v1', hub='v1', clip='v1')
        code, out, err = self.gate('scan')
        self.assertEqual((code, err), (0, '')); self.assertIn('scan: 3 parts, 3 new, 0 changed, 0 gone, 0 document(s) not open', out)
        census = gate_common.load_census(self.root)
        self.assertEqual(sorted(census['parts']), [CLIP, FOOT, HUB])                                       # ids made from the names in the document: nobody typed a list
        self.assertEqual({p: (e['doc'], e['name'], e['sig']) for p, e in census['parts'].items()}, {p: (DOC_KEY, NAMES[p], self.sigs[p]) for p in NAMES})
        self.assertEqual(gate_plan.changed_parts(self.root), [CLIP, FOOT, HUB])
        self.assertEqual(self.gate('status')[0], 1)                                                        # discovered, not checked

    def step_2_run_finds_the_thin_wall_and_the_open_ring_face(self):
        self.facts(FOOT, fusion_census=foot_census(), fusion_inserts=foot_pocket(2.5))
        self.facts(HUB, fusion_bearings=hub_bearing()); self.facts(CLIP)
        code, out = self.run_gate()
        facts = self.by_facts()                                                                            # a clip and a ring with no hole and nothing near: most questions cannot apply
        self.assertGreater(facts, 0); self.assertEqual(self.latest()['totals']['attest_open'] + facts, 3 * self.n_design)       # answered by facts or asked: none is dropped
        self.assertEqual(code, 1); self.assertIn('3 parts, 2 open (1 FAIL, 1 GROW), %d questions (%d answered by facts), 0 not run -> exit 1' % (3 * self.n_design - facts, facts), out)
        for part in (CLIP, FOOT, HUB):                                                                     # one unit per probe the shipped rules name
            jobs = [j for j in self.fusion.calls if j['part'] == part]
            self.assertEqual([j['probe'] for j in jobs], list(gate_plan.PROBE_ORDER), part)
            self.assertEqual({(j['doc'], j['sig'], j['params']['inserts.wall_target'], json.dumps(j['target'], sort_keys=True)) for j in jobs},
                             {(DOC, self.sigs[part], 4.0, json.dumps(TARGET, sort_keys=True))})
        self.assertEqual(self.rows(FOOT), [('inserts.wall', 'GROW')])                                      # one pocket, one row: it names the wall beside the relief too
        self.assertEqual(self.rows(HUB), [('bearings.holding', 'FAIL')]); self.assertEqual(self.rows(CLIP), [])
        wall = self.finding(FOOT, 'inserts.wall')
        self.assertEqual((wall['measure'], wall['limit'], wall['by'], wall['anchor']), (2.5, 4.0, [], POCKET))
        self.assertIn('wall 2.5 at the lead-in, under 4, free space over 360 deg', wall['text'])
        self.assertIn('also 0.95 beside its D7.5 melt relief, under 2.45', wall['text'])
        self.assertIn('wider', wall['fix'][0]); self.assertNotIn('boss', wall['fix'][0])                   # the fix order starts with the wall, not with a boss
        self.assertEqual([i for i, line in enumerate(wall['fix']) if 'boss' in line], [2])
        ring = self.finding(HUB, 'bearings.holding')
        self.assertIn('bearing ref bearing 6203 (6203) at (0, 0, 8): the inner ring face + is touched by nothing', ring['text'])
        self.assertIn('GROW    %s  inserts.wall  ' % FOOT, out); self.assertIn('FAIL    %s  bearings.holding  ' % HUB, out)
        self.assertFalse(os.path.exists(gate_common.gate_path(self.root, 'stamps')))
        code, out, err = self.gate('export', FOOT)                                                         # nothing with an open row leaves the document
        self.assertEqual(code, 1); self.assertIn('NOT exported (exit 1): 1 open finding(s)', out); self.assertEqual(self.exports(), 0)

    def step_3_a_waiver_needs_a_reason_that_is_not_an_export_order(self):
        ring = self.finding(HUB, 'bearings.holding')
        waive = lambda reason: self.gate('owner', 'waive', ring['id'], '--reason', reason, '--quote', OWNER_SAID)
        code, out, err = waive(FREE_RING)
        self.assertEqual(code, 4); self.assertIn('nothing in this project puts a prompt in front of him', err)       # no ask rule yet: no owner command
        with open(os.path.join(ROOT, 'templates', 'gate', 'settings.json'), encoding='utf-8') as f:
            gate_common.write_json(self.path('.claude', 'settings.json'), json.load(f))                    # the owner adds what setup proposed
        for order in ('yes, export', 'ok print it'):
            code, out, err = waive(order)
            self.assertEqual(code, 4, order); self.assertIn('the reason is an order to export, not a reason', err); self.assertIn('nothing written', err)
        self.assertFalse(os.path.exists(self.path('gate', 'owner', 'waivers.json')))
        self.assertEqual(self.run_gate('--offline')[0], 1); self.assertEqual(self.rows(HUB), [('bearings.holding', 'FAIL')])
        code, out, err = waive(FREE_RING)
        self.assertEqual((code, err), (0, '')); self.assertIn('W-1 written to gate/owner/waivers.json: bearings.holding on %s' % HUB, out)
        w = self.read('gate', 'owner', 'waivers.json')['waivers'][0]
        self.assertEqual((w['rule'], w['part'], w['anchor'], w['context'], w['reason'], w['quote']),
                         ('bearings.holding', HUB, ring['anchor'], ring['context'], FREE_RING, OWNER_SAID))                 # feature and context come from the finding
        code, out = self.run_gate('--offline')
        self.assertEqual(code, 1); self.assertIn('1 open (1 GROW)', out); self.assertIn('1 waived', out)  # the foot still blocks: one row for its pocket
        self.assertEqual((self.rows(HUB), self.rows(HUB, 'waived')), ([], [('bearings.holding', 'FAIL')]))
        self.assertEqual([(x['waiver'], x['state']) for x in self.latest()['waivers']], [('W-1', 'valid')])

    def step_4_the_fixed_wall_is_a_new_geometry_and_its_questions_are_asked(self):
        old = self.sigs[FOOT]
        new, = self.model(foot='v2')                                                                       # the shoulder was made wider: a new B-rep signature
        self.facts(FOOT, fusion_census=foot_census(), fusion_inserts=foot_pocket(4.2))
        code, out, err = self.gate('scan')
        self.assertEqual(code, 0); self.assertIn('CHANGED  %s' % FOOT, out)
        entry = gate_common.load_census(self.root)['parts'][FOOT]
        self.assertEqual((entry['sig'], entry['prev_sig']), (new, old))
        # what was judged belongs to the old geometry: nothing of it counts for the new one, and nothing can be exported on it
        st = gate_report.status(self.root)
        self.assertEqual((st['unchecked'], [f['part'] for f in st['open']]), ([FOOT], []))
        self.assertTrue(os.path.isfile(gate_common.facts_path(self.root, FOOT, old))); self.assertFalse(os.path.exists(gate_common.facts_path(self.root, FOOT, new)))
        code, out, err = self.gate('export', FOOT)
        self.assertEqual(code, 1); self.assertIn('not every check ran on this geometry', out); self.assertEqual(self.exports(), 0)
        sent = len(self.fusion.calls)
        code, out = self.run_gate()
        facts = self.by_facts()
        self.assertEqual(code, 2); self.assertIn('3 parts, 0 open, %d questions (%d answered by facts), 0 not run, 1 waived -> exit 2' % (3 * self.n_design - facts, facts), out)
        self.assertIn('questions open: python3 gate/tools/gate.py attest list', out)
        self.assertEqual({j['sig'] for j in self.fusion.calls[sent:] if j['part'] == FOOT}, {new})         # measured again, on the new body
        self.assertEqual((self.rows(FOOT), self.rows(HUB, 'waived')), ([], [('bearings.holding', 'FAIL')]))       # the waiver holds: that feature did not change
        asked = self.questions()
        self.assertEqual(len(asked), 3 * self.n_asked - facts); self.assertEqual({part for _, part, _ in asked}, {CLIP, FOOT, HUB})
        code, out, err = self.gate('attest', 'answer', asked[0][0], '--label', 'looks fine')
        self.assertEqual(code, 4); self.assertIn('word for word', err)                                     # an answer is one of the fixed ones
        self.assertEqual(self.answer_all(), 3 * self.n_asked - facts)
        self.assertEqual(self.gate('attest', 'list')[0], 0)
        code, out = self.run_gate('--offline')
        self.assertEqual(code, 0); self.assertIn('3 parts, 0 open, 0 questions (%d answered by facts), 0 not run, 1 waived -> exit 0' % facts, out)
        self.assertEqual({p['part']: (p['verdict'], p['attested']) for p in self.latest()['parts']},
                         {p: ('pass_attested', self.n_design - self.by_facts(p)) for p in NAMES})          # an answer by facts is not the agent's: not attested
        self.assertFalse(os.path.exists(gate_common.gate_path(self.root, 'stamps')))                       # a run never stamps: the export does

    def step_5_export_checks_the_stl_before_it_stamps(self):
        mesh = {p: stl_bytes(MESHES[k](), NAMES[p]) for p, k in ((FOOT, 'foot v2'), (HUB, 'hub'), (CLIP, 'clip'))}
        for part, data in mesh.items(): self.fusion.set_export(part, data)
        code, out, err = self.gate('export', FOOT)
        self.assertEqual((code, err), (0, '')); self.assertIn('stamped -> stl/lamp_foot.stl', out)
        self.assertEqual((self.fusion.calls[-1]['probe'], self.fusion.read_only[-1]), ('fusion_export', False))
        stl = os.path.join(self.stl_dir, 'lamp_foot.stl')
        with open(stl, 'rb') as f: self.assertEqual(f.read(), mesh[FOOT])                                  # the quarantine file, promoted
        self.assertEqual(os.listdir(gate_common.gate_path(self.root, 'run', 'quarantine')), [])
        s = self.stamp(FOOT); ids = meshid.stl_ids(stl)
        self.assertEqual((s['part'], s['sig'], s['verdict'], s['attested'], s['stl'], s['stl_sha256'], s['geom_hash'], s['target']),
                         (FOOT, self.sigs[FOOT], 'pass_attested', self.n_asked - self.by_facts(FOOT), 'stl/lamp_foot.stl', ids['sha256'], ids['geom_hash'], TARGET))
        self.assertEqual(sorted(s['checks']), gate_stamp.applicable_rules(self.reg, {'source': 'fusion'}))       # every block rule of design and export
        self.assertEqual({s['checks'][r]['result'] for r in ('inserts.wall', 'walls.min_material', 'walls.one_line', 'mesh.closed', 'print.z_layers')}, {'pass'})
        self.assertEqual(s['checks']['inserts.wall']['params']['inserts.wall_target'], 4.0)
        self.assertEqual(gate_stamp.verify(self.root, self.reg, s, geom_hash=ids['geom_hash'])[0], 'valid')
        code, out, err = self.gate('export', HUB)
        self.assertEqual(code, 0); self.assertEqual(self.stamp(HUB)['checks']['bearings.holding']['result'], 'waived')      # the stamp says what it rests on
        # the clip passed every design check; its mesh has a 0.86 mm neck, which only the STL shows
        n = self.exports()
        code, out, err = self.gate('export', CLIP)
        self.assertEqual((code, self.exports()), (1, n + 1)); self.assertIn('NOT exported (exit 1): 1 open finding(s)', out)
        self.assertIn('FAIL walls.min_material: wall 0.86 mm', out); self.assertIn('under 1.5', out)
        self.assertFalse(os.path.exists(os.path.join(self.stl_dir, 'cable_clip.stl'))); self.assertIsNone(self.stamp(CLIP))
        self.assertEqual(os.listdir(gate_common.gate_path(self.root, 'run', 'quarantine')), ['demo_assembly__cable_clip.stl'])      # it never left quarantine
        code, out = self.run_gate()                                                                        # and a run after it is not a pass either
        self.assertEqual(code, 1); self.assertIn('1 parts, 1 open', out); self.assertIn('FAIL    %s  walls.min_material  wall 0.86 mm' % CLIP, out)
        neck = self.finding(CLIP, 'walls.min_material')
        self.assertEqual((neck['measure'], neck['limit'], neck['anchor']['kind']), (0.86, 1.5, 'face'))
        st = gate_report.status(self.root)
        self.assertEqual((st['changed'], st['stl_states']), ([CLIP], {'stl/lamp_foot.stl': 'valid', 'stl/turntable.stl': 'valid'}))

    def step_6_the_guard_lets_stamped_meshes_in_and_nothing_else(self):
        gate_common.write_json(self.path('gate', 'project.json'), dict(self.read('gate', 'project.json'), mode='enforce'))       # the owner's switch
        script = self.text('bambulab', 'build_3mf.py')                                                     # the plate table of the project's build script
        i = script.index('PLATES = ['); j = script.index(']\n', i) + 2
        with open(self.path('bambulab', 'build_3mf.py'), 'w', encoding='utf-8') as f: f.write(script[:i] + 'PLATES = %r\n' % (PLATES,) + script[j:])
        r = self.own(os.path.join('bambulab', 'build_3mf.py'))                                             # the vendored writer, guard and ledger
        self.assertEqual((r.returncode, r.stderr), (0, ''), r.stdout); self.assertNotIn('gate guard', r.stdout); self.assertNotIn('WARNING', r.stdout)
        self.assertEqual(self.objects(), ['Lamp foot', 'Turntable']); self.assertEqual(self.guard_log(), '')
        self.assertEqual([(e['kind'], e['object'], e['stamp'], e['tool']) for e in self.events()],
                         [('write', NAMES[p], '%s@%s' % (p, self.sigs[p][:12]), 'build') for p in (FOOT, HUB)])           # accepted with their stamps
        before = self.raw()
        # a second mesh that never went through the gate: an STL another exporter left in the folder
        H.write_stl(os.path.join(self.stl_dir, 'cable_clip.stl'), MESHES['clip']())
        with self.assertRaises(GuardRefusal) as c: G.quiet(bambu3mf.build, self.proj, self.stl_dir, WITH_CLIP)
        self.assertEqual([(r['object'], r['state'], r['stl']) for r in c.exception.rows], [('Cable clip', 'none', 'stl/cable_clip.stl')])
        self.assertIn('3 mesh(es), 1 new, 1 without a stamp (Cable clip)', str(c.exception)); self.assertIn('Nothing was written', str(c.exception))
        self.assertEqual((self.raw(), self.objects()), (before, ['Lamp foot', 'Turntable']))
        # a tool write that would drop the hole compensation the fits were measured under
        infos, files = bambu3mf.load(self.proj)
        settings = json.loads(files[PS]); self.assertEqual(settings.pop('xy_hole_compensation'), '0.1')
        with self.assertRaises(GuardRefusal) as c: G.quiet(bambu3mf.save, self.proj, dict(files, **{PS: json.dumps(settings).encode()}), infos)
        self.assertEqual([(r['rule'], r['verdict'], r['limit']) for r in c.exception.rows], [('project.settings_changed_by_write', 'FAIL', 0.1)])
        self.assertIn('xy_hole_compensation (global) of %s from 0.1 to not set, and the caller did not ask for it' % PROJ, str(c.exception))
        self.assertEqual(self.raw(), before); self.assertEqual([n for n in os.listdir(os.path.dirname(self.proj)) if n.endswith('.tmp')], [])
        self.assertEqual(self.guard_log().count('REFUSED'), 2); self.assertEqual(len(self.events()), 2)   # refused writes leave no ledger line
        G.quiet(bambu3mf.save, self.proj, files, infos)                                                    # a save that changes nothing goes through
        self.assertEqual(self.guard_log().count('REFUSED'), 2)

    def step_7_verify_reads_the_project_file(self):
        # the slicer saved the project: the turntable's plate was deleted in it and the wall generator was switched
        infos, files = bambu3mf.load(self.proj); cfg = files[MS].decode()
        oid = [o for o, _, n in bambu3mf.objects(cfg) if n == 'Turntable'][0]
        without = re.sub(r'  <plate>(?:(?!</plate>).)*?object_id" value="%s".*?</plate>\n' % oid, '', cfg, flags=re.S)
        self.assertNotEqual(without, cfg)
        files[MS] = without.encode(); files[PS] = json.dumps(dict(json.loads(files[PS]), wall_generator='arachne')).encode()
        with zipfile.ZipFile(self.proj, 'w') as z:
            for i in infos: z.writestr(i.filename, files[i.filename])
        r = self.own(os.path.join('gate', 'tools', 'gate.py'), 'verify')                                   # the project's own copy; no Fusion
        self.assertEqual((r.returncode, r.stderr), (1, ''), r.stdout)
        self.assertIn('FAIL project.object_on_no_plate %s: object Turntable of %s is on no plate' % (HUB, PROJ), r.stdout)
        self.assertIn('FAIL project.settings_match desklamp:DeskLamp.3mf: %s has wall_generator = arachne, its declared target says classic' % PROJ, r.stdout)
        found = sorted(re.findall(r'^  FAIL (\S+) (\S+):', r.stdout, re.M))
        self.assertEqual(found, [('project.build_table_differs', 'desklamp:build_3mf.py'), ('project.object_on_no_plate', HUB), ('project.settings_match', 'desklamp:DeskLamp.3mf'),
                                 ('project.stl_without_object', CLIP), ('project.stl_without_stamp', CLIP)])        # the stray STL of step 6 is named too
        self.assertNotIn('bambulab/ledger', r.stdout)                                                      # the ledger's copies of written STLs are not an STL folder
        report = self.latest()
        self.assertEqual((report['command'], report['stage'], report['totals']['block_open']), ('verify', 'project', 5))

    def step_8_the_ledger_knows_what_was_printed(self):
        write = next(e for e in self.events() if e['object'] == 'Lamp foot')
        printed = meshid.stl_ids(os.path.join(self.stl_dir, 'lamp_foot.stl'))
        self.assertEqual((write['project'], write['plate'], write['stl'], write['model'], write['stl_sha256'], write['cad']),
                         (PROJ, 'first plate', 'stl/lamp_foot.stl', printed['geom_hash'], printed['sha256'], 'Fusion Demo Assembly v12'))
        code, out = self.ledger('status', 'Lamp foot')
        self.assertEqual(code, 0); self.assertRegex(out, r'^Lamp foot +never\n'); self.assertIn('no evidence of a print since', out)      # a write is not a print
        code, out = self.ledger('say', 'Lamp foot', 'printed', '--quote', 'the foot is printed, it carries the lamp now', '--stl', os.path.join('stl', 'lamp_foot.stl'))
        self.assertEqual(code, 0); self.assertIn('recorded: Lamp foot printed as %s' % printed['geom_hash'][:8], out)
        st = json.loads(self.ledger('status', 'Lamp foot', '--json')[1])
        self.assertEqual((st['level'], st['current'], [(p['basis'], p['hash']) for p in st['prints']]), ('printed-current', printed['geom_hash'], [('owner', printed['geom_hash'])]))
        code, out = self.ledger('may-change', 'Lamp foot')
        self.assertEqual(code, 0); self.assertIn('FREE-REPRINT  printed and unchanged', out)
        # new geometry: the foot gets 2 mm thicker. The stamp of the printed version dies with it.
        old = self.sigs[FOOT]; new, = self.model(foot='v3')
        self.assertEqual(self.gate('scan')[0], 0)
        self.assertEqual(gate_stamp.verify(self.root, self.reg, self.stamp(FOOT, old))[0], 'stale')
        st = gate_report.status(self.root)
        self.assertEqual((gate_plan.changed_parts(self.root), st['unchecked'], st['stl_states']['stl/lamp_foot.stl']), ([CLIP, FOOT], [FOOT], 'stale'))
        code, out, err = self.gate('export', FOOT)
        self.assertEqual(code, 1); self.assertIn('not every check ran on this geometry', out)
        code, out = self.run_gate('--part', FOOT)
        facts = self.by_facts(FOOT)
        self.assertEqual(code, 2); self.assertIn('1 parts, 0 open, %d questions' % (self.n_design - facts), out)     # its questions are asked again for the new body
        self.assertEqual(self.answer_all(printed_interface_change='a copy is printed; no interface moved, the printed copies still fit'), self.n_asked - facts)
        self.assertEqual(self.run_gate('--offline', '--part', FOOT)[0], 0)
        self.fusion.clear('fusion_export', FOOT); self.fusion.set_export(FOOT, stl_bytes(MESHES['foot v3'](), 'Lamp foot'))
        code, out, err = self.gate('export', FOOT)
        self.assertEqual((code, err), (0, '')); self.assertEqual(self.stamp(FOOT)['sig'], new)
        now = meshid.stl_ids(os.path.join(self.stl_dir, 'lamp_foot.stl'))['geom_hash']
        st = json.loads(self.ledger('status', 'Lamp foot', '--json')[1])
        self.assertEqual((st['level'], st['current'], st['prints'][0]['hash']), ('printed-changed', now, printed['geom_hash']))
        self.assertEqual(st['changed']['boxes'], [[0.0, 0.0, 10.0, 44.0, 34.0, 12.0]])                     # where the print and the model differ: the 2 mm on top
        code, out = self.ledger('may-change', 'Lamp foot')
        self.assertEqual(code, 0); self.assertIn('printed-changed: printed as %s' % printed['geom_hash'][:8], out)
        self.assertIn('change against the as-printed copy %s: 1 box, up to 2.00 mm' % printed['geom_hash'][:8], out)
        self.assertIn('FREE-REPRINT  printed; the change touches no interface the ledger can see', out)
        self.assertEqual(self.ledger('status', 'Turntable')[1].split()[1], 'never')
        r = self.own(os.path.join('gate', 'tools', 'gate.py'), 'verify')                                   # and the project still holds the printed version
        self.assertIn('FAIL project.mesh_stale %s: the mesh of object Lamp foot in %s is not the current stamped geometry of its part' % (FOOT, PROJ), r.stdout)

    def step_9_the_session_start_digest_names_what_is_open(self):
        text = gate_hook.output(self.root)
        self.assertEqual(text.splitlines()[0], 'Print gate of %s (fusion-bambulab; skill: gate)' % self.root)
        self.assertIn('gate [enforce]: 3 parts | 1 changed since their stamp | 1 open findings | 0 questions | 1 STLs without a valid stamp', text)
        self.assertRegex(text, r'  FAIL    %s  walls.min_material  wall 0.86 mm .* under 1.5  \[F-[0-9a-f]{6}\]' % CLIP)
        self.assertIn('  checked, no stamp yet (not exported): %s' % CLIP, text); self.assertIn('  STLs without a valid stamp: stl/cable_clip.stl', text)
        self.assertIn('python3 gate/tools/gate.py run --changed', text); self.assertNotIn('forked', text); self.assertNotIn('older than', text)
        self.assertLessEqual(len(text.splitlines()), gate_report.DIGEST_LINES + 1)
        r = subprocess.run([sys.executable, HOOK, 'session-start'], input=json.dumps({'cwd': self.root, 'hook_event_name': 'SessionStart', 'source': 'startup'}),
                           capture_output=True, text=True, timeout=30, cwd=os.path.dirname(self.root), env=clean_env())
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertTrue(r.stdout.startswith('Print gate of %s (fusion-bambulab; skill: gate)' % self.root), r.stdout)
        if 'took longer than' not in r.stdout:                                                             # a loaded machine: the hook leaves in time and says so
            self.assertEqual(r.stdout.rstrip('\n'), text)
        code, out, err = self.gate('status')
        self.assertEqual(code, 1); self.assertIn('FAIL    %s  walls.min_material' % CLIP, out)


if __name__ == '__main__':
    unittest.main()
