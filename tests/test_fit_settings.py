"""The fit settings of a .3mf project (hole compensation, layer height, ...): read from the archive, held against the
declared target by gate.py verify, and compared at every save by the guard. Also the two design rules of the same
incident: a pocket for a bought part takes its size from a printed fit, and its depth is a whole number of layers.
No Fusion, no Bambu Studio: the projects are built here with bambu3mf, every name and number is invented."""
import contextlib, io, json, os, shutil, sys, tempfile, types, unittest, zipfile
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
os.environ.pop('CLAUDE_PROJECT_DIR', None)      # bambu3mf finds a gated root for a file outside it from this: never the session's project
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
    if d not in sys.path: sys.path.insert(0, d)
import gate, gate_attest, gate_common, gate_guard, gate_judges, gate_params, gate_plan, gate_registry, gate_stamp
from gate_guard import GuardRefusal
from probes import project_consistency
import bambu3mf
import test_guard as G
from test_guard import quiet, PLATES
from test_stamp import SIG1

PS, MS = 'Metadata/project_settings.config', 'Metadata/model_settings.config'
FIT = {'nozzle_diameter': ['0.4'], 'line_width': '0.45', 'layer_height': '0.3', 'initial_layer_print_height': '0.3', 'xy_hole_compensation': '0.125', 'xy_contour_compensation': '0',
       'elefant_foot_compensation': '0.15', 'wall_generator': 'classic'}
PRINT = {'enable_support': '1', 'support_type': 'tree(auto)', 'support_remove_small_overhang': '0', 'wall_sequence': 'inner wall/outer wall'}
FULL = {'nozzle': 0.4, 'line_width': 0.45, 'material': 'PETG', 'layer_height': 0.3, 'initial_layer_print_height': 0.3, 'xy_hole_compensation': 0.125, 'xy_contour_compensation': 0.0,
        'elefant_foot_compensation': 0.15, 'wall_generator': 'classic',
        'print': {'enable_support': True, 'support_type': 'tree(auto)', 'support_remove_small_overhang': 0, 'wall_sequence': 'inner wall/outer wall'}}
UNDER = {k: FULL[k] for k in gate_params.MEASURED_UNDER}


def template(path, **settings):
    """The empty template of test_guard with the fit settings a saved project has."""
    G.template(path)
    with zipfile.ZipFile(path) as z: files = {n: z.read(n) for n in z.namelist()}
    ps = json.loads(files[PS]); ps.update(FIT); ps.update(PRINT); ps.update(filament_type=['PETG']); ps.update(settings)
    files[PS] = json.dumps(ps).encode()
    with zipfile.ZipFile(path, 'w') as z:
        for n, data in files.items(): z.writestr(n, data)
    return path


def with_setting(files, **values):
    """The archive with other global values: what a tool does that lays other settings over a project."""
    ps = json.loads(files[PS]); ps.update(values)
    return dict(files, **{PS: json.dumps(ps).encode()})


class Settings(G.Guarded):
    """A gated project whose .3mf holds the fit settings and whose declared target says them."""

    def setUp(self):
        super().setUp(); bambu3mf._SETTING_NOTES.clear()
        template(self.tpl)
        self.set_project(self.mode, {'main': FULL})

    def adopt(self, plates=G.PLATES):
        """The project on disk. In enforce mode a mesh on disk that nothing backs is refused at the next save, so there it
        is the project as the gate builds it: both parts exported through the gate (stamped for the declared target)."""
        if self.mode != 'enforce':
            return G.Guarded.adopt(self, plates)
        self.stamp('demo/bracket', G.SIG1, 'bracket', target=FULL); self.stamp('demo/cover', G.SIG2, 'cover', target=FULL)
        quiet(self.build, plates)

    def settings(self, proj=None): return project_consistency.read_fit_settings(proj or self.proj)

    def raw(self, proj=None):
        with open(proj or self.proj, 'rb') as f: return f.read()


# ---- reading ---------------------------------------------------------------------------------------------------------------

class Reading(Settings):
    def test_global_values_and_every_per_object_override_are_read(self):
        self.adopt()
        quiet(bambu3mf.set_settings, self.proj, 'bracket', [('xy_hole_compensation', '0'), ('wall_loops', '4')])
        infos, files = bambu3mf.load(self.proj)
        cfg = files[MS].decode()                                                # the slicer also keeps a setting on one part of an object
        at = cfg.index('<metadata key="matrix"', cfg.index('value="cover"'))
        files[MS] = (cfg[:at] + '<metadata key="wall_generator" value="arachne"/>\n      ' + cfg[at:]).encode()
        quiet(bambu3mf.save, self.proj, files, infos)
        for source in (self.proj, bambu3mf.load(self.proj)[1]):                 # a path, and the archive a save is about to write
            s = project_consistency.read_fit_settings(source)
            self.assertEqual(s['keys'], list(project_consistency.FIT_SETTINGS))
            self.assertEqual(s['global'], {'nozzle_diameter': 0.4, 'line_width': 0.45, 'layer_height': 0.3, 'initial_layer_print_height': 0.3, 'xy_hole_compensation': 0.125, 'xy_contour_compensation': 0.0,
                                           'elefant_foot_compensation': 0.15, 'wall_generator': 'classic', 'filament': 'PETG'})
            self.assertEqual((s['objects_read'], sorted(s['object_names'])), (2, ['bracket', 'cover']))
            rows = {o['name']: o for o in s['objects']}
            self.assertEqual(rows['bracket']['overrides'], {'xy_hole_compensation': 0.0})         # wall_loops is no fit setting
            self.assertEqual(rows['cover']['overrides'], {}); self.assertEqual([(p['subtype'], p['overrides']) for p in rows['cover']['parts']], [('normal_part', {'wall_generator': 'arachne'})])

    def test_the_list_is_the_parameter_and_odd_values_stay_readable(self):
        self.assertEqual(project_consistency.fit_keys('layer_height, wall_generator  layer_height'), ['layer_height', 'wall_generator'])
        self.assertEqual(project_consistency.fit_keys(''), list(project_consistency.FIT_SETTINGS))
        self.assertEqual((project_consistency.target_key('nozzle_diameter'), project_consistency.target_key('layer_height')), ('nozzle', 'layer_height'))
        files = {PS: json.dumps({'nozzle_diameter': ['0.4', '0.6'], 'line_width': '105%', 'layer_height': 0.2, 'wall_generator': ['arachne', 'arachne']}).encode()}
        s = project_consistency.read_fit_settings(files, 'nozzle_diameter line_width layer_height wall_generator seam_gap')
        self.assertEqual(s['global'], {'nozzle_diameter': [0.4, 0.6], 'line_width': 0.42, 'layer_height': 0.2, 'wall_generator': 'arachne', 'seam_gap': None})
        self.assertEqual((s['objects'], s['objects_read'], s['object_names']), ([], None, None))     # no object settings in the archive: nothing is known about overrides
        self.assertEqual(project_consistency.read_fit_settings({PS: b'not json'})['global'], None)
        with self.assertRaises(gate_common.GateError): project_consistency.read_fit_settings(os.path.join(self.d, 'nope.3mf'))

    def test_the_override_reader_takes_every_way_a_tag_can_be_written(self):
        ms = ('<config>\n <object id="2" >\n  <metadata key="name" value="lever"/>\n  <metadata value=\'0.05\'  key = \'xy_hole_compensation\' />\n'
              '  <part id="1" subtype=\'modifier_part\'><metadata key="name" value="zone"/><metadata\n key="layer_height"\n value="0.2"/></part>\n </object>\n'
              ' <object id=\'3\'><metadata key="name" value="cap"/><metadata key="wall_generator" value="classic"/><metadata key="wall_generator" value="arachne"/></object>\n'
              ' <object id="4"><metadata key="name" value="knob"/><metadata key="layer_height" value="0.2"/><metadata key="layer_height" value="0.20"/></object>\n'
              ' <object id="5"><metadata key="name" value="broken"/>\n</config>')
        ps = json.dumps({'nozzle_diameter': ['0.4'], 'line_width': '112.5%', 'outer_wall_line_width': '0.45', 'xy_hole_compensation': '12.5%', 'layer_height': '0.3',
                         'different_settings_to_system': ['layer_height;xy_hole_compensation', '', 'nozzle_diameter']})
        s = project_consistency.read_fit_settings({PS: ps.encode(), MS: ms.encode(), 'Metadata/layer_config_ranges.xml': b'<objects><object id="2"/></objects>',
                                                   'Metadata/layer_heights_profile.txt': b'  \n'}, 'nozzle_diameter line_width outer_wall_line_width layer_height xy_hole_compensation wall_generator')
        rows = {o['name']: o for o in s['objects']}
        self.assertEqual(rows['lever']['overrides'], {'xy_hole_compensation': 0.05})                       # value before key, single quotes, spaces round the '='
        self.assertEqual([(p['name'], p['subtype'], p['overrides']) for p in rows['lever']['parts']], [('zone', 'modifier_part', {'layer_height': 0.2})])
        self.assertEqual((s['objects_read'], s['objects_unread'], s['object_names']), (3, 1, ['lever', 'cap', 'knob']))     # the tag that never closes is counted, not skipped
        self.assertEqual(s['repeated'], [{'scope': 'object cap', 'key': 'wall_generator', 'values': ['classic', 'arachne']}])       # twice with the same value is no row
        self.assertEqual((s['global']['line_width'], s['global']['outer_wall_line_width'], s['global']['xy_hole_compensation']), (0.45, 0.45, '12.5%'))     # a percent only of a line width
        self.assertEqual(s['listed'], {'nozzle_diameter': True, 'line_width': False, 'outer_wall_line_width': False, 'layer_height': True, 'xy_hole_compensation': True, 'wall_generator': False})
        self.assertEqual(s['layer_members'], ['Metadata/layer_config_ranges.xml'])                         # the empty profile is no profile
        plain = project_consistency.read_fit_settings({PS: json.dumps({'layer_height': '0.2'}).encode()}, 'layer_height')
        self.assertEqual((plain['listed'], plain['layer_members'], plain['repeated']), (None, [], []))     # no list in the file: nothing is known about it

    def test_what_a_write_changes(self):
        before = {'keys': ['a', 'b'], 'global': {'a': 0.125, 'b': 'classic'}, 'objects_read': 2, 'object_names': ['lever', 'cap'],
                  'objects': [{'name': 'lever', 'overrides': {'a': 0.1}, 'parts': []}, {'name': 'cap', 'overrides': {}, 'parts': [{'name': 'cap', 'overrides': {'b': 'arachne'}}]}]}
        same = project_consistency.settings_diff(before, json.loads(json.dumps(before)))
        self.assertEqual((same['compared'], same['changed'], same['keys']), (True, [], ['a', 'b']))
        after = {'keys': ['a', 'b'], 'global': {'a': 0.0, 'b': 'Classic'}, 'objects_read': 2, 'object_names': ['lever', 'knob'],
                 'objects': [{'name': 'lever', 'overrides': {}, 'parts': []}, {'name': 'knob', 'overrides': {'a': 0.3}, 'parts': []}]}
        rows = project_consistency.settings_diff(before, after)['changed']
        self.assertEqual([(r['key'], r['scope'], r['before'], r['after'], r['asked']) for r in rows],
                         [('a', 'global', 0.125, 0.0, False), ('b', 'global', 'classic', 'Classic', False), ('a', 'object lever', 0.1, None, False)])
        # cap is gone and knob is new: an object that only one of the two holds is not a changed setting
        notes = [{'key': 'a', 'value': '0', 'scope': 'global', 'tool': 'set_project'}, {'key': 'b', 'value': 'arachne', 'scope': 'global', 'tool': 'set_project'},
                 {'key': 'a', 'value': None, 'scope': 'lever', 'tool': 'sync'}, {'key': 'a', 'value': '0', 'scope': 'cap', 'tool': 'set_settings'}]
        rows = project_consistency.settings_diff(before, after, notes)['changed']
        self.assertEqual([(r['asked'], r['by']) for r in rows], [(True, 'set_project'), (False, None), (True, 'sync')])       # asked for arachne, got Classic: not asked
        gone = project_consistency.settings_diff(before, dict(after, **{'global': None}))['changed']
        self.assertEqual([(r['key'], r['after']) for r in gone if r['scope'] == 'global'], [('a', None), ('b', None)])          # an archive without project settings drops them all
        fresh = project_consistency.settings_diff(dict(before, **{'global': None}), after)['changed']                          # no settings on disk: no global value to lose
        self.assertEqual([(r['key'], r['scope']) for r in fresh], [('a', 'object lever')])
        self.assertEqual(project_consistency.settings_diff(None, after)['compared'], False)


