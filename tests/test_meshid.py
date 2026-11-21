"""meshid: the one identity of a mesh for the gate and the ledger. Synthetic meshes only: a plate with a square hole."""
import hashlib, math, os, random, re, shutil, struct, sys, tempfile, unittest, zipfile
os.environ.pop('CLAUDE_PROJECT_DIR', None)      # bambu3mf finds a gated root for a file outside it from this: never the session's project
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
    if d not in sys.path: sys.path.insert(0, d)
import bambu3mf, meshid


def plate(hx=10.0, hy=10.0, d=4.0, size=(30.0, 20.0, 5.0)):
    """A closed plate with a square through hole of side d centred at (hx, hy). 16 vertices, 32 triangles."""
    X, Y, Z = size; h = d / 2
    o = [(0, 0), (X, 0), (X, Y), (0, Y)]; i = [(hx - h, hy - h), (hx + h, hy - h), (hx + h, hy + h), (hx - h, hy + h)]
    V = [(float(x), float(y), float(z)) for z in (0, Z) for x, y in o + i]; F = []
    for k in range(4):
        a, b = k, (k + 1) % 4
        F += [(a, 4 + a, 4 + b), (a, 4 + b, b), (8 + a, 12 + b, 12 + a), (8 + a, 8 + b, 12 + b),
              (a, b, 8 + b), (a, 8 + b, 8 + a), (4 + a, 12 + b, 4 + b), (4 + a, 12 + a, 12 + b)]
    return V, F


def write_stl(path, V, F, ascii=False, header=b''):
    if ascii:
        with open(path, 'w') as f:
            f.write('solid demo\n')
            for t in F:
                f.write(' facet normal 0 0 0\n  outer loop\n' + ''.join('   vertex %r %r %r\n' % V[i] for i in t) + '  endloop\n endfacet\n')
            f.write('endsolid demo\n')
    else:
        with open(path, 'wb') as f:
            f.write(header.ljust(80, b'\0') + struct.pack('<I', len(F)))
            for t in F: f.write(struct.pack('<12fH', 0, 0, 0, *[c for i in t for c in V[i]], 0))
    return path


def shuffled(V, F, seed=7):
    """The same mesh with its vertices and triangles in another order (and each triangle rotated)."""
    r = random.Random(seed); order = list(range(len(V))); r.shuffle(order); new = {old: k for k, old in enumerate(order)}
    F2 = [tuple(new[i] for i in (t[1], t[2], t[0])) for t in F]; r.shuffle(F2)
    return [V[i] for i in order], F2


def gh(mesh): return meshid.geom_hash(mesh[0], len(mesh[1]))


def corners(mesh):
    """The triangles of a mesh as coordinates, in file order: what two readers must agree on."""
    return [tuple(mesh[0][i] for i in t) for t in mesh[1]]


class Temp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.d)

    def path(self, name): return os.path.join(self.d, name)


