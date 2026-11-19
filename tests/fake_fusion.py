"""A stand-in for Fusion in the tests: the transport gate_fusion_rpc.run_unit() takes, with canned facts per probe and part.

    import fake_fusion
    fusion = fake_fusion.FakeFusion(documents={'Demo Assembly': 12})
    fusion.set_facts('fusion_census', 'demo/bracket', {'holes': [], 'openings': [], 'counts': {}}, sig='ab' * 32)
    fusion.set_facts('fusion_scan', None, {'parts': [...], 'refs': []})          # a document unit: part None
    envelope = gate_fusion_rpc.run_unit(job, url, transport=fusion.transport, pinger=fusion.ping)

What the fake does for one call, exactly as Fusion + probes/_fusion.py would:
  - reads the JOB dict out of the script text (gate_fusion_rpc.job_of) and notes the call in `.calls`;
  - writes the envelope {"job", "doc", "doc_version", "part", "sig", "probe", "facts", "done", "cursor", "elapsed_s",
    "error", "open_documents"} as JSON to JOB["out"];
  - returns {"ok": True, "text": "", "error": None, "timeout": False, "down": False}.
Canned answers are looked up by (probe, part). Several answers for one key are given out one per call (the last one
repeats), so a unit that needs three calls is three set_facts() with done=False, False, True.
A document that is not in `documents` gives the error envelope the real script gives ("document ... is not open").
A (probe, part) with nothing canned gives an error envelope that says so: a test never passes on facts nobody set.

Added for the tests that drive gate.py as a whole:
  - a clock: `fusion.clock()` is the time the fake has "used" (every call adds its answer's elapsed_s, a timeout adds the
    whole HTTP timeout). gate_plan reads its wall time from gate_plan._clock; DemoProject points that at this clock, so
    a test decides how long a unit takes;
  - `fusion.read_only` notes, per call, whether the unit would have been sent read-only (gate_fusion_rpc.read_only_for);
  - `fusion.set_export(part, stl_bytes)`: the answer of the fusion_export unit, which writes the STL to JOB["args"]["stl"];
  - `fusion.clear(probe, part)` forgets the canned answers of one unit (the model changed between two runs);
  - `fusion.set_late(probe, part, facts, elapsed_s=65.0)`: the unit works longer than the host waits. The call times
    out, the ping after it gets no answer (Fusion is busy), and nothing is written until the test calls
    `fusion.finish()`: then the envelope is written as the real unit writes it, and the ping answers again. A call
    that arrives while a unit is still working is counted in `fusion.stacked` (the host must never send one).
    With broken=True the connection ends with an error instead of a timeout, and the unit works on all the same;
  - `fusion.listening`: what gate_fusion_rpc.http_listening is replaced with: does anything accept a connection
    (False only while `fusion.down`: Fusion is not running);
  - `demo_rules()` / `DEMO_JUDGES`: a small registry and its judges, independent of the shipped ones;
  - `DemoProject`: a gated project in a temporary folder wired to all of the above."""
import contextlib, io, json, os, shutil, sys, tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'gate'))
import gate_fusion_rpc      # noqa: E402

OK = {'ok': True, 'text': '', 'error': None, 'timeout': False, 'down': False}