# ---- gate.py verify: the file against the declared target --------------------------------------------------------------------

class Match(Settings):
    def found(self, rule='project.settings_match'):
        resolved = gate_params.resolve(self.reg, None, FULL)
        r = gate_guard.verify_project(self.d, self.reg, resolved, {'key': 'main', 'file': 'bambulab/Demo.3mf', 'stl_dir': 'stl'})
        return [f for f in r['open'] if f['rule'] == rule], r

    def test_the_incident_hole_compensation_0_where_0_125_is_declared(self):
        self.adopt()
        open_, r = self.found()
        self.assertEqual(open_, []); self.assertEqual(r['facts']['settings_write'], None)
        self.assertEqual(r['facts']['fit_settings']['global']['xy_hole_compensation'], 0.125)
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)       # the tool write that "kept the settings"
        open_, r = self.found()
        self.assertEqual([(f['verdict'], f['part'], f['measure'], f['limit']) for f in open_], [('FAIL', 'main:Demo.3mf', 0.0, 0.125)])
        self.assertIn('xy_hole_compensation = 0, its declared target says 0.125', open_[0]['text'])
        self.assertEqual(self.found('project.settings_changed_by_write')[0], [])                    # verify judges no write

    def test_an_override_of_one_object_is_found(self):
        self.adopt()
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('xy_hole_compensation', '0.05')])
        open_, r = self.found()
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in open_], [('FAIL', 0.05, 0.125)])
        self.assertIn(open_[0]['part'], ('main:cover', 'demo/cover')); self.assertIn('object cover of bambulab/Demo.3mf overrides xy_hole_compensation = 0.05', open_[0]['text'])
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('xy_hole_compensation', '0.125')])      # an override that repeats the declared value changes nothing
        self.assertEqual(self.found()[0], [])

    def test_a_project_that_sets_the_value_on_every_object_prints_with_it(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)       # the global value is 0 ...
        quiet(bambu3mf.set_settings, self.proj, 'bracket', [('xy_hole_compensation', '0.125')])
        open_, r = self.found()
        self.assertEqual([(f['verdict'], f['measure']) for f in open_], [('FAIL', 0.0)])            # ... and the cover still prints with it
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('xy_hole_compensation', '0.125')])
        open_, r = self.found()                                                                     # every object carries the declared value: nothing prints at 0
        self.assertEqual(open_, [])
        info = [f for f in r['info'] if f['rule'] == 'project.settings_match' and 'global xy_hole_compensation = 0' in f['text']]
        self.assertEqual(len(info), 1); self.assertIn('Every one of its 2 object(s) overrides it with the declared value', info[0]['text'])

    def test_an_override_that_is_meant_is_declared_and_then_held(self):
        self.adopt()
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('layer_height', '0.2')])
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in self.found()[0]], [('FAIL', 0.2, 0.3)])          # nobody declared it
        declared = dict(FULL, objects={'cover': {'layer_height': 0.2}})
        self.set_project(self.mode, {'main': declared})
        self.assertEqual(gate_params.check(self.reg, {}, declared), [])                             # "objects" is part of a declared target
        self.assertEqual(self.found()[0], [])
        quiet(bambu3mf.build, self.proj, self.stls, PLATES)                                         # a rebuild from the plate table drops what was set by hand
        open_, _ = self.found()
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in open_], [('FAIL', 0.3, 0.2)])
        self.assertIn('the declared target says layer_height = 0.2 for the object cover', open_[0]['text']); self.assertIn('it prints with the global 0.3', open_[0]['text'])
        self.set_project(self.mode, {'main': dict(FULL, objects={'cover v1': {'layer_height': 0.2}})})     # the object was renamed
        open_, _ = self.found()
        self.assertEqual([f['part'] for f in open_], ['main:cover v1']); self.assertIn('holds no object of that name', open_[0]['text'])
        for bad, word in (({'objects': ['cover']}, '"objects" must be an object of object name'), ({'objects': {'cover': {'layer_hight': 0.2}}}, 'object "cover": unknown key "layer_hight"'),
                          ({'objects': {'cover': {'layer_height': '0.2'}}}, 'object "cover": "layer_height" must be a number'), ({'objects': {'cover': {'objects': {}}}}, 'unknown key "objects"')):
            self.assertIn(word, ' '.join(gate_params.check(self.reg, {}, dict(FULL, **bad))))

    def test_a_key_that_is_not_in_the_override_list_is_said_and_does_not_block(self):
        self.adopt()
        open_, r = self.found()
        info = [f for f in r['info'] if f['rule'] == 'project.settings_match']
        self.assertEqual(open_, []); self.assertEqual(len(info), 1)                                # one row for all of them
        self.assertIn('xy_hole_compensation', info[0]['text']); self.assertIn('are not in the project\'s list of settings that differ from the system preset', info[0]['text'])
        infos, files = bambu3mf.load(self.proj); ps = json.loads(files[PS])
        quiet(bambu3mf.set_project, ps, {k: v[0] if isinstance(v, list) else v for k, v in FIT.items()})   # as the tools set them: every key is listed
        quiet(bambu3mf.save, self.proj, dict(files, **{PS: json.dumps(ps).encode()}), infos)
        open_, r = self.found()
        self.assertEqual((open_, [f for f in r['info'] if f['rule'] == 'project.settings_match']), ([], []))

    def test_doctor_says_it_too(self):
        import gate_vendor
        self.adopt()
        said = lambda: [text for level, text in gate_vendor.doctor_lines(self.d, pinger=False) if 'fit settings its target declares' in text]
        self.assertEqual(said(), [])
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        lines = said()
        self.assertEqual(len(lines), 1); self.assertTrue(lines[0].startswith('warn '))
        self.assertIn('bambulab/Demo.3mf does not print with the fit settings its target declares', lines[0]); self.assertIn('xy_hole_compensation = 0', lines[0]); self.assertIn('gate.py verify', lines[0])

    def test_a_target_that_does_not_say_them_is_not_a_pass(self):
        self.adopt(); self.set_project(self.mode, {'main': {'nozzle': 0.4, 'line_width': 0.45, 'material': 'PETG'}})
        open_, _ = self.found()
        self.assertEqual([f['verdict'] for f in open_], ['NOT_RUN', 'NOT_RUN'])
        for word in ('"layer_height"', '"xy_hole_compensation"', '"wall_generator"', 'gate/project.json'): self.assertIn(word, open_[0]['text'])
        self.assertIn('holds no "print" block', open_[1]['text'])                                    # nor whether its overhangs print with supports

    def test_the_print_block_supports_and_wall_order_every_object_prints_with(self):
        self.adopt()
        self.assertEqual(self.found()[0], [])                                                         # tree supports on, small overhangs kept: as declared
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, support_remove_small_overhang='1', enable_support='0'), infos)
        open_, _ = self.found()
        self.assertEqual([f['verdict'] for f in open_], ['FAIL', 'FAIL'])
        text = ' | '.join(f['text'] for f in open_)
        self.assertIn('bambulab/Demo.3mf has the global enable_support = 0, the declared target says 1: 2 object(s) print with it (bracket, cover)', text)
        self.assertIn('the global support_remove_small_overhang = 1, the declared target says 0', text)
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, support_remove_small_overhang='0', enable_support='1'), infos)
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('enable_support', '0')])                  # one object without supports, by hand
        open_, _ = self.found()
        self.assertEqual([f['verdict'] for f in open_], ['FAIL'])
        self.assertIn('object cover of bambulab/Demo.3mf prints with enable_support = 0, the declared target says 1', open_[0]['text'])
        self.set_project(self.mode, {'main': dict(FULL, objects={'cover': {'print': {'enable_support': False}}})})     # declared for that object
        self.assertEqual(self.found()[0], [])
        part = dict(FULL); part['print'] = {'enable_support': True}                                    # a block that does not say every key
        self.set_project(self.mode, {'main': part})
        self.assertIn('does not say support_type, support_remove_small_overhang, wall_sequence', ' '.join(f['text'] for f in self.found()[0] if f['verdict'] == 'NOT_RUN'))
        for bad in ({'print': ['tree']}, {'print': {'enable_support': None}}, {'print': {'': 1}}):
            self.assertIn('"print" must be an object of slicer setting -> value', ' '.join(gate_params.check(self.reg, {}, dict(FULL, **bad))))

    def test_the_filament_an_object_prints_with_is_a_fit_setting(self):
        self.adopt()
        self.assertEqual(self.settings()['global']['filament'], 'PETG')
        infos, files = bambu3mf.load(self.proj); ps = json.loads(files[PS])
        ps['filament_type'] = ['PETG', 'PLA']
        quiet(bambu3mf.save, self.proj, dict(files, **{PS: json.dumps(ps).encode()}), infos)
        self.assertEqual(self.found()[0], [])                                                         # a second slot nobody prints with: nothing
        quiet(bambu3mf.set_settings, self.proj, 'bracket', [('extruder', '2')])                       # the bracket moved to the PLA slot
        rows = {o['name']: o for o in self.settings()['objects']}
        self.assertEqual(rows['bracket']['overrides'], {'filament': 'PLA'})
        open_, _ = self.found()
        self.assertEqual([f['verdict'] for f in open_], ['FAIL']); self.assertIn('filament', open_[0]['text']); self.assertIn('PLA', open_[0]['text'])
        self.set_project(self.mode, {'main': dict(FULL, objects={'bracket': {'material': 'PLA'}})})   # declared: the bracket is a PLA test print
        self.assertEqual(self.found()[0], [])

    def test_verify_exits_1_on_it(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, layer_height='0.2'), infos)
        code, out = quiet(gate_guard.cmd_verify, self.d, types.SimpleNamespace(project=None))
        self.assertEqual(code, 1); self.assertIn('FAIL project.settings_match main:Demo.3mf: bambulab/Demo.3mf has layer_height = 0.2, its declared target says 0.3', out)


