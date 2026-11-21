"""A stand-in for the Bambu Studio CLI in the tests: `fake_slicer.py --slice N --outputdir DIR project.3mf` writes what the
real one writes there, result.json and plate_<n>.gcode, for every plate that holds a printed object (N = 0) or for plate
N. The G-code holds what probes.project_slice reads: the header with the labels and heights of the objects, layers with
"; Z_HEIGHT:", "; OBJECT_ID:" before each object and "; FEATURE: Support" moves.

How much support each object gets is read from the environment: FAKE_SUPPORT='{"object name": [mm of filament, top z]}'
(an object not named gets none); a third item [[x0, y0, x1, y1], ...] puts the support lines in those boxes (mm from the
low corner of the object), else they fill the object's whole footprint. Each object's walls are a rectangle round its
footprint, its low corner at (10, 10) plus 60 mm per object along x. FAKE_SLICE_FAIL=1 makes it fail the way the real one does on a conflict.
make(path) writes an executable wrapper of this file to `path` (BAMBU_STUDIO can point at it)."""
import json, os, stat, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools', 'bambu'))


def make(path):
    with open(path, 'w') as f:
        f.write('#!%s\nimport runpy, sys\nsys.path.insert(0, %r)\nrunpy.run_path(%r, run_name="__main__")\n' % (sys.executable, HERE, os.path.abspath(__file__)))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def gcode(objects, layer=0.2, support=None):
    """The text of one plate. objects: [(label, name, height)] or [(label, name, height, (size x, size y))].
    support: {name: (e, top_z)} or {name: (e, top_z, [[x0, y0, x1, y1], ...])}."""
    support = support or {}
    objects = [tuple(o) if len(o) == 4 else tuple(o) + ((10.0, 10.0),) for o in objects]
    lines = ['; HEADER_BLOCK_START', '; BambuStudio 01.00.00.00']
    if len(objects) > 1:
        lines.append('; model label id: %s' % ','.join(str(o[0]) for o in objects))
        lines.append('; object max height: %s' % ','.join('%.2f' % o[2] for o in objects))
    lines += ['; HEADER_BLOCK_END', '; CONFIG_BLOCK_START', '; layer_height = %s' % layer, '; CONFIG_BLOCK_END']
    top = max(o[2] for o in objects)
    n = max(1, int(round(top / layer)))
    for k in range(1, n + 1):
        z = round(k * layer, 4)
        lines += ['; CHANGE_LAYER', '; Z_HEIGHT: %s' % z]
        for j, (label, name, h, size) in enumerate(objects):
            if z > h + 1e-9:
                continue
            ox, oy, sx, sy = 10.0 + 60.0 * j, 10.0, float(size[0]), float(size[1])
            lines += ['; OBJECT_ID: %d' % label, '; FEATURE: Outer wall', 'G1 X%.3f Y%.3f F3000' % (ox, oy),
                      'G1 X%.3f Y%.3f E.5' % (ox + sx, oy), 'G1 X%.3f Y%.3f E.5' % (ox + sx, oy + sy),
                      'G1 X%.3f Y%.3f E.5' % (ox, oy + sy), 'G1 X%.3f Y%.3f E.5' % (ox, oy)]
            got = support.get(name, (0.0, None))
            e, tz = got[0], got[1]
            boxes = got[2] if len(got) > 2 and got[2] else [[0.0, 0.0, sx, sy]]
            if e and tz is not None and z <= tz + 1e-9:
                runs = [(b, y) for b in boxes for y in [b[1] + 0.5 * i for i in range(max(1, int((b[3] - b[1]) / 0.5) + 1))]]
                per = e / max(1, int(round(tz / layer))) / len(runs)
                lines.append('; FEATURE: Support')
                for b, y in runs:
                    lines += ['G1 X%.3f Y%.3f F3000' % (ox + b[0], oy + y), 'G1 X%.3f Y%.3f E%.6f' % (ox + b[2], oy + y, per)]
                lines.append('G1 E-.8')
    return '\n'.join(lines) + '\n'


def main(argv):
    plate, out, proj = int(argv[argv.index('--slice') + 1]), argv[argv.index('--outputdir') + 1], argv[-1]
    if os.environ.get('FAKE_SLICE_FAIL'):
        with open(os.path.join(out, 'result.json'), 'w') as f:
            json.dump({'error_string': 'Some objects are too close to the clumping detection area', 'return_code': -100, 'sliced_plates': []}, f)
        sys.stdout.write('object conflict: "demo" is too close to the clumping detection area\n')
        return 1
    import meshid
    support = {k: tuple(v) for k, v in json.loads(os.environ.get('FAKE_SUPPORT') or '{}').items()}
    plates, label = {}, 100
    for m in meshid.read_project(proj):
        if m.get('plate_index') is None or not m.get('printable', True):
            continue
        z = [v[2] for v in m['vertices']]
        label += 11
        size = tuple(round(max(v[i] for v in m['vertices']) - min(v[i] for v in m['vertices']), 3) for i in (0, 1))
        plates.setdefault(m['plate_index'], []).append((label, m['object'], round(max(z) - min(z), 3), size))
    want = sorted(plates) if plate == 0 else [plate]
    res = {'error_string': 'Success.', 'layer_height': 0.2, 'plate_index': plate, 'return_code': 0, 'sliced_plates': []}
    for p in want:
        objs = plates.get(p) or []
        if not objs:
            continue
        res['sliced_plates'].append({'id': p, 'objects': [{'id': o[0], 'name': o[1], 'bbox': {'height': o[2]}} for o in objs]})
        with open(os.path.join(out, 'plate_%d.gcode' % p), 'w') as f:
            f.write(gcode(objs, 0.2, support))
    with open(os.path.join(out, 'result.json'), 'w') as f:
        json.dump(res, f)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