class FakeFusion:
    def __init__(self, documents=None):
        """documents: {document name: saved version number} of the documents that are "open"."""
        self.documents = dict(documents or {})
        self.answers = {}            # (probe, part) -> list of answers
        self.calls = []              # every JOB dict received, in order
        self.pings = 0
        self.down = False            # True: no server (transport reports "down", ping fails)
        self.hang = set()            # (probe, part) keys that time out
        self.alive_after_timeout = True
        self.now = 0.0               # seconds the fake has used; see clock()
        self.read_only = []          # per call: would this unit have been sent read-only
        self.late = {}               # (probe, part) -> list of answers that come after the host stopped waiting
        self.working = None          # (job, answer) of the unit Fusion is still busy with
        self.stacked = 0             # calls that were sent while a unit was still working

    # ---- what a test sets up ----
    def set_facts(self, probe, part, facts, done=True, cursor=None, sig=None, elapsed_s=0.1):
        """Queue one answer for the unit (probe, part). `sig` None = the signature the job expects (JOB["sig"])."""
        self.answers.setdefault((probe, part), []).append({'facts': facts, 'done': done, 'cursor': cursor, 'sig': sig, 'elapsed_s': elapsed_s, 'error': None})

    def set_error(self, probe, part, error):
        """Queue an answer that is a probe error (an exception inside Fusion)."""
        self.answers.setdefault((probe, part), []).append({'facts': {}, 'done': False, 'cursor': None, 'sig': None, 'elapsed_s': 0.1, 'error': error})

    def set_timeout(self, probe, part, alive_after=True):
        """The unit never answers: the transport reports a timeout and writes no file. alive_after: does the ping after
        it get an answer (False = Fusion hangs)."""
        self.hang.add((probe, part))
        self.alive_after_timeout = alive_after

    def set_late(self, probe, part, facts, done=True, cursor=None, sig=None, elapsed_s=65.0, error=None, broken=False):
        """Queue one answer that comes late: the call times out and Fusion stays busy (no ping) until finish().
        broken=True: the call does not time out, the connection ends with an error while the unit works on."""
        self.late.setdefault((probe, part), []).append({'facts': facts, 'done': done, 'cursor': cursor, 'sig': sig, 'elapsed_s': elapsed_s, 'error': error,
                                                        'broken': broken})

    def finish(self, write=True):
        """The unit Fusion was busy with ends now. write=False: it ends without a result file (it was killed)."""
        job, a = self.working
        self.working = None
        if write:
            env = self._envelope(job)
            env.update(facts=a['facts'], done=a['done'], cursor=a['cursor'], elapsed_s=a['elapsed_s'], error=a['error'],
                       sig=a['sig'] if a['sig'] is not None else job['sig'])
            self._write(job, env)

    def _envelope(self, job):
        return {'job': job['id'], 'doc': job['doc'], 'doc_version': self.documents.get(job['doc']), 'part': job['part'],
                'sig': None, 'probe': job['probe'], 'facts': {}, 'done': False, 'cursor': job['cursor'], 'elapsed_s': 0.0,
                'error': None, 'open_documents': sorted(self.documents), 'notes': [],
                'asked': {'run': job['run'], 'sent': job.get('sent'), 'sig': job['sig'], 'cursor_key': gate_fusion_rpc.cursor_key(job['cursor'])}}

    def _write(self, job, env):
        os.makedirs(os.path.dirname(job['out']), exist_ok=True)
        with open(job['out'], 'w', encoding='utf-8') as f:
            json.dump(env, f)

    def set_export(self, part, stl_bytes, written=True, why_not=None, sig=None, **facts):
        """Queue the answer of the fusion_export unit of one part. When `written`, the call writes `stl_bytes` to the path
        the job names in JOB["args"]["stl"], as the real probe writes the quarantine STL. `facts` overrides single facts."""
        self.answers.setdefault(('fusion_export', part), []).append({'facts': dict(facts), 'done': True, 'cursor': None, 'sig': sig, 'elapsed_s': 0.1,
                                                                     'error': None, 'export': {'bytes': stl_bytes, 'written': written, 'why_not': why_not}})

    def clear(self, probe, part):
        """Forget the canned answers and the timeout of one unit."""
        self.answers.pop((probe, part), None)
        self.hang.discard((probe, part))
        self.late.pop((probe, part), None)

    def clock(self):
        """Seconds the fake has used so far: what gate_plan._clock returns in a DemoProject."""
        return self.now

    # ---- what gate_fusion_rpc.run_unit() calls ----
    def transport(self, url, script, timeout_s):
        if self.down:
            return dict(OK, ok=False, error='connection refused', down=True)
        job = gate_fusion_rpc.job_of(script)
        self.calls.append(job)
        self.read_only.append(gate_fusion_rpc.read_only_for(script))
        key = (job['probe'], job['part'])
        if self.working is not None:                 # Fusion is busy: this call waits behind the unit that still works
            self.stacked += 1
            self.now += timeout_s
            return dict(OK, ok=False, error='timeout', timeout=True)
        if self.late.get(key):
            self.working = (job, self.late[key].pop(0))
            if self.working[1].get('broken'):
                return dict(OK, ok=False, error='RemoteDisconnected: Remote end closed connection without response')
            self.now += timeout_s
            return dict(OK, ok=False, error='timeout', timeout=True)
        if key in self.hang:
            self.now += timeout_s
            return dict(OK, ok=False, error='timeout', timeout=True)
        env = self._envelope(job)
        if job['doc'] not in self.documents:
            env['error'] = 'KeyError: document %r is not open (open: %s)' % (job['doc'], ', '.join(sorted(self.documents)))
        elif key not in self.answers:
            env['error'] = 'fake_fusion: no facts set for probe %r on part %r' % key
        else:
            queue = self.answers[key]
            a = queue.pop(0) if len(queue) > 1 else queue[0]
            env.update(facts=a['facts'], done=a['done'], cursor=a['cursor'], elapsed_s=a['elapsed_s'], error=a['error'],
                       sig=a['sig'] if a['sig'] is not None else job['sig'])
            if a.get('export'):
                env['facts'] = self._export(job, a['export'], a['facts'])
        self.now += env['elapsed_s'] or 0.0
        self._write(job, env)
        return dict(OK)

    def _export(self, job, export, overrides):
        path = (job.get('args') or {}).get('stl')
        written = bool(export['written'] and path)
        if written:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(export['bytes'])
        facts = {'stl': path if written else None, 'written': written, 'why_not': None if written else (export['why_not'] or 'the fake was told not to write'),
                 'sig_before': job['sig'], 'sig_after': job['sig'], 'triangles': 12, 'open_edges': 0, 'shells': 1, 'volume_mesh': 1000.0,
                 'volume_brep': 1000.0, 'bbox_mesh': [[0, 0, 0], [10, 10, 10]], 'bbox_brep': [[0, 0, 0], [10, 10, 10]], 'quality': 'High',
                 'header': 'Fusion %s v%s %s' % (job['doc'], self.documents.get(job['doc']), (job.get('args') or {}).get('name'))}
        facts.update(overrides)
        return facts

    def ping(self, url, timeout_s=5):
        self.pings += 1
        alive = not self.down and self.alive_after_timeout and self.working is None
        if not alive:
            self.now += timeout_s
        return alive

    def listening(self, url, timeout_s=5):
        return not self.down

    # ---- what a test asks afterwards ----
    def calls_of(self, probe, part=None):
        """The JOB dicts received for one probe (and one part)."""
        return [j for j in self.calls if j['probe'] == probe and (part is None or j['part'] == part)]