# ---- the guard: a write does not change a fit setting unasked -------------------------------------------------------------------

class Observe(Settings):
    def test_a_dropped_hole_compensation_is_one_loud_line_and_the_save_goes_on(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        _, out = quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        self.assertEqual(out.count('FIT SETTINGS CHANGED BY THIS WRITE'), 1)
        for word in ('gate guard [observe] bambulab/Demo.3mf', 'xy_hole_compensation (global)', 'from 0.125 to 0', 'did not ask for it', 'written anyway', 'enforce would refuse this save'):
            self.assertIn(word, out)
        self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.0)                     # observe never stops a save
        self.assertIn('FAIL this write changes xy_hole_compensation (global)', self.log())

    def test_a_save_that_keeps_them_says_nothing(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        r, out = quiet(gate_guard.check, self.proj, bambu3mf.project_meshes(files), files=files)
        self.assertEqual((r['settings']['compared'], r['settings']['changed'], r['settings']['refused']), (True, [], []))
        self.assertNotIn('FIT SETTINGS', out); self.assertNotIn('FIT SETTINGS', self.log())
        self.assertNotIn('FIT SETTINGS', self.build())                                                # a build over its own template: the same settings

    def test_a_file_nobody_registered_is_compared_too(self):
        other = os.path.join(self.d, 'bambulab', 'Trial.3mf')
        self.adopt(); shutil.copyfile(self.proj, other)
        infos, files = bambu3mf.load(other)
        _, out = quiet(bambu3mf.save, other, with_setting(files, wall_generator='arachne'), infos)
        self.assertIn('bambulab/Trial.3mf: FIT SETTINGS CHANGED BY THIS WRITE', out); self.assertIn('wall_generator (global) of bambulab/Trial.3mf from classic to arachne', out)
        self.assertIn('is not a registered project', out)

    def test_an_empty_template_is_not_an_unregistered_project_and_one_with_a_mesh_is(self):
        self.adopt()
        layout = gate_common.load_layout(self.d)
        self.assertEqual(layout['templates'], ['bambulab/template.3mf'])                              # the default: where setup_project.py puts it
        tpl = template(os.path.join(self.d, 'bambulab', 'template.3mf')); trial = os.path.join(self.d, 'bambulab', 'Trial.3mf'); shutil.copyfile(self.proj, trial)
        self.assertEqual(sorted(gate_common.unregistered(self.d, layout)['projects']), ['bambulab/Trial.3mf', 'template.3mf'])       # the empty template beside the project is not listed
        shutil.copyfile(self.proj, tpl)                                                               # a "template" that holds parts is a project nobody registered
        self.assertIn('bambulab/template.3mf', gate_common.unregistered(self.d, layout)['projects'])
        with open(tpl, 'wb') as f: f.write(b'not a zip')
        self.assertIn('bambulab/template.3mf', gate_common.unregistered(self.d, layout)['projects'])  # unreadable: not known to be empty
        code, out = quiet(gate_guard.cmd_verify, self.d, types.SimpleNamespace(project=None))
        self.assertIn('NOT REGISTERED  bambulab/template.3mf', out)

    def test_a_change_that_was_asked_for_is_printed(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj); ps = json.loads(files[PS])
        quiet(bambu3mf.set_project, ps, {'xy_hole_compensation': '0.1'})
        _, out = quiet(bambu3mf.save, self.proj, dict(files, **{PS: json.dumps(ps).encode()}), infos)      # named, away from the declared 0.125: loud
        self.assertIn('FIT SETTINGS CHANGED BY THIS WRITE', out); self.assertIn('a note covers only a change to the declared target', out); self.assertIn('written anyway', out)
        quiet(bambu3mf.save, self.proj, files, infos)                                                     # back to 0.125 (said loudly too: not named)
        self.set_project(self.mode, {'main': dict(FULL, xy_hole_compensation=0.1)})
        quiet(bambu3mf.set_project, ps, {'xy_hole_compensation': '0.1'})
        _, out = quiet(bambu3mf.save, self.proj, dict(files, **{PS: json.dumps(ps).encode()}), infos)
        self.assertEqual(out.count('gate guard [observe] bambulab/Demo.3mf: xy_hole_compensation (global) of bambulab/Demo.3mf changes from 0.125 to 0.1, as asked by set_project'), 1)
        self.assertNotIn('FIT SETTINGS', out); self.assertNotIn('the slicer loads that one', out)     # set_project lists the key

    def test_a_change_made_outside_the_tools_is_said_at_the_next_save(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        self.assertNotIn('outside the tools', quiet(bambu3mf.save, self.proj, files, infos)[1])       # the guard has seen the file: 0.125
        other = with_setting(files, xy_hole_compensation='0')
        with zipfile.ZipFile(self.proj, 'w') as z:                                                    # the template copied over the project, or the slicer saved it
            for i in infos: z.writestr(i.filename, other[i.filename])
        infos, files = bambu3mf.load(self.proj)
        _, out = quiet(bambu3mf.save, self.proj, files, infos)                                         # a save that itself changes nothing
        self.assertEqual(out.count('changed outside the tools: xy_hole_compensation from 0.125 to 0.0'), 1); self.assertIn('gate.py verify', out)
        self.assertNotIn('FIT SETTINGS CHANGED BY THIS WRITE', out)
        self.assertNotIn('outside the tools', quiet(bambu3mf.save, self.proj, files, infos)[1])       # said once: the memory follows the file

    def test_a_guard_that_cannot_be_loaded_is_said_in_observe_mode(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        real = bambu3mf._tool
        with mock.patch.object(bambu3mf, '_tool', lambda name, *paths: None if name == 'gate_guard' else real(name, *paths)):
            _, out = quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        self.assertIn('gate_guard.py is not (gate/tools): this save is not checked', out); self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.0)

    def test_the_guard_cannot_break_a_save(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        with mock.patch.object(project_consistency, 'read_fit_settings', side_effect=RuntimeError('boom')):
            _, out = quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        self.assertIn('the fit settings of this save could not be compared: RuntimeError: boom - written anyway (observe mode)', out)
        self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.0)
        with mock.patch.object(gate_registry, 'load', side_effect=gate_registry.RegistryError(['broken'])):      # no registry: the list the probe ships with
            self.assertEqual(gate_guard.fit_setting_keys(self.d), list(project_consistency.FIT_SETTINGS))
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'params.json'),
                               {'target.fit_settings': {'value': 'layer_height seam_gap', 'tag': 'U', 'source': 'owner', 'date': '2026-03-04'}})
        self.assertEqual(gate_guard.fit_setting_keys(self.d), ['layer_height', 'seam_gap'])                      # the owner's list

    def test_a_writer_that_hands_over_no_archive_is_said(self):
        self.adopt()
        r, out = quiet(gate_guard.check, self.proj, bambu3mf.project_meshes(bambu3mf.load(self.proj)[1]))        # as an older bambu3mf.py calls it
        self.assertEqual(r['settings']['compared'], False); self.assertIn('the writer handed over no archive', out); self.assertIn('written anyway', out)
        old = types.SimpleNamespace(check=lambda path, meshes, root=None: {'stamps': {}})                         # and an older guard under a new writer
        with mock.patch.dict(sys.modules, {'gate_guard': old}):
            _, out = quiet(bambu3mf.save, self.proj, *reversed(bambu3mf.load(self.proj)))
        self.assertIn('gate_guard.py is older than this bambu3mf.py: the fit settings of this save are NOT compared', out)


