"""Add one plate with one or more objects to an existing Bambu project, keeping every other plate as it is (settings, pauses,
thumbnails). Stdlib only; run it with Bambu Studio closed (with_bambu_closed.py).
   python3 bambulab/tools/add_plate.py <project.3mf> "<plate name>" <stl-dir> "<object name>=<file.stl>[=<orient>]" …
          [--set key=value …] [--extruder N] [--cols N] [--at "<object name>=x,y" …] [--pause z1,z2 …]
orient as in bambu3mf.build ('' = as modelled, '+z' = flipped about X, '+x' …). --set are per-object settings for the new objects;
--extruder is the filament slot (default: the slot of the project's first object).
Layout: rows of --cols objects (default ceil(sqrt(n))), centred on the plate; --at puts an object's bounding-box centre at x, y
in plate coordinates (mm from the bed's front-left corner). The plate is refused, and nothing is written, when an object leaves
the bed, overlaps another, or covers a keep-out area of the printer (bed_exclude_area, and wrapping_exclude_area while wrapping
detection is on: the P2S clumping-detection strip). Bounding boxes are compared, so round parts get a conservative check.
Plates sit on a grid of ceil(sqrt(plate count)) columns, so adding a plate can change the cell of earlier plates: every plate's
objects are moved to the cell Bambu gives it for the new count (regrid). Without that, adding plates one at a time left objects
of earlier plates outside their plate.
--pause adds a print pause (the printer's machine_pause_gcode) at each height top_z, in mm above the bed, a multiple of the layer
height. Bambu pauses before printing the layer whose top is top_z, so the part then stands top_z minus one layer high. Use the
top of the pocket that takes the insert: the insert must not stand above top_z, and the layers from there on close over it.
Check it in the sliced G-code: slice_check.py prints every pause with the height printed at that moment."""
import sys, os, re, json, math, uuid
from xml.sax.saxutils import quoteattr
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf

def keep_out(ps):
    """Rectangles (x0, y0, x1, y1) of the bed that no part may cover: bed_exclude_area, and wrapping_exclude_area while
    enable_wrapping_detection is on (Bambu refuses to slice: "<object> is too close to clumping detection area")."""
    keys = ['bed_exclude_area'] + (['wrapping_exclude_area'] if str(ps.get('enable_wrapping_detection', '0')) == '1' else [])
    out = []
    for k in keys:
        v = ps.get(k) or []
        pts = [tuple(float(c) for c in p.split('x')) for p in ([v] if isinstance(v, str) else v) if 'x' in p]
        if len(pts) >= 3:
            xs, ys = zip(*pts); out.append((min(xs), min(ys), max(xs), max(ys)))
    return out

def layout_problems(names, centres, sizes, bed, keep=(), margin=3.0, gap=2.0):
    """What is wrong with a layout: objects off the bed (closer than margin to its edge), covering a keep-out rectangle, or
    closer than gap to each other. Bounding boxes in plate coordinates."""
    box = lambda c, s, g=0.0: (c[0] - s[0] / 2 - g, c[1] - s[1] / 2 - g, c[0] + s[0] / 2 + g, c[1] + s[1] / 2 + g)
    hit = lambda a, b: a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
    bad = []
    for i, (n, c, s) in enumerate(zip(names, centres, sizes)):
        r = box(c, s)
        if r[0] < margin or r[1] < margin or r[2] > bed - margin or r[3] > bed - margin:
            bad.append('%s leaves the bed: x %.1f..%.1f, y %.1f..%.1f on a %g mm bed' % (n, r[0], r[2], r[1], r[3], bed))
        for k in keep:
            if hit(r, k): bad.append('%s covers the keep-out area x %g..%g, y %g..%g' % (n, k[0], k[2], k[1], k[3]))
        for j in range(i):
            if hit(box(c, s, gap / 2), box(centres[j], sizes[j], gap / 2)): bad.append('%s is closer than %g mm to %s' % (n, gap, names[j]))
    return bad