# ---- a small registry and its judges, for tests that must not depend on the shipped rules ----------------------------------

def _param(default, tag='D', looser='lower'):
    return {'default': default, 'unit': 'mm', 'looser': looser, 'measured_on': 'invented for the tests', 'class': 'limit', 'formula': None, 'tag': tag}


def _rule(rid, level, stage, probe=None, params=(), attest=None, fix=(), **extra):
    check = None
    if probe:
        needs = 'fusion' if probe.startswith('fusion_') else 'stl'
        check = {'probe': probe, 'judge': rid.replace('.', '_') if (level == 'block' or extra.pop('judged', False)) else None, 'needs': needs}
    extra.pop('judged', None)
    return dict({'id': rid, 'title': rid, 'statement': 'Demo rule %s.' % rid, 'why': 'A test needs it.', 'params': list(params), 'check': check,
                 'level': level, 'stage': stage, 'fix_order': list(fix), 'attest': attest}, **extra)


def _attest(question, evidence, per, labels=()):
    a = {'question': question, 'evidence': list(evidence), 'per': per}
    if labels:
        a['labels'] = list(labels)
    return a


NONE_FINAL, BED_BOTTOM, CABLE, COVER_LATER = ('none: every functional shape is final', 'bed face is the modelled bottom',
                                              'cable or tie passage', 'the cover is mounted later and comes off first')