class Enforce(Settings):
    mode = 'enforce'

    def test_a_dropped_hole_compensation_is_refused_and_nothing_is_written(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj); before = self.raw()
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        self.assertEqual(self.raw(), before); self.assertEqual(sorted(os.listdir(os.path.dirname(self.proj))), ['Demo.3mf'])
        text = str(c.exception)
        for word in ('FIT SETTINGS CHANGED BY THIS WRITE and not asked for', 'xy_hole_compensation (global) of bambulab/Demo.3mf from 0.125 to 0', 'Nothing was written',
                     'bambu3mf.note_setting(key, value)', 'the owner changes the declared target'):
            self.assertIn(word, text)
        self.assertEqual([(r['rule'], r['verdict'], r['measure'], r['limit']) for r in c.exception.rows], [('project.settings_changed_by_write', 'FAIL', 0.0, 0.125)])
        self.assertIn('REFUSED', self.log())

    def test_a_template_laid_over_the_project_is_that_write(self):
        self.adopt(); before = self.raw()
        other = template(os.path.join(self.d, 'other-template.3mf'), xy_hole_compensation='0', layer_height='0.2')
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.new_project, other, self.proj)
        self.assertEqual(self.raw(), before); self.assertEqual(sorted(r['context'] != '' for r in c.exception.rows), [True, True])
        self.assertIn('layer_height (global)', str(c.exception)); self.assertIn('xy_hole_compensation (global)', str(c.exception))
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.new_project, other, self.proj, settings=('layer_height',))     # one of the two was meant
        self.assertIn('layer_height (global) of bambulab/Demo.3mf from 0.3 to 0.2 as asked by new_project, but the declared target says', str(c.exception))
        self.assertIn('xy_hole_compensation (global) of bambulab/Demo.3mf from 0.125 to 0, and the caller did not ask for it', str(c.exception))
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.new_project, other, self.proj, settings=('layer_height', 'xy_hole_compensation'))
        self.assertEqual(self.raw(), before)                                                           # both named, both away from the declared target:
        self.assertIn('as asked by new_project, but the declared target says layer_height = 0.3', str(c.exception))     # a note cannot move the target
        self.set_project(self.mode, {'main': dict(FULL, layer_height=0.2, xy_hole_compensation=0.0)})    # the owner declares the new values first
        _, out = quiet(bambu3mf.new_project, other, self.proj, settings=('layer_height', 'xy_hole_compensation'))
        self.assertEqual((self.settings()['global']['layer_height'], self.settings()['global']['xy_hole_compensation']), (0.2, 0.0))
        # a change that was asked for is never silent, and one whose key is not in the override list says what the slicer may do with it
        self.assertEqual(out.count('as asked by new_project'), 2); self.assertIn('xy_hole_compensation (global) of bambulab/Demo.3mf changes from 0.125 to 0', out)
        self.assertIn('where the preset has another value the slicer loads that one', out)

    def test_a_note_lives_for_one_save_and_only_for_its_own_file(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        bambu3mf.note_setting('xy_hole_compensation', '0.1', tool='a script')                      # named, and then the dict is thrown away:
        quiet(bambu3mf.save, self.proj, files, infos)                                                # a save that changes nothing uses the note up
        self.assertEqual(bambu3mf._SETTING_NOTES, [])
        with self.assertRaises(GuardRefusal): quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0.1'), infos)
        self.assertEqual(bambu3mf._SETTING_NOTES, [])                                                # a refused save uses them up too
        other = os.path.join(self.d, 'bambulab', 'Trial.3mf'); shutil.copyfile(self.proj, other)
        bambu3mf.note_setting('xy_hole_compensation', None, tool='new_project', project=other)     # "any value", for the OTHER file
        with self.assertRaises(GuardRefusal): quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        bambu3mf.note_setting('xy_hole_compensation', None, tool='new_project', project=self.proj)
        with self.assertRaisesRegex(GuardRefusal, 'a note covers only a change to the'):              # its own file, but away from the declared 0.125
            quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        self.set_project(self.mode, {'main': dict(FULL, xy_hole_compensation=0.0)})
        bambu3mf.note_setting('xy_hole_compensation', None, tool='new_project', project=self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.0)

    def test_a_key_that_leaves_the_override_list_is_refused(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj); ps = json.loads(files[PS])
        quiet(bambu3mf.set_project, ps, {'xy_hole_compensation': '0.125'})                          # as the tools set it: the key is listed
        quiet(bambu3mf.save, self.proj, dict(files, **{PS: json.dumps(ps).encode()}), infos)
        self.assertEqual(self.settings()['listed']['xy_hole_compensation'], True); self.assertEqual(self.settings()['listed']['layer_height'], False)
        infos, files = bambu3mf.load(self.proj); ps = json.loads(files[PS]); ps['different_settings_to_system'] = ['', '', '']
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.save, self.proj, dict(files, **{PS: json.dumps(ps).encode()}), infos)
        self.assertIn('takes xy_hole_compensation out of "different_settings_to_system" and keeps its value 0.125', str(c.exception))
        self.assertIn('the slicer takes the value of the system preset', str(c.exception))

    def test_a_project_file_that_is_gone_is_compared_with_what_the_guard_remembers(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, files, infos)                                                # the guard has seen the project once
        memory = gate_common.read_json(gate_common.gate_path(self.d, 'fit_settings.json'))
        self.assertEqual(memory['projects']['bambulab/Demo.3mf']['settings']['global']['xy_hole_compensation'], 0.125)
        other = template(os.path.join(self.d, 'other-template.3mf'), xy_hole_compensation='0')
        before = self.raw()
        for damage in ('removed', 'garbage', 'empty'):                                               # delete the project, then build it again from another template
            with self.subTest(damage=damage):
                os.remove(self.proj)
                if damage != 'removed':
                    with open(self.proj, 'wb') as f: f.write(b'not a zip at all' if damage == 'garbage' else b'')
                with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.new_project, other, self.proj)
                self.assertIn('xy_hole_compensation (global) of bambulab/Demo.3mf from 0.125 to 0', str(c.exception)); self.assertIn('compared with what it held at', str(c.exception))
                with open(self.proj, 'wb') as f: f.write(before)
        os.remove(self.proj)
        quiet(bambu3mf.new_project, self.tpl, self.proj)                                             # the same settings: nothing to refuse
        self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.125)

    def test_an_unreadable_file_nobody_remembers_is_not_compared_and_refused(self):
        with open(self.proj, 'wb') as f: f.write(b'not a zip at all')
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.new_project, self.tpl, self.proj)
        self.assertIn('were not compared', str(c.exception)); self.assertIn('cannot be read as a project', str(c.exception)); self.assertIn('delete the broken file', str(c.exception))
        os.remove(self.proj)
        _, out = quiet(bambu3mf.new_project, self.tpl, self.proj)                                    # a first write has nothing to lose: nothing is said
        self.assertNotIn('gate guard', out); self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.125)

    def test_a_change_the_caller_names_goes_through_only_to_the_declared_target(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj); ps = json.loads(files[PS])
        quiet(bambu3mf.set_project, ps, {'xy_hole_compensation': '0.1'})
        files[PS] = json.dumps(ps).encode()
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.save, self.proj, files, infos)      # named, but the target says 0.125
        self.assertIn('as asked by set_project, but the declared target says xy_hole_compensation = 0.125 and the project would print with 0.1', str(c.exception))
        self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.125)
        self.set_project(self.mode, {'main': dict(FULL, xy_hole_compensation=0.1)})                   # the owner changes the target first
        quiet(bambu3mf.set_project, ps, {'xy_hole_compensation': '0.1'})
        _, out = quiet(bambu3mf.save, self.proj, files, infos)
        self.assertNotIn('FIT SETTINGS', out); self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.1)
        self.assertIn('xy_hole_compensation (global) of bambulab/Demo.3mf changes from 0.125 to 0.1, as asked by set_project', self.log())
        infos, files = bambu3mf.load(self.proj)                                # the note covers that value only: 0 is still not asked for
        with self.assertRaises(GuardRefusal): quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        bambu3mf.note_setting('xy_hole_compensation', '0', tool='a script of the project')
        with self.assertRaises(GuardRefusal): quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)     # named, off the target
        self.set_project(self.mode, {'main': dict(FULL, xy_hole_compensation=0.0)})
        bambu3mf.note_setting('xy_hole_compensation', '0', tool='a script of the project')
        quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
        self.assertIn('as asked by a script of the project', self.log())
        self.set_project(self.mode, {})                                                                # a project with no declared target: no note covers anything
        bambu3mf.note_setting('xy_hole_compensation', '0.125', tool='a script of the project')
        with self.assertRaisesRegex(GuardRefusal, 'the project has no declared target'): quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0.125'), infos)

    def test_per_object_settings_through_the_tools_and_lost_by_hand(self):
        self.adopt()
        with self.assertRaisesRegex(GuardRefusal, 'says wall_generator = classic'): quiet(bambu3mf.set_settings, self.proj, 'bracket', [('wall_generator', 'arachne')])
        self.set_project(self.mode, {'main': dict(FULL, objects={'bracket': {'wall_generator': 'arachne'}})})     # declared for that object: then it goes in
        hit, _ = quiet(bambu3mf.set_settings, self.proj, 'bracket', [('wall_generator', 'arachne')])
        self.assertEqual(hit, ['bracket']); self.assertEqual(self.settings()['objects'][0]['overrides'], {'wall_generator': 'arachne'})
        infos, files = bambu3mf.load(self.proj)
        lost = dict(files, **{MS: files[MS].decode().replace('<metadata key="wall_generator" value="arachne"/>\n    ', '').encode()})
        self.assertNotEqual(lost[MS], files[MS])
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.save, self.proj, lost, infos)
        self.assertIn('wall_generator (object bracket) of bambulab/Demo.3mf from arachne to not set', str(c.exception))
        other = dict(files, **{MS: files[MS].decode().replace('value="arachne"', 'value="classic"').encode()})      # named for arachne, written classic
        with self.assertRaises(GuardRefusal): quiet(bambu3mf.save, self.proj, other, infos)
        with self.assertRaisesRegex(GuardRefusal, 'for this object'): quiet(bambu3mf.set_settings, self.proj, 'bracket', [('wall_generator', 'classic')])
        self.set_project(self.mode, {'main': FULL})
        quiet(bambu3mf.set_settings, self.proj, 'bracket', [('wall_generator', 'classic')])
        self.assertEqual(self.settings()['objects'][0]['overrides'], {'wall_generator': 'classic'})

    def test_a_rebuild_from_the_plate_table_names_its_own_settings(self):
        self.adopt()                                                            # the meshes are in the file: only the settings are at stake
        plates = [('first plate', [('bracket', 'bracket.stl', False, {'xy_hole_compensation': '0.2'})]), PLATES[1]]
        self.set_project(self.mode, {'main': dict(FULL, objects={'bracket': {'xy_hole_compensation': 0.2}})})
        _, out = quiet(bambu3mf.build, self.proj, self.stls, plates)            # the table sets an override the target declares: asked
        self.assertNotIn('FIT SETTINGS', out); self.assertEqual(self.settings()['objects'][0]['overrides'], {'xy_hole_compensation': 0.2})
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.build, self.proj, self.stls, PLATES)       # the table drops it again: nobody named that
        self.assertIn('xy_hole_compensation (object bracket)', str(c.exception))

    def test_a_template_over_a_project_with_settings_made_by_hand_is_refused_before_it_wipes_them(self):
        self.adopt()                                                            # stamped parts: a rebuild from the template finds them again
        self.stamp('demo/bracket', SIG1, 'bracket', target=FULL); self.stamp('demo/cover', 'b2' * 20, 'cover', target=FULL)
        self.set_project(self.mode, {'main': dict(FULL, objects={'cover': {'xy_hole_compensation': 0.2}})})
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('xy_hole_compensation', '0.2')])           # set for one part, in the file only
        before = self.raw()
        with self.assertRaises(GuardRefusal) as c: self.build()                                       # the template first, then every part: the override would be gone
        self.assertEqual(self.raw(), before); self.assertEqual(self.objects(), ['bracket', 'cover'])  # refused at the first of the two writes: nothing is lost
        self.assertIn('xy_hole_compensation (object cover, removed with every other object) of bambulab/Demo.3mf from 0.2 to not set', str(c.exception))
        bambu3mf.note_setting('xy_hole_compensation', None, 'cover', tool='the build script')         # a builder that means to drop it says so,
        with self.assertRaisesRegex(GuardRefusal, 'for this object'): self.build()                    # and while the target still says 0.2 for it, that is not enough
        self.set_project(self.mode, {'main': FULL})
        bambu3mf.note_setting('xy_hole_compensation', None, 'cover', tool='the build script')
        self.build(); self.assertEqual(self.settings()['objects'], [])

    def test_a_guard_that_cannot_be_loaded_does_not_switch_enforce_off(self):
        self.adopt(); before = self.raw()
        infos, files = bambu3mf.load(self.proj)
        real = bambu3mf._tool

        def broken(name, *paths):
            if name == 'gate_guard': raise SyntaxError('half a file')
            return real(name, *paths)
        for stand_in, word in ((broken, 'gate_guard.py cannot be loaded (SyntaxError: half a file)'), (lambda name, *paths: None if name == 'gate_guard' else real(name, *paths), 'gate_guard.py is not (gate/tools)')):
            with mock.patch.object(bambu3mf, '_tool', stand_in):
                with self.assertRaises(bambu3mf.GuardRefusal) as c: quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)
            self.assertIn(word, str(c.exception)); self.assertIn('nothing was written', str(c.exception)); self.assertEqual(self.raw(), before)
        self.assertEqual(type(c.exception).__name__, 'GuardRefusal'); self.assertEqual(sorted(os.listdir(os.path.dirname(self.proj))), ['Demo.3mf'])
        with open(gate_common.gate_path(self.d, 'project.json'), 'w') as f: f.write('{"mode": "enforce", broken')      # a broken owner file still says enforce
        with mock.patch.object(bambu3mf, '_tool', broken):
            with self.assertRaises(bambu3mf.GuardRefusal): quiet(bambu3mf.save, self.proj, files, infos)

    def test_what_was_not_compared_has_not_passed(self):
        self.adopt()
        meshes = bambu3mf.project_meshes(bambu3mf.load(self.proj)[1])
        with self.assertRaisesRegex(GuardRefusal, 'the writer handed over no archive'): quiet(gate_guard.check, self.proj, meshes)
        infos, files = bambu3mf.load(self.proj)
        with mock.patch.object(project_consistency, 'settings_diff', side_effect=RuntimeError('boom')):
            with self.assertRaisesRegex(GuardRefusal, 'could not be compared: RuntimeError: boom'): quiet(bambu3mf.save, self.proj, files, infos)
        r, _ = quiet(gate_guard.check, os.path.join(self.d, 'bambulab', 'New.3mf'), [], files=files)          # a file that is not on disk yet has nothing to compare with
        self.assertEqual((r['settings']['compared'], r['settings']['refused']), (False, []))


