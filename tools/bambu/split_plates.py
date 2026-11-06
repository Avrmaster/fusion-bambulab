"""Move some plates of a Bambu project into a separate project file (stdlib only; run it with Bambu Studio closed).
   python3 bambulab/tools/split_plates.py <project.3mf> <new.3mf> <plate id> [<plate id> …]
The moved plates leave <project.3mf> and become the plates of <new.3mf>, in the same order. Both files keep the project
settings; objects, positions, per-object settings, layer pauses and thumbnails go with their plate; plates are renumbered."""
import sys, os, re, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf

def keep(files, ids):
    """files (name → bytes) reduced to the plates whose plater_id is in ids (in that order), renumbered 1…n."""
    f = dict(files); ms = f['Metadata/model_settings.config'].decode(); m3 = f['3D/3dmodel.model'].decode()
    plates = re.findall(r'  <plate>.*?</plate>\n', ms, re.S)
    pid = lambda p: int(re.search(r'plater_id" value="(\d+)"', p).group(1))
    new = {old: i + 1 for i, old in enumerate(ids)}
    kept = [p for old in ids for p in plates if pid(p) == old]
    objs = {o for p in kept for o in re.findall(r'object_id" value="(\d+)"', p)}
    def renum(p):
        old = pid(p); p = re.sub(r'plater_id" value="\d+"', 'plater_id" value="%d"' % new[old], p)
        return re.sub(r'(Metadata/(?:plate|plate_no_light|top|pick))_%d\b' % old, r'\1_%d' % new[old], p)
    ms = re.sub(r'  <object id="(\d+)">.*?\n  </object>\n', lambda m: m.group(0) if m.group(1) in objs else '', ms, flags=re.S)
    ms = re.sub(r'\s*<assemble_item object_id="(\d+)"[^>]*/>', lambda m: m.group(0) if m.group(1) in objs else '', ms)
    # Bambu lays plates out on a grid (ceil(sqrt(n)) columns, 20 % gap): move each part with its plate's new grid cell
    stride = 1.2 * float(json.loads(f['Metadata/project_settings.config'])['printable_area'][2].split('x')[0])
    cell = lambda i, n: ((i - 1) % math.ceil(math.sqrt(n) - 1e-9) * stride, -((i - 1) // math.ceil(math.sqrt(n) - 1e-9)) * stride)
    shift = {}
    for p in kept:
        (ax, ay), (bx, by) = cell(pid(p), len(plates)), cell(new[pid(p)], len(ids))
        for o in re.findall(r'object_id" value="(\d+)"', p): shift[o] = (bx - ax, by - ay)
    def move(m):
        o = m.group(1); v = m.group(3).split(); dx, dy = shift.get(o, (0, 0))
        v[9] = '%.6f' % (float(v[9]) + dx); v[10] = '%.6f' % (float(v[10]) + dy)
        return m.group(0).replace(m.group(3), ' '.join(v))
    ms = re.sub(r'<assemble_item object_id="(\d+)"([^>]*?)transform="([^"]*)"', move, ms)
    m3 = re.sub(r'<item objectid="(\d+)"([^>]*?)transform="([^"]*)"', move, m3)
    first = ms.index('  <plate>'); last = ms.rindex('</plate>\n') + len('</plate>\n')
    ms = ms[:first] + ''.join(renum(p) for p in kept) + ms[last:]
    f['Metadata/model_settings.config'] = ms.encode()
    # 3D model: build items and the component objects of the removed parts, with their mesh files
    gone = [m.group(1) for m in re.finditer(r'<item objectid="(\d+)"', m3) if m.group(1) not in objs]
    paths_gone = set()
    for o in gone:
        blk = re.search(r'<object id="%s"[^>]*>.*?</object>\s*' % o, m3, re.S)
        paths_gone |= set(re.findall(r'p:path="([^"]+)"', blk.group(0))); m3 = m3.replace(blk.group(0), '')
        m3 = re.sub(r'\s*<item objectid="%s"[^>]*/>' % o, '', m3)
    paths_gone -= set(re.findall(r'p:path="([^"]+)"', m3))
    f['3D/3dmodel.model'] = m3.encode()
    rels = f['3D/_rels/3dmodel.model.rels'].decode()
    for p in paths_gone: rels = re.sub(r'\s*<Relationship Target="%s"[^>]*/>' % re.escape(p), '', rels); f.pop(p.lstrip('/'), None)
    f['3D/_rels/3dmodel.model.rels'] = rels.encode()
    # per-plate files: thumbnails, plate json, filament sequence, layer pauses
    out = {}
    for k, v in f.items():
        m = re.match(r'(Metadata/(?:plate|plate_no_light|top|pick))_(\d+)((?:_small)?\.(?:png|json))$', k)
        if m:
            if int(m.group(2)) in new: out['%s_%d%s' % (m.group(1), new[int(m.group(2))], m.group(3))] = v
        else: out[k] = v
    if 'Metadata/filament_sequence.json' in out:
        fs = json.loads(out['Metadata/filament_sequence.json'])
        out['Metadata/filament_sequence.json'] = json.dumps({'plate_%d' % new[int(k[6:])]: v for k, v in fs.items() if int(k[6:]) in new}).encode()
    if 'Metadata/custom_gcode_per_layer.xml' in out:
        cg = out['Metadata/custom_gcode_per_layer.xml'].decode()
        blocks = re.findall(r'<plate>.*?</plate>\n?', cg, re.S); keepb = []
        for b in blocks:
            i = int(re.search(r'plate_info id="(\d+)"', b).group(1))
            if i in new: keepb.append((new[i], re.sub(r'plate_info id="\d+"', 'plate_info id="%d"' % new[i], b)))
        head = cg[:cg.index('<plate>')] if blocks else cg; tail = cg[cg.rindex('</plate>') + 8:] if blocks else ''
        out['Metadata/custom_gcode_per_layer.xml'] = (head + ''.join(b for _, b in sorted(keepb)) + tail.lstrip('\n')).encode()
    ci = out.get('Metadata/cut_information.xml', b'').decode()
    if ci and not re.search(r'cut_id id="(?!0")', ci): out.pop('Metadata/cut_information.xml')   # all uncut: indexes would shift
    return out

if __name__ == '__main__':
    src, dst, *ids = sys.argv[1:]; ids = [int(i) for i in ids]
    infos, files = bambu3mf.load(src); order = [i.filename for i in infos]
    all_ids = [int(i) for i in re.findall(r'plater_id" value="(\d+)"', files['Metadata/model_settings.config'].decode())]
    rest = [i for i in all_ids if i not in ids]
    for path, part in ((dst, ids), (src, rest)):
        out = keep(files, part); bambu3mf.save(path, out, [k for k in order if k in out] + sorted(k for k in out if k not in order))
        print(path, len(part), 'plates')