DEMO_REGISTRY = {
    'inserts': {'params': {'inserts.wall_target': _param(4.0), 'inserts.min_thread': dict(_param(3.2), per={'M3': 3.2, 'M4': 4.0})},
                'rules': [_rule('inserts.wall', 'block', 'design', 'fusion_inserts', ['inserts.wall_target'],
                                fix=['Make the whole wall wider where the outline is free.', 'Move the pocket with its screw.'])]},
    'walls': {'params': {'walls.min_face_width': _param(1.2, tag='M')},
              'rules': [_rule('walls.thin_faces', 'block', 'design', 'fusion_thin', ['walls.min_face_width'], fix=['Remove the sliver.'])]},
    'access': {'params': {},
               'rules': [_rule('access.hole_covered', 'block', 'design', 'fusion_openings', fix=['Open a window in what covers it.']),
                         _rule('census.unknown_hole', 'attest', 'design', 'fusion_census', judged=True,
                               attest=_attest('Hole {feature}: what is this hole for?', ['label', 'waiver'], 'finding', [CABLE, 'vent or drain'])),
                         _rule('assembly.order', 'attest', 'design',
                               attest=_attest('Opening {feature} is covered by {by}. Is {by} mounted later?', ['label', 'waiver'], 'finding', [COVER_LATER]))]},
    'process': {'params': {},
                'rules': [_rule('process.concept_geometry', 'attest', 'design',
                                attest=_attest('Is any functional shape of this part concept geometry?', ['label'], 'part', [NONE_FINAL, 'yes: listed for the owner'])),
                          _rule('process.visual_check', 'attest', 'design', attest=_attest('Screenshot of the changed zone.', ['shot'], 'part')),
                          _rule('hardware.bought_from_step', 'attest', 'design',
                                attest=_attest('Is each bought part from its STEP file?', ['label', 'param', 'na'], 'part', ['it touches no bought part']))]},
    'print': {'params': {},
              'rules': [_rule('print.bed_face', 'attest', 'export', attest=_attest('Which face is on the bed?', ['label', 'shot'], 'part', [BED_BOTTOM]))]},
    'regression': {'params': {}, 'rules': [_rule('regression.unintended', 'block', 'design', 'fusion_scan', fix=['Undo the change or name the part in --parts.'])]},
    'mesh': {'params': {}, 'rules': [_rule('mesh.closed', 'block', 'export', 'stl_mesh', fix=['Export again.'])]},
    'target': {'params': {'target.nozzle': dict(_param(0.4, looser=None), **{'class': 'geometry', 'from': 'target'}),
                          'target.line_width': dict(_param(0.42, looser=None), **{'class': 'geometry', 'from': 'target'}),
                          'target.material': dict(_param('PLA', looser=None), **{'unit': 'text', 'class': 'geometry', 'from': 'target'})},
               'rules': []},
    'teeth': {'params': {},
              'rules': [_rule('teeth.profile', 'attest', 'design', deferred=True,
                              attest=_attest('Which tooth profile check ran?', ['label', 'na'], 'part', ['no teeth on this part']))]},
}
PART_QUESTIONS = ('process.concept_geometry', 'process.visual_check', 'hardware.bought_from_step', 'print.bed_face')