class SyncTool(Settings):
    """sync3mf merges the plate table into the saved project. What it changes on purpose it names to the guard."""
    mode = 'enforce'

    def setUp(self):
        super().setUp()
        import sync3mf
        self.sync3mf = sync3mf
        p = mock.patch.object(sync3mf, 'bambu_running', lambda: False); p.start(); self.addCleanup(p.stop)
        self.adopt()

    def sync(self, plates, **kw): return quiet(self.sync3mf.sync, self.proj, plates, self.stls, **kw)[0]

    def test_a_setting_the_plate_table_adds_goes_in_and_one_made_by_hand_survives_a_rebuild(self):
        self.set_project(self.mode, {'main': dict(FULL, objects={'bracket': {'wall_generator': 'arachne'}})})
        self.assertFalse(self.sync(PLATES).get('changed'))                      # the first run records what was built
        with_override = [('first plate', [('bracket', 'bracket.stl', False, {'wall_generator': 'arachne'})]), PLATES[1]]
        rep = self.sync(with_override)
        self.assertEqual((rep.get('refused'), rep['applied']), (None, ['bracket: wall_generator None -> arachne']))
        self.assertEqual({o['name']: o['overrides'] for o in self.settings()['objects']}, {'bracket': {'wall_generator': 'arachne'}})
        self.assertIn('wall_generator (object bracket) of bambulab/Demo.3mf changes from not set to arachne, as asked by sync', self.log())
        self.set_project(self.mode, {'main': dict(FULL, objects={'bracket': {'wall_generator': 'arachne'}, 'cover': {'xy_hole_compensation': 0.2}})})
        with self.assertRaisesRegex(GuardRefusal, 'would not print with its declared target'): self.sync(with_override, rebuild=True)   # declared, not in the file yet
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('xy_hole_compensation', '0.2')])           # the owner, by hand
        rep = self.sync(with_override, rebuild=True)
        self.assertEqual(rep.get('refused'), None); self.assertTrue(rep['changed'])
        self.assertEqual({o['name']: o['overrides'] for o in self.settings()['objects']}, {'bracket': {'wall_generator': 'arachne'}, 'cover': {'xy_hole_compensation': 0.2}})
        self.assertEqual(self.log().count('REFUSED'), 1)                        # only the rebuild before the owner set it

    def test_a_global_value_from_the_config_is_named_and_another_one_is_not(self):
        self.sync(PLATES, project={'layer_height': '0.3'})
        with self.assertRaisesRegex(GuardRefusal, 'declared target says layer_height = 0.3'): self.sync(PLATES, project={'layer_height': '0.2'})     # named, off the target
        self.assertEqual(self.settings()['global']['layer_height'], 0.3)
        self.set_project(self.mode, {'main': dict(FULL, layer_height=0.2)})
        with self.assertRaisesRegex(GuardRefusal, 'stale stamp'): self.sync(PLATES, project={'layer_height': '0.2'})   # the stamps were made for 0.3: they carry nothing now
        self.stamp('demo/bracket', G.SIG1, 'bracket', target=dict(FULL, layer_height=0.2)); self.stamp('demo/cover', G.SIG2, 'cover', target=dict(FULL, layer_height=0.2))
        rep = self.sync(PLATES, project={'layer_height': '0.2'})
        self.assertEqual(rep.get('refused'), None); self.assertEqual(self.settings()['global']['layer_height'], 0.2)
        self.assertIn('layer_height (global) of bambulab/Demo.3mf changes from 0.3 to 0.2, as asked by set_project', self.log())
        self.assertEqual(self.settings()['global']['xy_hole_compensation'], 0.125)


class NoGate(unittest.TestCase):
    def test_without_a_gate_a_save_is_what_it_always_was(self):
        d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d, True)
        proj = template(os.path.join(d, 'p.3mf'))
        infos, files = bambu3mf.load(proj)
        with mock.patch.object(bambu3mf, '_tool', side_effect=AssertionError('no gate above the file: nothing may be loaded')):
            _, out = quiet(bambu3mf.save, proj, with_setting(files, xy_hole_compensation='0'), infos)
        self.assertEqual(out, ''); self.assertEqual(project_consistency.read_fit_settings(proj)['global']['xy_hole_compensation'], 0.0)
        self.assertEqual(sorted(os.listdir(d)), ['p.3mf'])


# ---- the pocket for a bought part: its fit and its layers --------------------------------------------------------------------

LEVER = 'demo/lever'
TABLE = "PLATES = [\n    ('levers', [('Lever', 'lever.stl', %r)]),\n]\n"
# the lever stands in the document on its end: the part's +x axis points up there (part frame -> document frame, row-major)
ON_END = [0.0, 0.0, -1.0, 12.0, 0.0, 1.0, 0.0, -4.0, 1.0, 0.0, 0.0, 30.0, 0.0, 0.0, 0.0, 1.0]
POCKET = {'id': 'h1', 'anchor': {'kind': 'hole', 'axis': [1.0, 0.0, 0.0], 'point': [42.0, 6.0, 3.0]}, 'segments': [{'d': 4.2, 'len': 1.7}], 'depth': 1.7,
          'through': False, 'floor': 3.0, 'exit': None, 'class': None, 'coaxial': []}
MAGNET = {'sensors.magnet_d': {'value': 4.0, 'tag': 'M', 'source': 'caliper on the magnet in hand', 'date': '2026-03-04'},
          'sensors.magnet_h': {'value': 1.6, 'tag': 'M', 'source': 'caliper on the magnet in hand', 'date': '2026-03-04'}}


