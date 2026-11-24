#!/usr/bin/env python3
"""A picture of one part with the place in question marked: for every question to the engineer about a part.

A project has hundreds of parts; a name and coordinates mean nothing to the person who has to answer. Before asking,
show the part alone, turned the way it prints, with each place of the question marked by a numbered red ball, and put
the picture next to the question (number in the picture = number in the question).

    python3 <plugin>/tools/fusion/show_part.py PART [--at X,Y,Z ...] [--gate overhang] [--view below|above|both]
                                               [--out DIR] [--size 1600x1000] [--project DIR] [--r MM]

PART is a name from fusion/parts.json (the part's bed face there turns the view: "below" looks at the side that faces
the bed, where overhangs are). --at marks a world point in mm (repeat it); --gate overhang marks every open finding of
the last overhang run (numbered in the order of its result file). Prints the PNG paths.

In Fusion (one call): the part's body is copied (read) from the document, which only has to be open, into a new
unsaved document; there it is made see-through when places are marked (a hole inside a wall stays visible), red balls
and numbers are drawn as custom graphics, the camera looks at it from the bed side or from above and fits it, the
images are saved, the new document is closed without saving and the document that was active is active again. The
project's document, its camera and its visibility are never touched."""
import argparse, json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fusion_rpc  # noqa: E402

AXES = {'-z': [0, 0, -1], '+z': [0, 0, 1], '-y': [0, -1, 0], '+y': [0, 1, 0], '-x': [-1, 0, 0], '+x': [1, 0, 0]}

JOB_SRC = r'''
import adsk.core, adsk.fusion, math, os

def _find(des, spec):
    occs_all = []
    stack = list(des.rootComponent.occurrences)
    while stack:
        o = stack.pop(); occs_all.append(o); stack.extend(o.childOccurrences)
    if spec.get('path'):
        occs, occ = des.rootComponent.occurrences, None
        for p in spec['path']:
            occ = next((o for o in occs if o.name == p), None)
            if occ is None:
                raise KeyError('no occurrence %r' % p)
            occs = occ.childOccurrences
    else:
        cands = [o for o in occs_all if o.component.name == spec['component']]
        if len(cands) != 1:
            raise KeyError('component %r has %d occurrences' % (spec.get('component'), len(cands)))
        occ = cands[0]
    bodies = [b for b in occ.bRepBodies if not spec.get('body') or b.name == spec['body']]
    if len(bodies) != 1:
        raise KeyError('%d bodies match %r' % (len(bodies), spec.get('body')))
    return occ, bodies[0]

def _v(a):
    return adsk.core.Vector3D.create(a[0], a[1], a[2])

def _n(a):
    l = math.sqrt(sum(x * x for x in a)) or 1.0
    return [x / l for x in a]

def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

def job(J):
    app = adsk.core.Application.get()
    docs = [d for d in app.documents if d.name == J['doc']]
    if not docs:
        raise KeyError('document %r is not open' % J['doc'])
    was_active = app.activeDocument
    des = adsk.fusion.Design.cast(docs[0].products.itemByProductType('DesignProductType'))
    occ, body = _find(des, J['spec'])
    tbm = adsk.fusion.TemporaryBRepManager.get()
    copy = tbm.copy(body)                                   # world coordinates; the real document is only read
    tmp = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    out = []
    try:
        d2 = adsk.fusion.Design.cast(tmp.products.itemByProductType('DesignProductType'))
        d2.designType = adsk.fusion.DesignTypes.DirectDesignType
        b2 = d2.rootComponent.bRepBodies.add(copy)
        if J.get('marks'):
            b2.opacity = J.get('opacity', 0.45)            # a place inside a wall (a hole) stays visible through it
        group = d2.rootComponent.customGraphicsGroups.add()
        bb = b2.boundingBox
        lo = [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z]; hi = [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z]
        size = max(hi[i] - lo[i] for i in range(3))
        r = (J.get('r') or max(0.6, size * 10 * 0.025)) / 10.0
        red = adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(230, 30, 20, 255))
        for k, m in enumerate(J.get('marks') or []):
            c = adsk.core.Point3D.create(m[0] / 10, m[1] / 10, m[2] / 10)
            g = group.addBRepBody(tbm.createSphere(c, r))
            g.color = red
            try:
                mtx = adsk.core.Matrix3D.create(); mtx.translation = adsk.core.Vector3D.create(c.x + r * 1.3, c.y + r * 1.3, c.z + r * 1.3)
                t = group.addText(str(k + 1), 'Arial', r * 2.6, mtx)
                t.color = red
                t.billBoarding = adsk.fusion.CustomGraphicsBillBoard.create(adsk.core.Point3D.create(0, 0, 0))
            except Exception:
                pass
        vp = app.activeViewport
        target = [(lo[i] + hi[i]) / 2 for i in range(3)]
        d = _n(J['down'])
        a = [1, 0, 0] if abs(d[0]) < 0.9 else [0, 1, 0]
        s1 = _n(_cross(d, a)); s2 = _n(_cross(d, s1))
        for view in J['views']:
            sgn = 1.0 if view == 'below' else -1.0                 # below: from the bed side, where the overhangs face
            e = _n([sgn * 0.75 * d[i] + 0.5 * s1[i] + 0.45 * s2[i] for i in range(3)])
            cam = vp.camera
            cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType
            cam.target = adsk.core.Point3D.create(*target)
            cam.eye = adsk.core.Point3D.create(*[target[i] + e[i] * size * 4 for i in range(3)])
            cam.upVector = _v([-x for x in d])                       # the printer's up
            cam.isSmoothTransition = False
            vp.camera = cam
            cam = vp.camera; cam.isFitView = True; cam.isSmoothTransition = False; vp.camera = cam
            vp.refresh(); adsk.doEvents()
            path = os.path.join(J['dir'], '%s_%s.png' % (J['name'], view))
            if not vp.saveAsImageFile(path, J['w'], J['h']):
                raise RuntimeError('saveAsImageFile failed for %s' % path)
            out.append(path)
    finally:
        try:
            tmp.close(False)                                # may raise "count > 0" and still close
        except Exception:
            pass
        try:
            if was_active:
                was_active.activate()
        except Exception:
            pass
    return {'images': out}
'''