def demo_rules(folder, part_questions=('process.concept_geometry',)):
    """Write the small registry into `folder` (registry/<category>.json) and return the folder. `part_questions` chooses
    which of the per-part attest rules (PART_QUESTIONS) are in it: a test that wants a clean pass keeps the list short."""
    os.makedirs(os.path.join(folder, 'registry'), exist_ok=True)
    for cat, content in DEMO_REGISTRY.items():
        rules = [r for r in content['rules'] if r['id'] not in PART_QUESTIONS or r['id'] in part_questions]
        name = ('_target' if cat == 'target' else cat) + '.json'
        with open(os.path.join(folder, 'registry', name), 'w', encoding='utf-8') as f:
            json.dump({'category': cat, 'params': content['params'], 'rules': rules}, f, indent=1)
    return folder


def _f(*a, **k):
    import gate_judges
    return gate_judges.finding(*a, **k)


def inserts_wall(facts, params, ctx):
    """Demo judge. facts: {"pockets": [{"anchor", "wall", "by": [names]}]}. Under the target: BLOCKED when something is named, else GROW."""
    limit, out = params['inserts.wall_target'], []
    for p in facts.get('pockets') or []:
        if p['wall'] < limit:
            by = sorted(p.get('by') or [])
            out.append(_f(ctx['rule'], ctx['part'], 'BLOCKED' if by else 'GROW', 'pocket wall %s under %s' % (p['wall'], limit), anchor=p['anchor'],
                          measure=p['wall'], limit=limit, context={'wall': p['wall'], 'by': by}, by=by))
    return out


def walls_thin_faces(facts, params, ctx):
    """Demo judge. facts: {"faces": [{"anchor", "width"}]}. Narrower than the limit: FAIL."""
    limit = params['walls.min_face_width']
    return [_f(ctx['rule'], ctx['part'], 'FAIL', 'face %s wide, under %s' % (x['width'], limit), anchor=x['anchor'], measure=x['width'], limit=limit,
               context={'width': x['width']}) for x in facts.get('faces') or [] if x['width'] < limit]


def access_hole_covered(facts, params, ctx):
    """Demo judge. facts: {"openings": [{"anchor", "by": [names of printed parts in front of it]}]}. Covered: BLOCKED, asks assembly.order."""
    return [_f(ctx['rule'], ctx['part'], 'BLOCKED', 'opening covered by %s' % ', '.join(o['by']), anchor=o['anchor'], measure=0.0, limit=60.0,
               context={'by': sorted(o['by'])}, by=o['by'], ask='assembly.order') for o in facts.get('openings') or [] if o.get('by')]


def census_unknown_hole(facts, params, ctx):
    """Demo judge. facts: {"holes": [{"anchor", "d", "class"}]}. A hole with no class: INFO, asks census.unknown_hole."""
    return [_f(ctx['rule'], ctx['part'], 'INFO', 'hole D%s is of no known kind' % h['d'], anchor=h['anchor'], measure=h['d'], context={'d': h['d']},
               ask='census.unknown_hole') for h in facts.get('holes') or [] if not h.get('class')]


def regression_unintended(facts, params, ctx):
    """Demo judge. facts: {"changed": [{"part", "sig", "prev_sig"}]}. A changed part outside ctx["intent"]: FAIL."""
    if ctx.get('intent') is None:
        return []
    return [_f(ctx['rule'], c['part'], 'FAIL', 'changed outside the stated intent', context={'sig': c['sig'], 'prev': c['prev_sig']})
            for c in facts.get('changed') or [] if c['part'] not in ctx['intent']]


def mesh_closed(facts, params, ctx):
    """Demo judge. facts of stl_mesh: open edges above 0: FAIL."""
    n = (facts.get('open_edges') or 0) + (facts.get('non_manifold_edges') or 0)
    return [_f(ctx['rule'], ctx['part'], 'FAIL', '%d open edges' % n, measure=n, limit=0, unit='edges')] if n else []