class Pocket(Settings):
    """One printed lever with a pocket for a bought magnet, judged from stored census facts (no Fusion)."""

    def setUp(self):
        super().setUp()
        layout = gate_common.read_json(gate_common.gate_path(self.d, 'layout.json'))
        layout['projects'][0]['build_table'] = 'bambulab/build_3mf.py'
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        self.table(False)
        self.census(LEVER, SIG1, stl='stl/lever.stl', matrix=ON_END, bbox=[[0.0, 0.0, 0.0], [42.0, 12.0, 6.0]])
        self.facts(POCKET)

    def table(self, orient, text=None):
        with open(os.path.join(self.d, 'bambulab', 'build_3mf.py'), 'w') as f: f.write(text if text is not None else TABLE % (orient,))

    def facts(self, *holes):
        gate_common.save_probe_facts(self.d, LEVER, SIG1, {'job': 'u-1', 'doc': 'Demo Assembly', 'doc_version': 3, 'part': LEVER, 'sig': SIG1, 'probe': 'fusion_census',
                                                          'facts': {'holes': list(holes), 'openings': [], 'counts': {}}, 'done': True, 'cursor': None, 'elapsed_s': 0.1, 'error': None})

    def frame(self): return gate_plan.print_frame(self.d, LEVER, gate_common.load_census(self.d)['parts'][LEVER])

    def judged(self, overrides=None, target=FULL):
        resolved = gate_params.resolve(self.reg, dict(MAGNET, **(overrides or {})), target)
        out = gate_plan.judge_part(self.d, self.reg, resolved, LEVER, target=target)
        return out['fits.bought_part_pocket'], out['print.z_layers']

    def test_the_bed_face_comes_from_the_build_table_and_the_pose_of_the_part(self):
        f = self.frame()                                                        # as modelled: the document's -z face on the bed, and the lever stands on its end there
        self.assertEqual((f['up'], f['bed'], f['why']), ([1.0, 0.0, 0.0], 0.0, None)); self.assertIn('bambulab/build_3mf.py: Lever on plate "levers", orient False', f['source'])
        for orient, up, bed in ((True, [-1.0, 0.0, 0.0], -42.0), ('+z', [-1.0, 0.0, 0.0], -42.0), ('-y,z90', [0.0, 1.0, 0.0], 0.0), ('+y', [0.0, -1.0, 0.0], -12.0),
                                ('+x', [0.0, 0.0, 1.0], 0.0), ('n0/0/-1', [1.0, 0.0, 0.0], 0.0), ('', [1.0, 0.0, 0.0], 0.0)):
            self.table(orient); f = self.frame()
            with self.subTest(orient=orient): self.assertEqual(([c + 0.0 for c in f['up']], f['bed'] + 0.0), (up, bed))

    def test_an_unknown_bed_face_says_why(self):
        cases = (('q', "the orientation 'q' of Lever in bambulab/build_3mf.py cannot be read"), ('n1/2', 'cannot be read'))
        for orient, why in cases:
            self.table(orient); self.assertIn(why, self.frame()['why']); self.assertIsNone(self.frame()['up'])
        self.table(None, 'PLATES = make_plates()\n'); self.assertIn('the plate list is computed, not a plain literal', self.frame()['why'])
        self.table(None, "PLATES = [('levers', [('Knob', 'knob.stl', False)])]\n"); self.assertIn('no build table of a registered .3mf project builds an object from stl/lever.stl', self.frame()['why'])
        self.table(None, "PLATES = [('a', [('Lever', 'lever.stl', False)]), ('b', [('Lever 2', 'lever.stl', '+y')])]\n"); self.assertIn('lay it on different faces', self.frame()['why'])
        self.table(False)
        self.census(LEVER, SIG1, stl='stl/lever.stl', bbox=[[0.0, 0.0, 0.0], [42.0, 12.0, 6.0]])                 # a census of before the scan kept the matrix
        self.assertIn('the census holds no pose of this part (its matrix): scan the document again', self.frame()['why'])
        self.census(LEVER, SIG1, stl='stl/lever.stl', matrix=ON_END)
        self.assertEqual((self.frame()['up'], self.frame()['bed']), ([1.0, 0.0, 0.0], None))                      # no bounding box: the axis is known, the bed level is not
        layout = gate_common.read_json(gate_common.gate_path(self.d, 'layout.json')); del layout['projects'][0]['build_table']
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        self.assertIn('no registered .3mf project names a build table', self.frame()['why'])

    def test_the_incident_pocket_fails_both_rules(self):
        fit, layers = self.judged()
        self.assertEqual([(f['verdict'], f['measure'], f['by']) for f in fit], [('FAIL', None, ['sensors.magnet_pocket_d', 'sensors.magnet_pocket_depth'])])
        self.assertIn('sensors.magnet_pocket_d = 4.2 (tag D)', fit[0]['text'])
        self.assertIn('a disc magnet', fit[0]['text']); self.assertIn('Print the coupon first', fit[0]['text']); self.assertIn('coupon', fit[0]['fix'][0])
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in layers], [('FAIL', 1.7, 1.8)])
        self.assertIn('not a whole number of 0.3 layers. It prints 1.5 or 1.8; the part is 1.6 high and needs 1.7', layers[0]['text'])
        v = gate_plan.part_verdict(self.d, self.reg, LEVER)
        self.assertEqual(v['verdict'], 'fail'); self.assertEqual({'fits.bought_part_pocket', 'print.z_layers'} - {f['rule'] for f in v['open']}, set())

    def test_after_the_coupon_and_at_whole_layers_both_pass(self):
        printed = {'sensors.magnet_d_extra': {'value': 0.2, 'tag': 'M', 'source': 'coupon in PETG, three sizes', 'date': '2026-03-06', 'measured_under': dict(UNDER)},
                   'sensors.magnet_depth_extra': {'value': 0.1, 'tag': 'U', 'source': 'owner, in chat', 'date': '2026-03-06'}}
        self.facts(dict(POCKET, segments=[{'d': 4.2, 'len': 1.8}], depth=1.8))
        self.assertEqual(self.judged(printed), ([], []))
        fit, layers = self.judged(printed, dict(FULL, xy_hole_compensation=0.0))                    # the owner changes the declared target: that fit is open again
        self.assertEqual(([(f['verdict'], f['by']) for f in fit], layers), ([('FAIL', ['sensors.magnet_pocket_d'])], []))
        self.facts(dict(POCKET, segments=[{'d': 4.2, 'len': 1.8}], depth=1.8, anchor={'kind': 'hole', 'axis': [1.0, 0.0, 0.0], 'point': [41.9, 6.0, 3.0]}))
        fit, layers = self.judged(printed)                                                          # whole layers deep, but opening between two layers
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in layers], [('FAIL', 41.9, 42.0)])

    def test_the_layers_are_counted_from_the_first_layer(self):
        printed = {'sensors.magnet_d_extra': {'value': 0.2, 'tag': 'U', 'source': 'owner, in chat', 'date': '2026-03-06'},
                   'sensors.magnet_depth_extra': {'value': 0.1, 'tag': 'U', 'source': 'owner, in chat', 'date': '2026-03-06'}}
        first = dict(FULL, initial_layer_print_height=0.2)                                          # 0.2, 0.5, 0.8, ...: the 42.0 top face is between 41.9 and 42.2
        self.facts(dict(POCKET, segments=[{'d': 4.2, 'len': 1.8}], depth=1.8))
        fit, layers = self.judged(printed, first)
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in layers], [('FAIL', 42.0, 41.9)])
        self.assertIn('opens 42 above the bed face: not where a layer ends (first layer 0.2, then 0.3 each)', layers[0]['text'])
        self.facts(dict(POCKET, segments=[{'d': 4.2, 'len': 1.8}], depth=1.8, anchor={'kind': 'hole', 'axis': [1.0, 0.0, 0.0], 'point': [41.9, 6.0, 3.0]}))
        self.assertEqual(self.judged(printed, first)[1], [])                                        # 41.9 and 40.1 both end a layer
        self.facts(dict(POCKET, segments=[{'d': 4.2, 'len': 1.7}], depth=1.7, anchor={'kind': 'hole', 'axis': [-1.0, 0.0, 0.0], 'point': [0.0, 6.0, 3.0]}))
        self.assertEqual(self.judged(printed, first)[1], [])                                        # in the bed face: 1.7 is the first layer plus five layers
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in self.judged(printed)[1]], [('FAIL', 1.7, 1.8)])       # with a 0.3 first layer it is not

    def test_an_unknown_bed_face_or_layer_height_is_not_a_pass(self):
        self.table(None, 'PLATES = make_plates()\n')
        fit, layers = self.judged()
        self.assertEqual([f['verdict'] for f in layers], ['NOT_RUN']); self.assertIn('the bed face of this part is not known (bambulab/build_3mf.py: the plate list is computed', layers[0]['text'])
        self.table(False)
        fit, layers = self.judged(target={'nozzle': 0.4, 'line_width': 0.45, 'material': 'PETG'})
        self.assertEqual([f['verdict'] for f in layers], ['NOT_RUN']); self.assertIn('does not say the layer height', layers[0]['text'])
        fit, layers = self.judged(target={k: v for k, v in FULL.items() if k != 'initial_layer_print_height'})
        self.assertEqual([f['verdict'] for f in layers], ['NOT_RUN']); self.assertIn('does not say the first layer height', layers[0]['text'])
        self.table('+y')                                                                             # on its side: the depth lies in the print plane
        self.assertEqual(self.judged()[1], [])

    def test_a_pocket_of_no_parameter_is_asked_what_goes_in(self):
        self.facts(dict(POCKET, segments=[{'d': 6.1, 'len': 3.0}], depth=3.0))
        fit, layers = self.judged()
        self.assertEqual(([(f['verdict'], f['ask']) for f in fit], layers), ([('INFO', 'fits.pocket_content')], []))
        v = gate_plan.part_verdict(self.d, self.reg, LEVER)
        q = next(x for x in v['ask_questions'] if x['rule'] == 'fits.pocket_content')
        self.assertIn('What goes in the pocket', q['question']); self.assertNotIn('{feature}', q['question']); self.assertEqual(q['evidence'], ['label', 'param', 'waiver'])
        resolved = gate_params.resolve(self.reg, dict(MAGNET, **{'fits.pin_slip_extra': {'value': 0.05, 'tag': 'U', 'source': 'owner, in chat', 'date': '2026-03-06'}}), FULL)
        answer = lambda kind, value, res=resolved, question=q: gate_attest.validate_answer(self.d, self.reg, res, question, kind, value)
        self.assertIsNone(answer('label', 'no bought part goes in: a printed part, a cable, a tool or air'))
        self.assertIn('tagged D here', answer('param', 'fits.peg_under_hole'))                      # measured elsewhere: refused
        self.assertIn('word for word', answer('label', 'a magnet'))
        # a parameter is evidence for a pocket only when the pocket has its size: naming some M or U parameter is not enough
        why = answer('param', 'fits.pin_slip_extra')                                                # the owner's number, a fit, and 0.05 is no diameter of this pocket
        self.assertIn('the pocket is D6.1 at its floor, fits.pin_slip_extra is 0.05', why); self.assertIn('does not come from that parameter', why)
        self.assertIn('takes a parameter of class "fit" as evidence; clearance.gap_to_supported_face is of class', answer('param', 'clearance.gap_to_supported_face'))
        self.assertIn('a value of the declared target', answer('param', 'target.material'))         # declared, never measured: no evidence for any rule
        self.assertIn('a value of the declared target', answer('param', 'target.nozzle'))

    def test_a_pocket_cut_from_its_parameter_is_evidence_and_one_cut_from_a_typed_number_is_not(self):
        big = {'sensors.magnet_d': {'value': 5.9, 'tag': 'M', 'source': 'caliper on the magnet in hand', 'date': '2026-03-04'},
               'sensors.magnet_d_extra': {'value': 0.2, 'tag': 'U', 'source': 'owner, in chat', 'date': '2026-03-06'},
               'sensors.magnet_depth_extra': {'value': 0.1, 'tag': 'U', 'source': 'owner, in chat', 'date': '2026-03-06'}}
        resolved = gate_params.resolve(self.reg, dict(MAGNET, **big), FULL)
        self.assertEqual((resolved['sensors.magnet_pocket_d']['value'], resolved['sensors.magnet_pocket_d']['tag']), (6.1, 'U'))
        for d, accepted in ((6.1, True), (6.12, True), (6.2, False)):                               # the table's tolerance is 0.03
            with self.subTest(d=d):
                self.facts(dict(POCKET, segments=[{'d': d, 'len': 4.0}], depth=4.0))                # too deep for a magnet pocket of the table: it is asked
                gate_plan.judge_part(self.d, self.reg, resolved, LEVER, target=FULL)
                q = next(x for x in gate_plan.part_verdict(self.d, self.reg, LEVER)['ask_questions'] if x['rule'] == 'fits.pocket_content')
                why = gate_attest.validate_answer(self.d, self.reg, resolved, q, 'param', 'sensors.magnet_pocket_d')
                self.assertEqual(why is None, accepted, why)
        gate_common.save_probe_facts(self.d, LEVER, SIG1, {'job': 'u-1', 'doc': 'Demo Assembly', 'doc_version': 3, 'part': LEVER, 'sig': SIG1, 'probe': 'fusion_census',
                                                          'facts': {}, 'done': False, 'cursor': None, 'elapsed_s': 0.1, 'error': 'boom'})
        self.assertIn('is not in the census of the part', gate_attest.validate_answer(self.d, self.reg, resolved, q, 'param', 'sensors.magnet_pocket_d'))

    def test_a_stamp_judged_with_a_measured_fit_is_open_again_when_the_tag_falls(self):
        rule = self.reg['rules']['fits.bought_part_pocket']
        printed = {'sensors.magnet_d_extra': {'value': 0.2, 'tag': 'M', 'source': 'coupon', 'date': '2026-03-06', 'measured_under': dict(UNDER)},
                   'sensors.magnet_depth_extra': {'value': 0.1, 'tag': 'M', 'source': 'coupon', 'date': '2026-03-06', 'measured_under': dict(UNDER)}}
        then = gate_params.resolve(self.reg, dict(MAGNET, **printed), FULL)
        stored = gate_params.values(then, rule['params'])
        self.assertEqual((stored['sensors.magnet_pocket_d#tag'], stored['sensors.magnet_pocket_depth#tag']), ('M', 'M'))
        self.assertEqual(gate_stamp._stricter_params(self.reg, then, rule, stored), [])
        now = gate_params.resolve(self.reg, dict(MAGNET, **printed), dict(FULL, layer_height=0.2))
        moved = gate_stamp._stricter_params(self.reg, now, rule, stored)
        self.assertIn(('sensors.magnet_pocket_d#tag', 'M', 'D'), moved)
        # the whole way: a stamp, then the owner declares another hole compensation for the project
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'params.json'), dict(MAGNET, **printed))
        self.stl('lever')
        judged = self.judged_rules(then)
        stamp = gate_stamp.build(self.d, self.reg, then, LEVER, SIG1, judged, os.path.join(self.d, 'stl', 'lever.stl'), FULL)
        gate_stamp.write(self.d, stamp)
        self.assertEqual(self.verify(stamp)[0], 'valid'); self.assertEqual(self.verify(stamp, target=FULL)[0], 'valid')
        self.set_project(self.mode, {'main': dict(FULL, xy_hole_compensation=0.0)})
        for kw in ({}, {'target': dict(FULL, xy_hole_compensation=0.0)}):
            state, why = self.verify(stamp, **kw)
            self.assertEqual(state, 'stale'); self.assertIn('#tag = M, today it is D', why)

    def test_a_stamp_is_never_held_against_another_projects_target(self):
        printed = {'sensors.magnet_d_extra': {'value': 0.2, 'tag': 'M', 'source': 'coupon', 'date': '2026-03-06', 'measured_under': dict(UNDER)},
                   'sensors.magnet_depth_extra': {'value': 0.1, 'tag': 'M', 'source': 'coupon', 'date': '2026-03-06', 'measured_under': dict(UNDER)}}
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'params.json'), dict(MAGNET, **printed))
        then = gate_params.resolve(self.reg, dict(MAGNET, **printed), FULL)
        self.stl('lever')
        loose = dict(FULL, xy_hole_compensation=0.0)                             # a second project: same nozzle and material, no hole compensation
        plain = gate_stamp.build(self.d, self.reg, then, LEVER, SIG1, self.judged_rules(then), os.path.join(self.d, 'stl', 'lever.stl'), FULL)
        named = gate_stamp.build(self.d, self.reg, then, LEVER, SIG1, self.judged_rules(then), os.path.join(self.d, 'stl', 'lever.stl'), FULL, target_key='main')
        self.assertEqual((named['for'], 'for' in plain), ('main', False))
        gate_stamp.write(self.d, named)
        for targets in ({'main': FULL, 'trial': loose}, {'trial': loose, 'main': FULL}):          # in either order of the file
            self.set_project(self.mode, targets)
            for stamp in (named, plain):
                self.assertEqual(self.verify(stamp)[0], 'valid')                 # nobody handed a target over: the stamp's own project
                self.assertEqual(self.verify(stamp, target=FULL)[0], 'valid')
                state, why = self.verify(stamp, target=loose)                    # the project the mesh goes into prints without the compensation
                self.assertEqual(state, 'stale'); self.assertIn('#tag = M, today it is D', why)
                state, why = self.verify(stamp, resolved=gate_stamp._resolved_for(self.d, self.reg, loose))
                self.assertEqual(state, 'stale')
        self.set_project(self.mode, {'main': loose})                             # the owner changes the declaration of the stamp's own project
        self.assertEqual(self.verify(named)[0], 'stale')
        # gate.py verify of each project holds the mesh against THAT project's declared target
        self.set_project(self.mode, {'trial': loose, 'main': FULL})
        layout = gate_common.read_json(gate_common.gate_path(self.d, 'layout.json'))
        layout['projects'].append({'key': 'trial', 'file': 'bambulab/Trial.3mf', 'stl_dir': 'stl'})
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        plates = [('levers', [('lever', 'lever.stl', False)])]
        for name in ('Demo.3mf', 'Trial.3mf'):
            self.build(plates, proj=os.path.join(self.d, 'bambulab', name))
        states = {}
        for key, target in (('main', FULL), ('trial', loose)):
            entry = next(e for e in layout['projects'] if e['key'] == key)
            r = gate_guard.verify_project(self.d, self.reg, gate_params.resolve(self.reg, gate_params.load_overrides(self.d), target), entry, layout)
            row = next(o for o in r['facts']['objects'] if o['name'] == 'lever')
            states[key] = (row['stamp']['state'], [f['rule'] for f in r['open'] if f['rule'] == 'project.mesh_stale'])
            if key == 'trial': self.assertIn('#tag = M, today it is D', row['stamp']['why'])
        self.assertEqual(states, {'main': ('valid', []), 'trial': ('stale', ['project.mesh_stale'])})

    def judged_rules(self, resolved):
        return {rid: {'judge_hash': gate_judges.judge_hash(self.reg['rules'][rid]['check']['judge']), 'params': gate_params.values(resolved, self.reg['rules'][rid]['params']), 'findings': []}
                for rid in gate_stamp.applicable_rules(self.reg, {'source': 'fusion'})}


