"""Add one plate with one or more objects to an existing Bambu project, keeping every other plate as it is (positions, per-object
settings, pauses, thumbnails). Stdlib only; run it with Bambu Studio closed (with_bambu_closed.py).
   python3 bambulab/tools/add_plate.py <project.3mf> "<plate name>" <stl-dir> "<object name>=<file.stl>[=<orient>]" … [--set key=value …] [--extruder N]
orient as in bambu3mf.build ('' = as modelled, '+z' = flipped about X, '+x' …). --set are per-object settings for the new objects;
--extruder is the filament slot (default: the slot of the project's first object). The new plate takes the next grid cell."""
import sys, os, re, json, math, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf

def add_plate(proj, pname, stl_dir, objs, settings=None, extruder=None):
    infos, files = bambu3mf.load(proj); order = [i.filename for i in infos]
    ms = files['Metadata/model_settings.config'].decode(); m3 = files['3D/3dmodel.model'].decode(); rels = files['3D/_rels/3dmodel.model.rels'].decode()
    ps = json.loads(files['Metadata/project_settings.config'])
    bed = float(ps['printable_area'][2].split('x')[0]); stride = 1.2 * bed
    pids = [int(i) for i in re.findall(r'plater_id" value="(\d+)"', ms)]; pid = max(pids) + 1; n = len(pids) + 1
    cols = math.ceil(math.sqrt(n) - 1e-9); ox, oy = (pid - 1) % cols * stride, -((pid - 1) // cols) * stride
    ids = [int(i) for i in re.findall(r'<object id="(\d+)"', m3)] + [int(i) for i in re.findall(r'<object id="(\d+)"', ms)]
    nid = max(ids) + 1
    paths = [int(x) for x in re.findall(r'object_(\d+)\.model', m3)]; npath = max(paths) + 1
    idents = [int(i) for i in re.findall(r'identify_id" value="(\d+)"', ms)]; ident = max(idents + [100]) + 1
    if extruder is None: extruder = int(re.search(r'key="extruder" value="(\d+)"', ms).group(1))
    pname = bambu3mf.bambu_name(pname); settings = settings or {}
    meshes = [bambu3mf.prep_object((bambu3mf.bambu_name(o[0]),) + tuple(o[1:]), stl_dir) for o in objs]
    pos = bambu3mf.plate_positions([m[2] for m in meshes], {}, bed)
    res, items, objcfg, inst, asm = [], [], [], [], []
    for k, (o, (V, F, size, bl)) in enumerate(zip(objs, meshes)):
        name = bambu3mf.bambu_name(o[0]); mesh_id, obj_id = nid, nid + 1; nid += 2; path = '3D/Objects/object_%d.model' % npath; npath += 1
        tr = '1 0 0 0 1 0 0 0 1 %.6f %.6f %.6f' % (ox + pos[k][0], oy + pos[k][1], size[2] / 2)
        files[path] = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" %s>\n <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n  <object id="%d" p:UUID="%s" type="model">\n   <mesh>%s</mesh>\n  </object>\n </resources>\n <build/>\n</model>\n'
                       % (bambu3mf.NS, mesh_id, uuid.uuid4(), bambu3mf.mesh_xml(V, F))).encode()
        rels = rels.replace('</Relationships>', ' <Relationship Target="/%s" Id="rel-%d" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n</Relationships>' % (path, npath))
        res.append('  <object id="%d" p:UUID="%s" type="model">\n   <components>\n    <component p:path="/%s" objectid="%d" p:UUID="%s" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n   </components>\n  </object>\n' % (obj_id, uuid.uuid4(), path, mesh_id, uuid.uuid4()))
        items.append('  <item objectid="%d" p:UUID="%s" transform="%s" printable="1"/>\n' % (obj_id, uuid.uuid4(), tr))
        opts = ''.join('    <metadata key="%s" value="%s"/>\n' % kv for kv in settings.items())
        objcfg.append('  <object id="%d">\n    <metadata key="name" value="%s"/>\n' % (obj_id, name) + opts +
                      '    <metadata key="extruder" value="%d"/>\n    <metadata face_count="%d"/>\n    <part id="%d" subtype="normal_part">\n      <metadata key="name" value="%s"/>\n      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n      <mesh_stat face_count="%d" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n    </part>\n  </object>\n' % (extruder, len(F), mesh_id, name, len(F)))
        inst.append('    <model_instance>\n      <metadata key="object_id" value="%d"/>\n      <metadata key="instance_id" value="0"/>\n      <metadata key="identify_id" value="%d"/>\n    </model_instance>\n' % (obj_id, ident)); ident += 1
        asm.append('   <assemble_item object_id="%d" instance_id="0" transform="%s" offset="0 0 0" />\n' % (obj_id, tr))
        print('%-24s %-40s %.1f × %.1f × %.1f' % (pname, name, *size))
    plate = '  <plate>\n    <metadata key="plater_id" value="%d"/>\n    <metadata key="plater_name" value="%s"/>\n    <metadata key="locked" value="false"/>\n    <metadata key="filament_map_mode" value="Auto For Flush"/>\n%s  </plate>\n' % (pid, pname, ''.join(inst))
    m3 = m3.replace(' </resources>', ''.join(res) + ' </resources>').replace(' </build>', ''.join(items) + ' </build>')
    first = ms.index('  <plate>'); last = ms.rindex('</plate>\n') + len('</plate>\n')
    ms = ms[:first] + ''.join(objcfg) + ms[first:last] + plate + ms[last:]       # new objects before the plates, new plate last
    ms = ms.replace('  </assemble>', ''.join(asm) + '  </assemble>')
    files['Metadata/model_settings.config'] = ms.encode(); files['3D/3dmodel.model'] = m3.encode(); files['3D/_rels/3dmodel.model.rels'] = rels.encode()
    if 'Metadata/filament_sequence.json' in files:
        fs = json.loads(files['Metadata/filament_sequence.json']); fs['plate_%d' % pid] = {'nozzle_sequence': [], 'optimal_assignment': [], 'sequence': []}
        files['Metadata/filament_sequence.json'] = json.dumps(fs).encode()
    if 'Metadata/cut_information.xml' in files:    # one entry per object, in order: keep the count right
        ci = files['Metadata/cut_information.xml'].decode(); cnt = len(re.findall(r'<object id=', ci))
        ci = ci.replace('</objects>', ''.join(' <object id="%d">\n  <cut_id id="0" check_sum="1" connectors_cnt="0"/>\n </object>\n' % (cnt + 1 + j) for j in range(len(objs))) + '</objects>')
        files['Metadata/cut_information.xml'] = ci.encode()
    bambu3mf.save(proj, files, [k for k in order if k in files] + sorted(k for k in files if k not in order))
    print('wrote', proj, 'plate', pid, pname)

if __name__ == '__main__':
    a = sys.argv[1:]; sets = {}; ext = None
    while '--set' in a: i = a.index('--set'); k, v = a[i + 1].split('=', 1); sets[k] = v; del a[i:i + 2]
    if '--extruder' in a: i = a.index('--extruder'); ext = int(a[i + 1]); del a[i:i + 2]
    proj, pname, stl_dir, *specs = a
    objs = []
    for s in specs:
        p = s.split('='); objs.append((p[0], p[1], p[2] if len(p) > 2 else False))
    add_plate(proj, pname, stl_dir, objs, sets, ext)