DEMO_JUDGES = {f.__name__: f for f in (inserts_wall, walls_thin_faces, access_hole_covered, census_unknown_hole, regression_unintended, mesh_closed)}
DEMO_PROBES = ('fusion_census', 'fusion_inserts', 'fusion_openings', 'fusion_thin')       # the units one part gets under the demo registry
CLEAN = {'fusion_census': {'holes': [], 'openings': [], 'counts': {}}, 'fusion_inserts': {'pockets': []},
         'fusion_openings': {'openings': [], 'closed': []}, 'fusion_thin': {'faces': [], 'width_cap': 2.0, 'lumps': 1, 'shells': 1}}


def cube_stl(a=10.0, hole=False):
    """Bytes of a binary STL: a closed cube of edge `a` mm with outward normals (12 triangles). hole=True leaves one
    triangle out, so the mesh has open edges."""
    import struct
    v = [(0, 0, 0), (a, 0, 0), (a, a, 0), (0, a, 0), (0, 0, a), (a, 0, a), (a, a, a), (0, a, a)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7)]       # each seen from outside, counter-clockwise
    tris = [t for q in quads for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))]
    if hole:
        tris = tris[:-1]
    out = [b'demo cube'.ljust(80, b' '), struct.pack('<I', len(tris))]
    for t in tris:
        out.append(struct.pack('<12fH', 0.0, 0.0, 0.0, *[c for i in t for c in v[i]], 0))
    return b''.join(out)


def sig_of(text):
    """A 64-hex signature made from a word, so a test can write sig_of('bracket v1')."""
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def scan_part(name, sig, **more):
    """One entry of the fusion_scan facts "parts"."""
    return dict({'name': name, 'component_id': 'c-' + name.lower().replace(' ', '-'), 'occurrence': name + ':1', 'body': 'Body1', 'sig': sig,
                 'pose': '0' * 16, 'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], 'bbox': [[0, 0, 0], [40, 30, 10]],
                 'volume': 12000.0, 'area': 3800.0, 'faces': 6, 'copies': 1}, **more)


ASK_RULES = {'permissions': {'ask': ['Edit(/gate/owner/**)', 'Bash(*gate.py owner *)']}}


def whole_stamp(root, part, sig, reg=None, target=None, **more):
    """A complete stamp of today for one part and signature, written to its place: every block rule that applies, with
    today's judge hash and today's parameter values, result "pass". Tests that only need "this part has a valid stamp"
    use it instead of typing half a stamp. `more` overrides keys. Returns the stamp."""
    import gate_common, gate_judges, gate_params, gate_registry, gate_stamp
    reg = reg or gate_registry.load_default()
    target = target if target is not None else gate_common.target_of(gate_common.load_project(root))
    resolved = gate_params.resolve(reg, gate_params.load_overrides(root), target)
    census = gate_common.load_census(root)
    source = ((census.get('parts') or {}).get(part) or {}).get('source') or 'fusion'
    checks = {rid: {'judge_hash': gate_judges.judge_hash(reg['rules'][rid]['check']['judge']),
                    'params': gate_params.values(resolved, reg['rules'][rid].get('params') or []), 'result': 'pass'}
              for rid in gate_stamp.applicable_rules(reg, {'source': source})}
    stamp = {'part': part, 'sig': sig, 'verdict': 'pass', 'attested': 0, 'checks': checks, 'stl': None, 'stl_sha256': None, 'geom_hash': None,
             'target': dict(target) if isinstance(target, dict) else None, 'doc_version': 1, 'time': gate_common.now(), 'run': 'test'}
    stamp.update(more)
    gate_common.write_json(gate_common.stamp_path(root, part, sig), stamp)
    return stamp