def find_project(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, 'fusion', 'parts.json')):
            return d
        up = os.path.dirname(d)
        if up == d:
            sys.exit('no fusion/parts.json in %s or above it: pass --project' % start)
        d = up


def main():
    a = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    a.add_argument('part'); a.add_argument('--at', action='append', default=[]); a.add_argument('--gate')
    a.add_argument('--view', default='both', choices=['below', 'above', 'both']); a.add_argument('--out')
    a.add_argument('--size', default='1600x1000'); a.add_argument('--project'); a.add_argument('--r', type=float)
    a = a.parse_args()
    root = os.path.abspath(a.project) if a.project else find_project(os.getcwd())
    cfg = json.load(open(os.path.join(root, 'fusion', 'parts.json'), encoding='utf-8'))
    if a.part not in cfg['parts']:
        sys.exit('%s is not in fusion/parts.json' % a.part)
    e = cfg['parts'][a.part]
    face = next((t.get('face') for t in e.get('3mf') or [] if t.get('face')), '-z')
    marks = [[float(x) for x in s.split(',')] for s in a.at]
    if a.gate:
        rp = os.path.join(root, 'gate', 'results', a.gate, a.part + '.json')
        rec = json.load(open(rp, encoding='utf-8'))
        marks += [f['at'] for f in rec.get('findings', []) if f.get('at')]
    w, h = (int(x) for x in a.size.split('x'))
    run_dir = fusion_rpc._out_dir()
    job = {'doc': e.get('doc') or cfg.get('doc'), 'spec': {k: v for k, v in e.items() if k in ('component', 'path', 'body')},
           'name': a.part, 'down': AXES[face], 'marks': marks, 'views': ['below', 'above'] if a.view == 'both' else [a.view],
           'dir': run_dir, 'w': w, 'h': h, 'r': a.r}
    try:
        r = fusion_rpc.run_job(JOB_SRC, job, write=True, timeout=120, name='show_part')   # graphics and isolation need a normal call; all is put back
    except fusion_rpc.FusionError as ex:
        sys.exit('no picture: %s' % ex)
    out = a.out or os.path.join(root, '.shots')
    os.makedirs(out, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for p in r['images']:
        dst = os.path.join(out, '%s_%s' % (stamp, os.path.basename(p)))
        shutil.move(p, dst)
        print(dst)
    if marks:
        for k, m in enumerate(marks):
            print('  %d: (%s)' % (k + 1, ', '.join('%.1f' % x for x in m)))


if __name__ == '__main__':
    main()