class GeomHash(Temp):
    def test_the_test_mesh_is_a_closed_plate_with_a_hole(self):
        V, F = plate()
        self.assertEqual(bambu3mf.open_edges(F), 0); self.assertAlmostEqual(bambu3mf.volume(V, F), 30 * 20 * 5 - 4 * 4 * 5)

    def test_it_is_40_hex_and_stable(self):
        h = gh(plate()); self.assertRegex(h, r'^[0-9a-f]{40}$'); self.assertEqual(h, gh(plate()))

    def test_a_hole_moved_by_a_twentieth_of_a_millimetre_changes_it(self):
        self.assertNotEqual(gh(plate()), gh(plate(hx=10.05)))
        self.assertNotEqual(gh(plate()), gh(plate(hy=9.95)))

    def test_a_diameter_change_changes_it(self):
        self.assertNotEqual(gh(plate()), gh(plate(d=4.1)))

    def test_a_mirror_changes_it(self):
        V, F = plate()
        mirrored = ([(30.0 - x, y, z) for x, y, z in V], [(a, c, b) for a, b, c in F])          # mirrored in place, hole now at x = 20
        self.assertEqual(bambu3mf.open_edges(mirrored[1]), 0)
        self.assertNotEqual(gh((V, F)), gh(mirrored))
        self.assertNotIn(gh(mirrored), meshid.turned_hashes(V, len(F)))                           # and no turn makes them equal

    def test_the_order_of_triangles_and_vertices_does_not_matter(self):
        self.assertEqual(gh(plate()), gh(shuffled(*plate())))
        a = write_stl(self.path('a.stl'), *plate()); b = write_stl(self.path('b.stl'), *shuffled(*plate(), seed=3))
        self.assertNotEqual(meshid.stl_ids(a)['sha256'], meshid.stl_ids(b)['sha256'])               # other bytes,
        self.assertEqual(meshid.stl_ids(a)['geom_hash'], meshid.stl_ids(b)['geom_hash'])            # one geometry

    def test_a_move_or_a_turn_changes_it_because_it_is_the_hash_of_one_frame(self):
        V, F = plate()
        self.assertNotEqual(gh((V, F)), gh(([(x + 0.001, y, z) for x, y, z in V], F)))
        self.assertNotEqual(gh((V, F)), gh(([bambu3mf.ORIENT['+y'](*v) for v in V], F)))

    def test_noise_under_the_quantum_does_not_change_it(self):
        V, F = plate()
        self.assertEqual(gh((V, F)), gh(([(x + 0.0001, y - 0.0002, z + 0.0003) for x, y, z in V], F)))
        self.assertEqual(meshid.quantize([(0.0004, -0.0004, 1.0)]), [(0, 0, 1000)])
        self.assertEqual(meshid.quantize([(2.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]), [(1000, 0, 0), (2000, 0, 0)])

    def test_the_triangle_count_is_in_geom_hash_and_not_in_vset_hash(self):
        V, F = plate()
        self.assertNotEqual(meshid.geom_hash(V, len(F)), meshid.geom_hash(V, len(F) - 1))
        self.assertEqual(meshid.vset_hash(V), meshid.vset_hash(list(reversed(V))))
        self.assertNotEqual(meshid.vset_hash(V), meshid.geom_hash(V, len(F)))
        self.assertNotEqual(meshid.vset_hash(V), meshid.vset_hash(plate(hx=10.05)[0]))

    def test_another_quantum_is_another_hash(self):
        V, F = plate()
        self.assertNotEqual(meshid.geom_hash(V, len(F)), meshid.geom_hash(V, len(F), quantum=0.01))
        self.assertEqual(meshid.geom_hash(V, len(F), quantum=0.1), meshid.geom_hash(plate(hx=10.01)[0], len(F), quantum=0.1))

    def test_a_quarter_turned_copy_is_found_by_turned_hashes(self):
        V, F = plate(); model = meshid.geom_hash(V, len(F))
        self.assertEqual(len(meshid.QUARTER_TURNS), 24); self.assertEqual(len(set(meshid.turned_hashes(V, len(F)))), 24)
        self.assertEqual(meshid.turned_hashes(V, len(F))[0], model)
        for face in ('+x', '-x', '+y', '-y', '+z', '-z'):
            T = [bambu3mf.ORIENT[face](*v) for v in V]
            with self.subTest(face=face): self.assertIn(model, meshid.turned_hashes(T, len(F)))
        a = math.radians(26.6); free = [(math.cos(a) * x - math.sin(a) * y, math.sin(a) * x + math.cos(a) * y, z) for x, y, z in V]
        self.assertNotIn(model, meshid.turned_hashes(free, len(F)))                                # a free angle is not found


class ShapeId(Temp):
    def test_it_is_the_four_numbers(self):
        s = meshid.shape_id(*plate())
        self.assertEqual(s, {'tris': 32, 'vol': 2920.0, 'area': 1748.0, 'box': [5.0, 20.0, 30.0]})
        self.assertEqual(meshid.shape_id([], []), {'tris': 0, 'vol': 0.0, 'area': 0.0, 'box': [0.0, 0.0, 0.0]})

    def test_equal_on_the_six_bed_faces(self):
        V, F = plate(hx=7.3, hy=12.1, d=3.7, size=(31.7, 22.3, 6.1)); s = meshid.shape_id(V, F)
        for face in ('+x', '-x', '+y', '-y', '+z', '-z', True, False):
            with self.subTest(face=face): self.assertEqual(meshid.shape_id([bambu3mf.ORIENT[face](*v) for v in V], F), s)

    def test_equal_after_a_translation_and_with_another_order(self):
        V, F = plate(hx=7.3, hy=12.1, d=3.7, size=(31.7, 22.3, 6.1)); s = meshid.shape_id(V, F)
        self.assertEqual(meshid.shape_id([(x + 112.5, y - 40.25, z + 3.0) for x, y, z in V], F), s)
        self.assertEqual(meshid.shape_id([(x - 1000.0, y + 1000.0, z) for x, y, z in V], F), s)
        self.assertEqual(meshid.shape_id(*shuffled(V, F)), s)

    def test_it_sees_a_size_change_and_not_a_moved_hole_or_a_mirror(self):
        s = meshid.shape_id(*plate())
        self.assertNotEqual(meshid.shape_id(*plate(d=4.1)), s); self.assertFalse(meshid.same_shape(meshid.shape_id(*plate(d=4.1)), s))
        self.assertEqual(meshid.shape_id(*plate(hx=11.2)), s)            # the limit: only a hint, never "unchanged"
        self.assertNotEqual(gh(plate(hx=11.2)), gh(plate()))             # the hash is what tells them apart
        V, F = plate(); self.assertEqual(meshid.shape_id([(30.0 - x, y, z) for x, y, z in V], [(a, c, b) for a, b, c in F]), s)

    def test_same_shape_takes_a_tolerance_and_can_leave_the_box_out(self):
        V, F = plate(); s = meshid.shape_id(V, F)
        self.assertTrue(meshid.same_shape(s, dict(s, vol=s['vol'] + 0.01)))
        self.assertFalse(meshid.same_shape(s, dict(s, vol=s['vol'] * 1.01))); self.assertFalse(meshid.same_shape(s, dict(s, tris=30)))
        self.assertFalse(meshid.same_shape(s, None))
        a = math.radians(26.6); T = [(math.cos(a) * x - math.sin(a) * y, math.sin(a) * x + math.cos(a) * y, z) for x, y, z in V]
        t = meshid.shape_id(T, F)
        self.assertFalse(meshid.same_shape(s, t)); self.assertTrue(meshid.same_shape(s, t, box=False))


class ReadStl(Temp):
    def test_binary_and_ascii_give_the_same_mesh_and_the_same_ids(self):
        V, F = plate(hx=7.25)
        b = write_stl(self.path('b.stl'), V, F); a = write_stl(self.path('a.stl'), V, F, ascii=True)
        self.assertEqual(meshid.read_stl(b), meshid.read_stl(a)); self.assertEqual(corners(meshid.read_stl(b)), corners((V, F)))
        self.assertEqual(len(meshid.read_stl(b)[0]), 16)                                             # each corner once
        ib, ia = meshid.stl_ids(b), meshid.stl_ids(a)
        self.assertEqual({k: ib[k] for k in ib if k != 'sha256'}, {k: ia[k] for k in ia if k != 'sha256'})
        self.assertEqual(ib['geom_hash'], meshid.geom_hash(V, len(F))); self.assertEqual(ib['triangles'], 32)
        self.assertEqual(sorted(ib), ['geom_hash', 'sha256', 'shape_id', 'triangles', 'vset_hash'])
        with open(b, 'rb') as f: self.assertEqual(ib['sha256'], hashlib.sha256(f.read()).hexdigest())
        self.assertEqual(meshid.mesh_ids(V, F), {k: ib[k] for k in ib if k != 'sha256'})

    def test_it_reads_what_bambu3mf_reads(self):
        p = self.path('p.stl'); bambu3mf.purge_stl(p, 300)
        V, F = meshid.read_stl(p); BV, BF = bambu3mf.read_stl(p)
        self.assertEqual((V, [tuple(t) for t in BF]), (BV, F))

    def test_a_binary_file_whose_header_starts_with_solid_is_still_binary(self):
        V, F = plate(); p = write_stl(self.path('s.stl'), V, F, header=b'solid made by some exporter')
        self.assertEqual(corners(meshid.read_stl(p)), corners((V, F)))

    def test_degenerate_triangles_are_dropped_and_dust_is_zero(self):
        V = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 1e-15, 0.0)]
        p = write_stl(self.path('d.stl'), V, [(0, 1, 2), (0, 1, 1), (3, 1, 2)])
        RV, RF = meshid.read_stl(p)
        self.assertEqual(RV, V[:3]); self.assertEqual(RF, [(0, 1, 2), (0, 1, 2)])                   # the seam vertex is the origin

    def test_what_is_not_an_stl_is_refused(self):
        for name, data in (('t.stl', b'not a mesh at all'), ('h.stl', b'solid x\nendsolid x\n'), ('z.stl', b'\0' * 80 + struct.pack('<I', 5) + b'\0' * 20),
                           ('v.stl', b'solid x\nfacet\nvertex 0 0 0\nvertex 1 0 0\nendsolid x\n'), ('w.stl', b'solid x\nfacet\nvertex a b c\nvertex 1 0 0\nvertex 0 1 0\n')):
            with open(self.path(name), 'wb') as f: f.write(data)
            with self.subTest(file=name), self.assertRaises(ValueError): meshid.read_stl(self.path(name))
        with self.assertRaises(OSError): meshid.read_stl(self.path('missing.stl'))
        with open(self.path('e.stl'), 'wb') as f: f.write(b'\0' * 84)
        self.assertEqual(meshid.read_stl(self.path('e.stl')), ([], []))                               # an empty binary STL is an STL


