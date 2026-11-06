"""Run: python3 -m unittest discover tests. Needs neither Fusion nor Bambu Studio: the template is synthetic."""
import json, os, re, shutil, subprocess, sys, tempfile, unittest, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'bambu'))
import bambu3mf, sync3mf
sync3mf.GUARD = False
PS, MS = 'Metadata/project_settings.config', 'Metadata/model_settings.config'


def settings(bed=180):
    k = bambu3mf.FILAMENT_KEYS; names = ['Bambu PLA @X', 'Bambu PETG @X']
    ps = {key: ['0'] * (2 * stride) for key, stride in k.items()}
    ps.update(filament_settings_id=names, filament_colour=['#FFFFFF', '#000000'], flush_volumes_matrix=['0', '1', '2', '0'],
              filament_self_index=['1'] * k['filament_self_index'] + ['2'] * k['filament_self_index'],
              printable_area=['0x0', '%dx0' % bed, '%dx%d' % (bed, bed), '0x%d' % bed], different_settings_to_system=['', 'a', 'b', ''], brim_width='5')
    return ps


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.d)
        self.tpl = os.path.join(self.d, 'template.3mf')
        with zipfile.ZipFile(self.tpl, 'w') as z:
            z.writestr('[Content_Types].xml', '<Types/>'); z.writestr('_rels/.rels', '<Relationships/>'); z.writestr(PS, json.dumps(settings()))
        self.stl = os.path.join(self.d, 'part.stl'); bambu3mf.purge_stl(self.stl, 300)
        self.proj = os.path.join(self.d, 'p.3mf')

    def files(self): return bambu3mf.load(self.proj)[1]


class Filaments(Base):
    def test_one_filament_keeps_a_full_self_index(self):
        one = bambu3mf.only_filament(settings(), 'PETG')
        self.assertEqual(one['filament_settings_id'], ['Bambu PETG @X'])
        self.assertEqual(len(one['filament_self_index']), bambu3mf.FILAMENT_KEYS['filament_self_index'])
        self.assertEqual(one['different_settings_to_system'], ['', 'b', ''])

    def test_colours_make_slots(self):
        one = bambu3mf.only_filament(settings(), 'PETG', ['#111111', '#222222'])
        self.assertEqual(len(one['filament_settings_id']), 2); self.assertEqual(len(one['flush_volumes_matrix']), 4)

    def test_ambiguous_filament_is_refused(self):
        with self.assertRaises(ValueError): bambu3mf.only_filament(settings(), 'Bambu')


class Build(Base):
    def build(self, plates, **kw):
        bambu3mf.new_project(self.tpl, self.proj, 'PLA'); bambu3mf.build(self.proj, self.d, plates, **kw)

    def test_sequence_has_one_entry_per_plate(self):
        self.build([('a', [('A', 'part.stl', False)]), ('b', [('B', 'part.stl', '+z')])])
        self.assertEqual(sorted(json.loads(self.files()['Metadata/filament_sequence.json'])), ['plate_1', 'plate_2'])

    def test_bed_comes_from_the_template(self):
        self.build([('a', [('A', 'part.stl', False)])])
        x, y = re.search(r'<item [^>]*transform="[^"]* ([-\d.]+) ([-\d.]+) [-\d.]+"', self.files()['3D/3dmodel.model'].decode()).groups()
        self.assertAlmostEqual(float(x), 90, 3); self.assertAlmostEqual(float(y), 90, 3)

    def test_blocker_part_id_is_the_component_objectid(self):
        self.build([('a', [('A', 'part.stl', False, {'_blockers': [((0, 0, 0), (1, 1, 1))]})])])
        f = self.files(); ms, model = f[MS].decode(), f['3D/3dmodel.model'].decode()
        pid = re.search(r'<part id="(\d+)" subtype="support_blocker"', ms).group(1)
        self.assertIn('objectid="%s"' % pid, model)

    def test_global_override_is_listed(self):
        self.build([('a', [('A', 'part.stl', False)])], project={'brim_width': '8'})
        ps = json.loads(self.files()[PS]); self.assertEqual(ps['brim_width'], '8'); self.assertIn('brim_width', ps['different_settings_to_system'][0])

    def test_bad_name_characters_are_cleaned(self):
        self.assertEqual(bambu3mf.bambu_name('a: b/c?'), 'a - b-c')


class Sync(Base):
    def plates(self): return [('a', [('A', self.stl, False)])]

    def test_same_mesh_changes_nothing_and_new_mesh_goes_in(self):
        bambu3mf.new_project(self.tpl, self.proj, 'PLA'); bambu3mf.build(self.proj, '/', self.plates())
        self.assertFalse(sync3mf.sync(self.proj, self.plates()).get('changed'))
        bambu3mf.purge_stl(self.stl, 900)
        self.assertEqual(sync3mf.sync(self.proj, self.plates())['swapped'], ['A'])


class HandEdits(Base):
    """The user picks another filament and a brim in Bambu, then the model changes and the build runs again."""
    def edit_in_bambu(self):
        infos, files = bambu3mf.load(self.proj); ps = json.loads(files[PS])
        ps['filament_settings_id'] = ['Bambu PETG @X']; ps['brim_type'] = 'outer_only'; ps['brim_width'] = '7'
        ps['different_settings_to_system'][0] = 'brim_type;brim_width'
        files[PS] = json.dumps(ps).encode(); bambu3mf.save(self.proj, files, infos)

    def test_they_survive_a_rebuild_with_a_new_mesh(self):
        plates, config = [('a', [('A', self.stl, False)])], {'brim_type': 'no_brim'}
        bambu3mf.new_project(self.tpl, self.proj, 'PLA'); bambu3mf.build(self.proj, '/', plates, project=config)
        self.edit_in_bambu(); bambu3mf.purge_stl(self.stl, 900)
        rep = sync3mf.sync(self.proj, plates, project=config)
        ps = json.loads(self.files()[PS])
        self.assertEqual(rep['swapped'], ['A'])
        self.assertEqual((ps['filament_settings_id'], ps['brim_type'], ps['brim_width']), (['Bambu PETG @X'], 'outer_only', '7'))


class Setup(unittest.TestCase):
    def test_second_run_keeps_existing_work(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d); run = [sys.executable, os.path.join(ROOT, 'tools', 'setup_project.py'), d, '--name', 'Demo']
        subprocess.run(run, check=True, capture_output=True)
        rules = os.path.join(d, 'DESIGN_RULES.md'); open(rules, 'w').write('mine')
        tool = os.path.join(d, 'bambulab', 'tools', 'bambu3mf.py'); open(tool, 'a').write('# local fix\n')
        subprocess.run(run, check=True, capture_output=True)
        self.assertEqual(open(rules).read(), 'mine'); self.assertIn('# local fix', open(tool).read())
        self.assertEqual(open(os.path.join(d, 'CLAUDE.md')).read().count('fusion-bambulab:start'), 1)


class Safety(Base):
    def test_backup_keeps_the_newest(self):
        shutil.copy(self.tpl, self.proj); d = os.path.join(self.d, '.backups'); os.makedirs(d)
        for i in range(12): open(os.path.join(d, '2026010%02d-p.3mf' % i), 'w').close()
        self.assertTrue(os.path.exists(bambu3mf.backup(self.proj, keep=5))); self.assertEqual(len(os.listdir(d)), 5)

    def test_save_leaves_no_temp_file(self):
        shutil.copy(self.tpl, self.proj); infos, files = bambu3mf.load(self.proj); bambu3mf.save(self.proj, files, infos)
        self.assertEqual([f for f in os.listdir(self.d) if f.endswith('.tmp')], [])