class DemoProject:
    """A gated project in a temporary folder, the demo registry and a FakeFusion, wired so that `gate.py` can be driven
    end to end with no Fusion:

        demo = fake_fusion.DemoProject(self)                      # self: the unittest.TestCase (for the clean-up)
        demo.model({'Bracket': 'v1'})                             # what the scan finds, and clean facts for every unit
        demo.fusion.clear('fusion_inserts', 'demo/bracket'); demo.fusion.set_facts('fusion_inserts', 'demo/bracket', {...})
        code, out, err = demo.gate('run', '--wall', '600')

    It replaces, until the test ends: gate_registry.load_default (the demo registry), the judges of the same names in
    gate_judges.JUDGES, gate_fusion_rpc.http_transport / http_ping / http_listening (the fake) and gate_plan._clock
    (the fake's clock)."""
    DOC, KEY = 'Demo Assembly', 'demo'

    def __init__(self, test, part_questions=('process.concept_geometry',), documents=None, budget_s=20, layout=None):
        import gate, gate_common, gate_judges, gate_plan, gate_registry
        self._gate, self.common = gate, gate_common
        self.root = os.path.realpath(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.root, True)
        self.rules = os.path.realpath(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.rules, True)
        demo_rules(self.rules, part_questions)
        gate_common.write_json(gate_common.gate_path(self.root, 'project.json'),
                               {'mode': 'observe', 'not_printed': ['^ref '], 'targets': {'main': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'}}})
        self.layout = {'documents': [{'key': self.KEY, 'name': self.DOC, 'stl_dir': 'stl'}], 'projects': [], 'generators': [],
                       'fusion': {'endpoint': 'http://127.0.0.1:1/mcp', 'budget_s': budget_s}}
        self.layout.update(layout or {})
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), self.layout)
        gate_common.write_json(os.path.join(self.root, '.claude', 'settings.json'), ASK_RULES)       # what the owner adds at adoption
        self.fusion = FakeFusion(documents if documents is not None else {self.DOC: 12})
        rules_dir = self.rules
        patches = [mock.patch.dict(gate_judges.JUDGES, DEMO_JUDGES),
                   mock.patch.object(gate_registry, 'load_default', lambda: gate_registry.load(rules_dir, judges=set(gate_judges.JUDGES), complete=False)),
                   mock.patch.object(gate_fusion_rpc, 'http_transport', self.fusion.transport),
                   mock.patch.object(gate_fusion_rpc, 'http_ping', self.fusion.ping),
                   mock.patch.object(gate_fusion_rpc, 'http_listening', self.fusion.listening),
                   mock.patch.object(gate_plan, '_clock', self.fusion.clock)]
        for p in patches:
            p.start()
            test.addCleanup(p.stop)

    def registry(self):
        import gate_registry
        return gate_registry.load_default()

    def model(self, parts, clean=True, doc_key=None, elapsed_s=0.1):
        """Say what the scan of the document finds: {part name: any word that stands for its geometry}. The answer
        replaces the previous one. With `clean`, every unit of every part gets facts with nothing in them (set_facts
        after clear() to give one unit something to find). Returns {part id: signature}."""
        self.fusion.clear('fusion_scan', None)
        sigs = {name: sig_of('%s %s' % (name, word)) for name, word in parts.items()}
        self.fusion.set_facts('fusion_scan', None, {'parts': [scan_part(n, s) for n, s in sigs.items()], 'refs': []}, elapsed_s=elapsed_s)
        out = {}
        for name, s in sigs.items():
            pid = self.common.part_id(doc_key or self.KEY, name)
            out[pid] = s
            if clean:
                for probe in DEMO_PROBES:
                    if (probe, pid) not in self.fusion.answers:
                        self.fusion.set_facts(probe, pid, json.loads(json.dumps(CLEAN[probe])), elapsed_s=elapsed_s)
        return out

    def facts(self, probe, part, facts, **kw):
        """Replace the canned answer of one unit."""
        self.fusion.clear(probe, part)
        self.fusion.set_facts(probe, part, facts, **kw)

    def gate(self, *argv):
        """Run gate.py with these arguments inside the project. Returns (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = self._gate.main(['--root', self.root] + list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def read(self, *parts):
        return self.common.read_json(self.path(*parts), None)