class ProjectMeshes(Temp):
    """Meshes out of a .3mf, the way bambu3mf.py lays a project out."""
    def setUp(self):
        super().setUp()
        k = bambu3mf.FILAMENT_KEYS; ps = {key: ['0'] * stride for key, stride in k.items()}
        ps.update(filament_settings_id=['Demo PLA'], filament_colour=['#FFFFFF'], flush_volumes_matrix=['0'], filament_self_index=['1'] * k['filament_self_index'],
                  printable_area=['0x0', '180x0', '180x180', '0x180'], different_settings_to_system=['', '', ''])
        import json
        self.proj = self.path('p.3mf')
        with zipfile.ZipFile(self.proj, 'w') as z:
            z.writestr('[Content_Types].xml', '<Types/>'); z.writestr('_rels/.rels', '<Relationships/>'); z.writestr('Metadata/project_settings.config', json.dumps(ps))
        write_stl(self.path('bracket.stl'), *plate(hy=7.0)); write_stl(self.path('cover.stl'), *plate(d=6.0))
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            bambu3mf.build(self.proj, self.d, [('first plate', [('bracket', 'bracket.stl', False, {'_blockers': [((0, 0, 0), (2, 2, 2))]})]),
                                               ('second plate', [('cover', 'cover.stl', '+y'), ('bracket again', 'bracket.stl', '+z')])])

    def test_one_entry_per_object_with_its_plate_and_no_blocker(self):
        ms = meshid.read_project(self.proj)
        self.assertEqual([(m['object'], m['plate'], m['plate_index'], m['printable'], m['parts']) for m in ms],
                         [('bracket', 'first plate', 1, True, 1), ('cover', 'second plate', 2, True, 1), ('bracket again', 'second plate', 2, True, 1)])
        self.assertEqual([len(m['triangles']) for m in ms], [32, 32, 32])                            # 12 blocker triangles are not in it
        self.assertEqual(len(ms[0]['vertices']), 16)

    def test_the_pose_is_the_stored_mesh_centred_and_turned_not_the_model(self):
        ms = meshid.read_project(self.proj); V, F = plate(hy=7.0)
        self.assertNotEqual(meshid.geom_hash(ms[0]['vertices'], 32), meshid.geom_hash(V, 32))        # centred on the origin
        self.assertEqual(meshid.geom_hash(ms[0]['vertices'], 32), meshid.geom_hash([(x - 15.0, y - 10.0, z - 2.5) for x, y, z in V], 32))
        self.assertNotEqual(meshid.geom_hash(ms[0]['vertices'], 32), meshid.geom_hash(ms[2]['vertices'], 32))      # the same STL on another face
        self.assertTrue(meshid.same_shape(meshid.shape_id(ms[0]['vertices'], ms[0]['triangles']), meshid.shape_id(V, F)))
        self.assertEqual(meshid.pose_hashes(ms)['cover'], meshid.geom_hash(ms[1]['vertices'], 32))

    def test_the_archive_in_memory_and_the_file_on_disk_give_the_same_hashes(self):
        _, files = bambu3mf.load(self.proj)
        self.assertEqual(meshid.pose_hashes(meshid.project_meshes(files)), meshid.pose_hashes(meshid.read_project(self.proj)))

    def test_an_object_on_no_plate_and_one_that_is_not_printable(self):
        infos, files = bambu3mf.load(self.proj); cfg = files['Metadata/model_settings.config'].decode()
        oid = [o for o, _, n in bambu3mf.objects(cfg) if n == 'cover'][0]
        cfg = re.sub(r'    <model_instance>\s*<metadata key="object_id" value="%s"/>.*?</model_instance>\n' % oid, '', cfg, flags=re.S)
        files['Metadata/model_settings.config'] = cfg.encode()
        model = files['3D/3dmodel.model'].decode(); last = max(re.findall(r'<item objectid="(\d+)"', model), key=int)
        files['3D/3dmodel.model'] = re.sub(r'(<item objectid="%s"[^>]*) printable="1"' % last, r'\1 printable="0"', model).encode()
        by = {m['object']: m for m in meshid.project_meshes(files)}
        self.assertEqual((by['cover']['plate'], by['cover']['plate_index']), (None, None))
        self.assertFalse(by['bracket again']['printable']); self.assertTrue(by['bracket']['printable'])

    def test_a_component_transform_moves_its_part(self):
        infos, files = bambu3mf.load(self.proj); model = files['3D/3dmodel.model'].decode()
        moved = model.replace('transform="1 0 0 0 1 0 0 0 1 0 0 0"', 'transform="1 0 0 0 1 0 0 0 1 5 0 0"', 1)
        self.assertNotEqual(moved, model); files['3D/3dmodel.model'] = moved.encode()
        a, b = meshid.read_project(self.proj)[0], meshid.project_meshes(files)[0]
        self.assertEqual(b['vertices'], [(x + 5.0, y, z) for x, y, z in a['vertices']])

    def test_rigid_copy_is_a_proof_not_a_hint(self):
        def turn(v, a=0.7, b=1.1, shift=(5.0, -3.0, 2.0)):
            x, y, z = v
            y, z = math.cos(a) * y - math.sin(a) * z, math.sin(a) * y + math.cos(a) * z
            x, y = math.cos(b) * x - math.sin(b) * y, math.sin(b) * x + math.cos(b) * y
            return (x + shift[0], y + shift[1], z + shift[2])
        def bracket(hx, lug=4.0):                                            # a plate with a round hole and a lug on one corner: no symmetry
            V = [(x, y, z) for z in (0.0, 5.0) for x, y in ((0, 0), (30, 0), (30, 20), (0, 20))]
            V += [(hx + 2 * math.cos(2 * math.pi * k / 32), 7 + 2 * math.sin(2 * math.pi * k / 32), z) for z in (0.0, 5.0) for k in range(32)]
            return V + [(0.0, 0.0, 5.0 + lug), (3.0, 0.0, 5.0 + lug), (0.0, 3.0, 5.0 + lug)]
        A = bracket(10.0)
        self.assertTrue(meshid.rigid_copy(A, [turn(v) for v in A]))
        self.assertTrue(meshid.rigid_copy(A + A[:5], [turn(v) for v in reversed(A)]))          # order and repeated vertices do not matter
        self.assertFalse(meshid.rigid_copy(A, [turn(v) for v in bracket(12.0)]))                # the hole moved by 2 mm
        self.assertFalse(meshid.rigid_copy(A, [turn(v) for v in bracket(10.0, lug=4.5)]))
        self.assertFalse(meshid.rigid_copy(A, [turn((-x, y, z)) for x, y, z in A]))             # a mirror image is another part
        self.assertFalse(meshid.rigid_copy(A, [turn((1.01 * x, 1.01 * y, 1.01 * z)) for x, y, z in A]))
        self.assertFalse(meshid.rigid_copy(A, A[:-1])); self.assertFalse(meshid.rigid_copy([], []))
        cube = [(x, y, z) for x in (0.0, 10.0) for y in (0.0, 10.0) for z in (0.0, 10.0)]       # every symmetry of the cloud is a copy
        self.assertTrue(meshid.rigid_copy(cube, [turn(v, a=math.pi / 2, b=0.0) for v in cube]))
        ring = [(20 * math.cos(2 * math.pi * k / 256), 20 * math.sin(2 * math.pi * k / 256), z) for k in range(256) for z in (0.0, 30.0)]
        self.assertTrue(meshid.rigid_copy(ring, [turn(v) for v in ring]))
        self.assertFalse(meshid.rigid_copy(ring, ring[:-1] + [(19.0, 0.5, 30.0)]))

    def test_xml_mesh_and_files_that_are_not_projects(self):
        V, F = meshid.xml_mesh('<mesh><vertices><vertex x="1" y="-2.5e-05" z="3e+02"/></vertices><triangles><triangle v1="0" v2="0" v3="0"/></triangles></mesh>')
        self.assertEqual((V, F), ([(1.0, -2.5e-05, 300.0)], [(0, 0, 0)]))
        self.assertEqual(meshid.project_meshes({}), []); self.assertEqual(meshid.project_meshes({'3D/3dmodel.model': b'<model/>'}), [])
        with self.assertRaises(Exception): meshid.read_project(self.path('bracket.stl'))
        plain = {'3D/3dmodel.model': b'<model><resources><object id="1" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>'
                                     b'<vertex x="0" y="1" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object></resources>'
                                     b'<build><item objectid="1"/></build></model>'}
        self.assertEqual([(m['object'], m['plate'], len(m['triangles'])) for m in meshid.project_meshes(plain)], [('object 1', None, 1)])


if __name__ == '__main__':
    unittest.main()