# ---- the four questions that are asked now --------------------------------------------------------------------------------

class Asked(Settings):
    def test_they_are_asked_per_part_at_their_stage(self):
        self.census(LEVER, SIG1, stl='stl/lever.stl')
        design = {q['rule']: q for q in gate_attest.questions_for_part(self.d, self.reg, LEVER, SIG1, [], ('design',))}
        both = {q['rule']: q for q in gate_attest.questions_for_part(self.d, self.reg, LEVER, SIG1, [], ('design', 'export'))}
        for rid in ('access.key_swing', 'screws.count_load_joint', 'bearings.no_plastic_in_clamp'): self.assertIn(rid, design)
        self.assertNotIn('access.hole_supports', design); self.assertIn('access.hole_supports', both)           # asked at export, when the bed face is chosen
        for rid in ('bearings.insertion_path', 'teeth.profile', 'clearance.sweep_full_travel', 'screws.head_recess'): self.assertNotIn(rid, both)
        resolved = gate_params.resolve(self.reg, None, FULL)
        for rid in ('access.hole_supports', 'access.key_swing', 'screws.count_load_joint', 'bearings.no_plastic_in_clamp'):
            q = both[rid]
            with self.subTest(rule=rid):
                self.assertTrue(q['question'].strip().endswith('?')); self.assertRegex(q['q'], '^Q-[0-9a-f]{6}$'); self.assertIsNone(q['answer'])
                self.assertEqual(q['evidence'], self.reg['rules'][rid]['attest']['evidence']); self.assertEqual(q['labels'], self.reg['rules'][rid]['attest']['labels'])
                self.assertIsNone(gate_attest.validate_answer(self.d, self.reg, resolved, q, 'label', q['labels'][0]))
                self.assertIn('word for word', gate_attest.validate_answer(self.d, self.reg, resolved, q, 'label', 'looks fine'))
                self.assertIn('needs a reason', gate_attest.validate_answer(self.d, self.reg, resolved, q, 'na', 'no'))
        self.assertIsNotNone(gate_attest.validate_answer(self.d, self.reg, resolved, both['screws.count_load_joint'], 'shot', 'shots/x.png'))    # not an evidence kind of that rule

    def test_a_label_the_facts_contradict_is_refused(self):
        self.census(LEVER, SIG1, stl='stl/lever.stl')
        q = {x['rule']: x for x in gate_attest.questions_for_part(self.d, self.reg, LEVER, SIG1, [], ('design', 'export'))}
        resolved = gate_params.resolve(self.reg, None, FULL)
        answer = lambda rule, label: gate_attest.validate_answer(self.d, self.reg, resolved, q[rule], 'label', label)

        def facts(probe, data, error=None):
            gate_common.save_probe_facts(self.d, LEVER, SIG1, {'job': 'u-1', 'doc': 'Demo Assembly', 'doc_version': 3, 'part': LEVER, 'sig': SIG1, 'probe': probe,
                                                              'facts': data, 'done': error is None, 'cursor': None, 'elapsed_s': 0.1, 'error': error})
        none, swing = 'this part has no screws', 'every screw: the L key swings free of the parts fitted before it'
        self.assertIsNone(answer('access.key_swing', none))                      # nothing was measured: the label is the only word
        facts('fusion_screws', {'screws': [{'ref': 'ref screw M3x12 cap'}, {'ref': 'ref screw M3x12 cap (2)'}], 'washers': [{'ref': 'ref washer M3'}]})
        self.assertIn('the facts of this part list 2 (fusion_screws: screws)', answer('access.key_swing', none))
        self.assertIsNone(answer('access.key_swing', swing))
        self.assertIn('the facts of this part list 2', answer('hardware.refs_present', 'this part has no fastener features'))
        self.assertIsNone(answer('hardware.refs_present', 'every fastener feature of this part has its ref body, rebuilt after the last hole change'))
        facts('fusion_screws', {'screws': [], 'washers': [{'ref': 'ref washer M3'}]})
        # a washer lies on the part and no screw is a row: the head on that washer was not recognised, so "no screws" is refused all the same
        self.assertIn('show what this answer denies: 1 washer(s) at this part', answer('access.key_swing', none))
        facts('fusion_screws', {'screws': [], 'washers': [], 'fastener_holes': [{'hole': 'h1', 'role': 'clearance', 'screw': None, 'bodies': ['ref fasteners / stud (1)']}]})
        self.assertIn('1 screw hole(s) or insert pocket(s) with a screw or a bought body on the axis', answer('access.key_swing', none))     # a blind screw unit is no "no screws"
        self.assertIn('show what this answer denies', gate_attest.validate_answer(self.d, self.reg, resolved, q['screws.count_load_joint'], 'na', 'no screw holds this part at all'))
        facts('fusion_screws', {'screws': [], 'washers': [], 'fastener_holes': [{'hole': 'h1', 'role': 'clearance', 'screw': None, 'bodies': []}]})
        self.assertIsNone(answer('access.key_swing', none))                      # an empty screw hole shows no screw: the label is the agent's word
        facts('fusion_screws', {'screws': [], 'washers': [{'ref': 'ref washer M3'}]})
        self.assertIn('the facts list none (fusion_screws: screws)', answer('access.key_swing', swing))
        self.assertIn('list 1 (fusion_screws: washers)', answer('hardware.refs_present', 'this part has no fastener features'))
        self.assertIn('the facts list none', answer('screws.count_load_joint', 'every load joint of this part has at least the minimum count, on the largest circle the parts allow'))
        self.assertIsNone(answer('screws.count_load_joint', 'no joint of this part carries torque or tipping'))        # nothing in the facts says what a joint carries
        steel, no_clamp = 'steel only: head, washer, ring', 'this part has no bearing clamp'
        facts('fusion_bearings', {'bearings': [{'ref': 'ref bearing 6203', 'clamps': []}], 'seats_without_bearing': []})
        self.assertIn('the facts list none (fusion_bearings: bearings.clamps)', answer('bearings.no_plastic_in_clamp', steel)); self.assertIsNone(answer('bearings.no_plastic_in_clamp', no_clamp))
        facts('fusion_bearings', {'bearings': [{'ref': 'ref bearing 6203', 'clamps': [{'ring': 'outer', 'side': '+'}]}, {'ref': 'ref bearing 6203 (2)', 'clamps': [{'ring': 'outer', 'side': '-'}]}]})
        self.assertIn('list 2 (fusion_bearings: bearings.clamps)', answer('bearings.no_plastic_in_clamp', no_clamp)); self.assertIsNone(answer('bearings.no_plastic_in_clamp', steel))
        facts('fusion_bearings', {}, error='boom')                               # a unit that failed proves nothing either way
        self.assertIsNone(answer('bearings.no_plastic_in_clamp', no_clamp)); self.assertIsNone(answer('bearings.no_plastic_in_clamp', steel))

    def test_the_export_questions_can_be_answered_before_the_part_is_in_a_project(self):
        for rid in ('access.hole_supports', 'print.bed_face'):
            a = self.reg['rules'][rid]['attest']
            self.assertTrue(any('owed' in label for label in a['labels']), rid)  # one label is true for a geometry no project holds yet
            self.assertIn('in no .3mf project', self.reg['rules'][rid]['note'])

    def test_a_part_made_by_code_is_asked_about_supports_too(self):
        rules = {q['rule'] for q in gate_attest.questions_for_part(self.d, self.reg, 'demo/shim', 'f' * 40, [], ('design', 'export'))}
        self.assertIn('access.hole_supports', rules)                             # a hole of an STL made by code is closed by supports just the same