def regrid(ms, m3, bed):
    """Move each plate's objects to the grid cell of its index for the current plate count (ceil(sqrt(n)) columns, a 20 %
    gap, rows going -Y). Returns (ms, m3, plates moved)."""
    stride = 1.2 * bed
    plates = re.findall(r'<plate>.*?</plate>', ms, re.S); cols = math.ceil(math.sqrt(len(plates)) - 1e-9)
    tr = {int(o): t.split() for o, t in re.findall(r'<item objectid="(\d+)"[^>]*transform="([^"]+)"', m3)}
    shift, moved = {}, 0
    for i, p in enumerate(plates):
        oids = [int(o) for o in re.findall(r'object_id" value="(\d+)"', p) if int(o) in tr]
        if not oids: continue
        cx = sum(float(tr[o][9]) for o in oids) / len(oids); cy = sum(float(tr[o][10]) for o in oids) / len(oids)
        col, row = math.floor(cx / stride), math.ceil(-cy / stride - 1e-9)
        dx, dy = (i % cols - col) * stride, -(i // cols - row) * stride
        if dx or dy:
            moved += 1
            for o in oids: shift[o] = (dx, dy)
    def move(m):
        o = int(m.group(2))
        if o not in shift: return m.group(0)
        t = m.group(4).split(); t[9] = '%.6f' % (float(t[9]) + shift[o][0]); t[10] = '%.6f' % (float(t[10]) + shift[o][1])
        return m.group(1) + m.group(2) + m.group(3) + ' '.join(t) + m.group(5)
    m3 = re.sub(r'(<item objectid=")(\d+)("[^>]*transform=")([^"]+)(")', move, m3)
    ms = re.sub(r'(<assemble_item object_id=")(\d+)("[^>]*transform=")([^"]+)(")', move, ms)
    return ms, m3, moved

def add_pauses(files, pid, heights, extruder, ps):
    """A pause before the layer at each height (mm above the bed) on plate pid, in Metadata/custom_gcode_per_layer.xml."""
    p = 'Metadata/custom_gcode_per_layer.xml'
    cg = files[p].decode() if p in files else '<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n</custom_gcodes_per_layer>\n'
    if 'plate_info id="%d"' % pid in cg: raise SystemExit('plate %d already has custom G-code' % pid)
    g = ps.get('machine_pause_gcode') or 'M400 U1'; g = g[0] if isinstance(g, list) else g
    lines = ''.join('<layer top_z="%s" type="1" extruder="%d" color="" extra="" gcode=%s/>\n' % (('%.3f' % z).rstrip('0').rstrip('.'), extruder, quoteattr(g)) for z in sorted(heights))
    files[p] = cg.replace('</custom_gcodes_per_layer>', '<plate>\n<plate_info id="%d"/>\n%s<mode value="MultiAsSingle"/>\n</plate>\n</custom_gcodes_per_layer>' % (pid, lines)).encode()

def add_plate(proj, pname, stl_dir, objs, settings=None, extruder=None, cols=None, at=None, pauses=None):
    infos, files = bambu3mf.load(proj); order = [i.filename for i in infos]
    ms = files['Metadata/model_settings.config'].decode(); m3 = files['3D/3dmodel.model'].decode(); rels = files['3D/_rels/3dmodel.model.rels'].decode()
    ps = json.loads(files['Metadata/project_settings.config'])
    bed = float(ps['printable_area'][2].split('x')[0]); stride = 1.2 * bed
    pids = [int(i) for i in re.findall(r'plater_id" value="(\d+)"', ms)]; pid = max(pids) + 1; n = len(pids) + 1
    gcols = math.ceil(math.sqrt(n) - 1e-9); ox, oy = (n - 1) % gcols * stride, -((n - 1) // gcols) * stride
    ids = [int(i) for i in re.findall(r'<object id="(\d+)"', m3)] + [int(i) for i in re.findall(r'<object id="(\d+)"', ms)]
    nid = max(ids) + 1
    paths = [int(x) for x in re.findall(r'object_(\d+)\.model', m3)]; npath = max(paths) + 1
    idents = [int(i) for i in re.findall(r'identify_id" value="(\d+)"', ms)]; ident = max(idents + [100]) + 1
    if extruder is None: extruder = int(re.search(r'key="extruder" value="(\d+)"', ms).group(1))
    pname = bambu3mf.bambu_name(pname); settings = settings or {}
    meshes = [bambu3mf.prep_object((bambu3mf.bambu_name(o[0]),) + tuple(o[1:]), stl_dir) for o in objs]
    names = [bambu3mf.bambu_name(o[0]) for o in objs]; sizes = [m[2] for m in meshes]
    pos = bambu3mf.plate_positions(sizes, {'cols': cols or math.ceil(math.sqrt(len(objs)) - 1e-9), 'gap': 6}, bed)
    for k, nm in enumerate(names):
        if at and nm in at: pos[k] = at[nm]
    bad = layout_problems(names, [pos[k] for k in range(len(objs))], sizes, bed, keep_out(ps))
    if bad: raise SystemExit('plate "%s" not added:\n  %s\nUse --cols, or --at "<object>=x,y" for the objects named.' % (pname, '\n  '.join(bad)))
    if pauses:
        lh, top = float(ps.get('layer_height', 0.2)), max(s[2] for s in sizes)
        for z in pauses:
            if not 0 < z < top: raise SystemExit('pause at %g mm is outside the parts (0..%g mm)' % (z, top))
            if abs(z / lh - round(z / lh)) > 1e-3: print('WARNING: pause at %g mm is not a multiple of the %g mm layer height' % (z, lh))
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
    ms, m3, moved = regrid(ms, m3, bed)
    if moved: print('regrid: objects of %d earlier plate(s) moved to their cell for %d plates' % (moved, n))
    if pauses: add_pauses(files, pid, pauses, extruder, ps)
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
    a = sys.argv[1:]; sets = {}; ext = cols = None; at = {}; pauses = []
    while '--set' in a: i = a.index('--set'); k, v = a[i + 1].split('=', 1); sets[k] = v; del a[i:i + 2]
    while '--at' in a: i = a.index('--at'); k, v = a[i + 1].rsplit('=', 1); at[bambu3mf.bambu_name(k)] = tuple(float(c) for c in v.split(',')); del a[i:i + 2]
    while '--pause' in a: i = a.index('--pause'); pauses += [float(z) for z in a[i + 1].split(',')]; del a[i:i + 2]
    if '--extruder' in a: i = a.index('--extruder'); ext = int(a[i + 1]); del a[i:i + 2]
    if '--cols' in a: i = a.index('--cols'); cols = int(a[i + 1]); del a[i:i + 2]
    proj, pname, stl_dir, *specs = a
    objs = []
    for s in specs:
        p = s.split('='); objs.append((p[0], p[1], p[2] if len(p) > 2 else False))
    add_plate(proj, pname, stl_dir, objs, sets, ext, cols, at, pauses)
