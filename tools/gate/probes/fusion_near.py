"""What stands near one part and is not printed: the bought, hardware and mesh bodies whose bounding box comes within a margin of the part's box.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.

What it is for: the standing questions of a part (motor leads, bearing clamps, bought parts from their STEP file) are
asked only where they can apply. "Cannot apply" needs a proof, and this list is one: a body that is NOT listed here
is farther from the part than the margin. The distance is the one between the two bounding boxes, which is never more
than the real gap: a box test can say "far", it cannot say "touching". So a body in the list is only a candidate, and
a body missing from it is proven to be away.
How it measures: nothing but boxes. gate_world() of _fusion.py walks the document once (the one walker with the rule
and the names of the scan: what is printed, what is a ref; visibility is never read). No face is read and no
temporary body is made, so the unit costs one walk over the bodies of the document.
Limits: a mesh body that was never drawn has no box and is not listed (the envelope's notes name it); what a name
means (a motor, a bearing) is not decided here: the host holds the name patterns (gate_attest)."""


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "params" ("process.near_margin"), "not_printed", "cursor",
         "deadline_s"

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body)} with facts
      "margin": 10.0                       mm: bodies whose box is farther from the part's box are not listed
      "bodies": [{"name": "ref motor / Rotor",   as gate_world() names a ref: the component, or "<component> / <body>"
                  "path": "ref motor+Rotor"      the occurrence path from the root, copy numbers dropped ("" at the root)
                  "kind": "bought" | "hardware" | "mesh",
                  "gap": 0.0                     mm between the two bounding boxes (0.0 = the boxes touch or overlap)
                 }]                        every body that is not a printed part, nearest first, then by name. A printed
                                           neighbour is not listed: the gap and access checks look at those
    Nothing else: the host counts the rows. A unit that needs a second call (a very large document) sends the rest of
    the rows then, and the host joins the two lists.
    The cursor is the index of the next body of the walk."""
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    sig = gate_signature(body)
    pbody = body.createForAssemblyContext(occ) if occ is not None else body
    part_box = gate_bbox(pbody)
    margin = float((job.get('params') or {}).get('process.near_margin', 10.0))
    own_path = '' if occ is None else occ.fullPathName
    # what the facts depend on: every body in the box (the wide record) and, exactly, the bodies that are no printed part
    # (the tight record: a printed neighbour is never listed, so it cannot change these facts)
    gate_read(part_box, margin, mode='wide')
    gate_read(part_box, margin, mode='tight', kinds=('ref', 'mesh'))
    world = [rec for rec in gate_world(design, job, part_box, margin, own_path, body.name, read=False) if not rec['own']]
    facts = {'margin': margin, 'bodies': []}
    start = int(job.get('cursor') or 0)
    i = start
    while i < len(world):
        if i > start and i % 50 == 0 and gate_time_up(job, 2.0):
            facts['bodies'].sort(key=lambda r: (r['gap'], r['name']))
            return {'facts': facts, 'done': False, 'cursor': i, 'sig': sig}
        rec = world[i]
        i += 1
        if rec['kind'] == 'printed':
            continue
        gap = box_gap(part_box[0], part_box[1], rec['box'][0], rec['box'][1])
        row = {'name': rec['name'], 'path': '+'.join(n.rsplit(':', 1)[0] for n in (rec['path'] or '').split('+') if n),
               'kind': rec['kind'], 'gap': round(max(0.0, gap), 2)}
        facts['bodies'].append(row)
    facts['bodies'].sort(key=lambda r: (r['gap'], r['name']))
    return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig}
