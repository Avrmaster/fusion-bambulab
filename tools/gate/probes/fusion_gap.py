"""Static minimum gap from one part to the bodies of the other declared motion groups, in the pose as modelled.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

How it measures: Application.measureManager.measureMinimumDistance between the part's body and each body of another
motion group whose bounding box comes within the margin. Both are taken in the document frame (the occurrence's proxy
body), so the pose is the one drawn. A mesh body has no B-rep: its bounding box stands for it ("approx").
A body whose measurement fails is listed with the distance between the bounding boxes (never more than the real gap)
and "approx": True, so a failed call cannot read as "far enough".
Which bodies are printed and how they are named (a hidden body is measured like a shown one, visibility is never read): gate_world() of _fusion.py, the
one walker with the rule of the scan.
Limits: the pose as modelled only, no sweep over the travel; a body in no declared group is not measured."""


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "motion_groups" ([{"key": "slide", "patterns": [regex on the
         occurrence path]}] of gate/layout.json), "params" ("clearance.scan_margin"), "not_printed", "cursor", "deadline_s"

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body)} with facts
      "own_group": "base" or None          the motion group this part belongs to; None = in no group (nothing is measured)
      "margin": 5.0                        mm: neighbours farther than this are not listed
      "gaps": [{"name": "Pulley", "kind": "printed" | "bought" | "hardware" | "mesh", "part": id or None, "group": "slide",
                "gap": 0.5                 least distance, mm (0.0 = touching or overlapping)
                "point": [x, y, z]         the closest point on THIS part, part frame
                "dir": [x, y, z]           unit direction from that point towards the neighbour
                "approx": bool}]           True for a mesh ref measured by its bounding box
            one entry per body of another group within the margin

    More than the contract lists: a gap also has "error" with the text when the measurement failed and the bounding boxes were used; facts hold
    "n_bodies": the bodies of other groups whose bounding box is within the margin, and "groups": how many motion groups
    the document declares (0: nothing can be checked for any part, the judge says so). A part at the root of the document
    is matched to its group by the component name, every other part by its occurrence path.
    The cursor is the index of the next body in that list.
    One measurement between two bodies is one API call and cannot be cut. A call stops before the next body when less
    time is left than 1.5 times the slowest measurement of this call (at least 2.5 s); the facts are the same."""
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    sig = gate_signature(body)
    inv = mat_inverse(gate_matrix(occ))
    pbody = body.createForAssemblyContext(occ) if occ is not None else body
    part_box = gate_bbox(pbody)
    margin = float((job.get('params') or {}).get('clearance.scan_margin', 5.0))
    own_path = '' if occ is None else occ.fullPathName
    own_group = group_of(own_path or comp.name, job.get('motion_groups') or [], gate_search)
    facts = {'own_group': own_group, 'margin': margin, 'gaps': [], 'groups': len(job.get('motion_groups') or [])}
    if own_group is None:
        return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig}
    groups = job.get('motion_groups') or []
    world = []
    # what the facts depend on: every body in the box (the wide record) and, exactly, the bodies of another motion group
    # (the tight record: a body of its own group or of none is never measured; a body at the root is always held)
    gate_read(part_box, margin, mode='wide')
    gate_read(part_box, margin, mode='tight', other_group=own_group)
    for rec in gate_world(design, job, part_box, margin, own_path, body.name, read=False):
        if rec['own']:
            continue
        group = group_of(rec['path'] or rec['comp'], groups, gate_search)
        if group is None or group == own_group:
            continue
        world.append(dict(rec, group=group))
    facts['n_bodies'] = len(world)
    mm = adsk.core.Application.get().measureManager
    start = int(job.get('cursor') or 0)
    i = start
    slowest = 0.0
    while i < len(world):
        if i > start and gate_time_up(job, max(2.5, 1.5 * slowest)):
            return {'facts': facts, 'done': False, 'cursor': i, 'sig': sig}
        rec = world[i]
        i += 1
        gate_asked(rec['name'], rec['box'], rec)                     # the cover check: it must lie in a region of each record
        t0 = time.time()
        centre = tuple((rec['box'][0][k] + rec['box'][1][k]) / 2.0 for k in range(3))
        row = {'name': rec['name'], 'kind': rec['kind'], 'part': rec['part'], 'group': rec['group'], 'approx': rec['mesh']}
        p1 = p2 = None
        if not rec['mesh']:
            try:
                res = mm.measureMinimumDistance(pbody, rec['body'])
                gap, p1, p2 = res.value * 10.0, gate_mm(res.positionOne), gate_mm(res.positionTwo)
            except Exception as e:
                row['error'], row['approx'] = ('%s: %s' % (type(e).__name__, e))[:160], True
            slowest = max(slowest, time.time() - t0)
        if p1 is None:                                                  # a mesh, or a measurement that failed: the boxes
            gap = box_gap(part_box[0], part_box[1], rec['box'][0], rec['box'][1])
            p2 = box_nearest(rec['box'][0], rec['box'][1], box_nearest(part_box[0], part_box[1], centre))
            p1 = box_nearest(part_box[0], part_box[1], p2)
        if gap > margin:
            continue
        d = v_unit(v_sub(p2, p1))
        if v_len(d) < 0.5:
            d = v_unit(v_sub(centre, p1))
        row.update({'gap': round(max(0.0, gap), 3), 'point': [round(c, 2) for c in mat_point(inv, p1)],
                    'dir': [round(c, 4) for c in v_unit(mat_vector(inv, d))]})
        facts['gaps'].append(row)
    return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig}