# ---- the owner records a fit with what it was printed under ---------------------------------------------------------------------

class Owner(Settings):
    def setUp(self):
        super().setUp()
        gate_common.write_json(os.path.join(self.d, '.claude', 'settings.json'), {'permissions': {'ask': ['Edit(/gate/owner/**)', 'Write(/gate/owner/**)', 'Bash(*gate.py owner *)']}})

    def run_gate(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): code = gate.main(['--root', self.d] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def params(self): return gate_common.read_json(gate_common.gate_path(self.d, 'owner', 'params.json'), {})

    def test_a_measured_fit_is_stored_with_the_declared_target_it_was_printed_from(self):
        self.adopt()                                                             # the project file the coupon was printed from
        code, out, err = self.run_gate('owner', 'param', 'sensors.magnet_d_extra', '0.15', '--tag', 'M', '--source', 'coupon, three sizes', '--quote', 'the middle one fits')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.params()['sensors.magnet_d_extra']['measured_under'], UNDER)
        self.assertIn('measured under: nozzle 0.4, material PETG, xy_hole_compensation 0.125, layer_height 0.3', out)
        res = gate_params.resolve(self.reg, gate_params.load_overrides(self.d), FULL)
        self.assertEqual((res['sensors.magnet_d_extra']['tag'], res['sensors.magnet_d_extra']['value']), ('M', 0.15))
        code, out, err = self.run_gate('owner', 'param', 'sensors.magnet_d_extra', '0.15', '--tag', 'U', '--source', 'owner', '--quote', 'use 0.15')
        self.assertEqual(code, 0, err); self.assertNotIn('measured_under', self.params()['sensors.magnet_d_extra'])     # his decision needs no print

    def test_it_is_refused_when_nothing_says_what_the_print_was_made_with(self):
        self.set_project(self.mode, {'main': {'nozzle': 0.4, 'line_width': 0.45, 'material': 'PETG'}})
        code, _, err = self.run_gate('owner', 'param', 'sensors.magnet_d_extra', '0.15', '--tag', 'M', '--source', 'coupon', '--quote', 'it fits')
        self.assertEqual(code, 4); self.assertIn('does not say xy_hole_compensation, layer_height', err); self.assertEqual(self.params(), {})
        self.set_project(self.mode, {'main': FULL, 'fine': dict(FULL, nozzle=0.2)})
        code, _, err = self.run_gate('owner', 'param', 'sensors.magnet_d_extra', '0.15', '--tag', 'M', '--source', 'coupon', '--quote', 'it fits')
        self.assertEqual(code, 4); self.assertIn('--under KEY (declared targets: fine, main)', err)
        code, _, err = self.run_gate('owner', 'param', 'sensors.magnet_d_extra', '0.15', '--tag', 'M', '--source', 'coupon', '--quote', 'it fits', '--under', 'fine')
        self.assertEqual(code, 4); self.assertIn('registers no .3mf project with the key fine', err); self.assertEqual(self.params(), {})
        code, _, err = self.run_gate('owner', 'param', 'sensors.magnet_d_extra', '0.15', '--tag', 'M', '--source', 'coupon', '--quote', 'it fits', '--under', 'main')
        self.assertEqual(code, 4); self.assertIn('the project file bambulab/Demo.3mf does not exist', err); self.assertEqual(self.params(), {})
        self.adopt()
        code, _, err = self.run_gate('owner', 'param', 'sensors.magnet_d_extra', '0.15', '--tag', 'M', '--source', 'coupon', '--quote', 'it fits', '--under', 'main')
        self.assertEqual(code, 0, err); self.assertEqual(self.params()['sensors.magnet_d_extra']['measured_under']['nozzle'], 0.4)
        code, _, err = self.run_gate('owner', 'param', 'inserts.wall_target', '4.5', '--tag', 'U', '--source', 'owner', '--quote', 'four and a half', '--under', 'main')
        self.assertEqual(code, 4); self.assertIn('--under goes with --tag M on a fit parameter', err)


    def test_a_coupon_printed_while_the_file_differs_from_the_declared_target_is_not_stored_as_measured_under_it(self):
        self.adopt()
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0'), infos)       # the incident state: the file says 0, the target 0.125
        args = ('owner', 'param', 'sensors.magnet_d_extra', '0.15', '--tag', 'M', '--source', 'coupon', '--quote', 'it fits')
        code, _, err = self.run_gate(*args)
        self.assertEqual(code, 4); self.assertEqual(self.params(), {})
        self.assertIn('xy_hole_compensation is 0.0 in bambulab/Demo.3mf and 0.125 in the declared target', err); self.assertIn('gate.py verify', err)
        infos, files = bambu3mf.load(self.proj)
        quiet(bambu3mf.save, self.proj, with_setting(files, xy_hole_compensation='0.125'), infos)
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('xy_hole_compensation', '0.05')])       # one object prints with another value: the same refusal
        code, _, err = self.run_gate(*args)
        self.assertEqual(code, 4); self.assertIn('object cover overrides xy_hole_compensation = 0.05', err)
        quiet(bambu3mf.set_settings, self.proj, 'cover', [('xy_hole_compensation', '0.125')])
        self.assertEqual(self.run_gate(*args)[0], 0)

    def test_one_size_cannot_be_measured_while_the_base_value_is_not(self):
        self.adopt()
        size = ('owner', 'param', 'fits.sliding_per_side', '0.3', '--key')
        code, _, err = self.run_gate(*size, 'M4', '--tag', 'M', '--source', 'coupon', '--quote', 'slides')
        self.assertEqual(code, 4); self.assertIn('the tag belongs to the whole parameter', err); self.assertEqual(self.params(), {})
        self.assertEqual(self.run_gate(*size, 'M4', '--tag', 'U', '--source', 'owner', '--quote', 'take 0.3 for M4')[0], 0)
        self.assertEqual(self.params()['fits.sliding_per_side']['tag'], 'U')


if __name__ == '__main__':
    unittest.main()
