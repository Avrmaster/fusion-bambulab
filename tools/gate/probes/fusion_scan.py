"""Discover the parts of one document: every component with a solid body, its B-rep signature and its pose.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

What is a printed part: a solid body whose own light bulb is on and whose names do not match job["not_printed"]. The
names tested are the body name, the component name and the name of every occurrence on the path from the root (so
everything inside a bought assembly "ref motor:1" is not printed). A hidden OCCURRENCE does not make a part a ref: it
stays a part with "visible": false, because hiding a component must not take it out of the gate. A solid body switched
off by its own light bulb is a ref with "visible": false (a leftover tool body is not a part).
Faces are looped over for the signature of a printed body (gate_sign_step of _fusion.py) and for the "shape" of a ref
(gate_ref_shape: area and centroid of each face, remembered for the Fusion session); a ref's "sig" itself comes from
volume, area, counts and bounding box only.
Time: the clock is read between two occurrences AND inside the face loop of a signature. A body with thousands of faces
that does not fit into what is left of the call is finished in the next call: the cursor then carries the signature in
progress, and the envelope's notes name the body.
A component that is saved in another document (a linked document) is listed like any other: its printed bodies are parts
with "external": true and "linked": the name of that document. The host decides who judges them."""


def _scan_printed(path, comp_name, body_name, job):
    names = [n.rsplit(':', 1)[0] for n in (path or '').split('+') if n] + [comp_name, body_name]
    return all(gate_printed(n, job) for n in names)


def _scan_linked(occ, own_name):
    """(external, linked) of an occurrence. external: True when its component, or a component above it on the path, is
    saved in another document; False when not; None when Fusion does not say. linked: the name of that document (without
    the " v12" of the tab), None when it is this document or the name cannot be read."""
    if occ is None:
        return False, None
    try:
        name = _gate_doc_name(occ.component.parentDesign.parentDocument)
    except Exception:
        name = None
    if name and name != own_name:
        return True, name
    try:
        o, steps = occ, 0
        while o is not None and steps < 50:
            if o.isReferencedComponent:
                return True, None
            o, steps = getattr(o, 'assemblyContext', None), steps + 1
    except Exception:
        return None, None
    return False, None


def _scan_shown(occ, memo):
    """Is every light bulb on the path of an occurrence on (its own and those of the occurrences above it)? The root
    component (None) is shown. memo: {fullPathName: bool} of this call, so a deep assembly is read once."""
    if occ is None:
        return True
    try:
        key = occ.fullPathName
        if key in memo:
            return memo[key]
        up = getattr(occ, 'assemblyContext', None)
        memo[key] = bool(occ.isLightBulbOn) and _scan_shown(up, memo)
        return memo[key]
    except Exception:
        return False


def _scan_references(doc):
    """[{"name", "version", "latest", "out_of_date"}] of the documents this document references (linked components), from
    Document.documentReferences: "version" is the version this document uses, "latest" the newest version of that file
    when Fusion gives it, "out_of_date" is DocumentReference.isOutOfDate (None when Fusion does not say). Read once per
    scan (in its first call). Parts of a linked document are measured as this document holds them: an older version
    of it than the one saved shows features that are no longer there and lacks the new ones."""
    out = []
    try:
        refs = list(doc.documentReferences)
    except Exception:
        return out
    for r in refs[:200]:
        row = {'name': None, 'version': None, 'latest': None, 'out_of_date': None}
        try:
            row['out_of_date'] = bool(r.isOutOfDate)
        except Exception:
            pass
        try:
            row['version'] = int(r.version)
        except Exception:
            pass
        try:
            f = r.dataFile
            row['name'] = str(f.name) if f is not None else None
            row['latest'] = int(f.latestVersionNumber) if f is not None else None
        except Exception:
            pass
        out.append(row)
    out.sort(key=lambda x: (str(x['name']), x['version'] or 0))
    return out


def _scan_mesh(mb, matrix):
    """(signature, bbox in the document frame, triangles, shape) of a mesh body, from its display mesh; Nones when it
    has none. shape: 16 hex over its node coordinates and triangle indices (as 32-bit numbers), so a mesh whose nodes
    move inside the same box is a change."""
    from array import array
    try:
        dm = mb.displayMesh
        c = dm.nodeCoordinatesAsDouble
        idx = dm.nodeIndices
        tris = len(idx) // 3
    except Exception:
        return None, None, 0, None
    if not len(c):
        return None, None, 0, None
    lo = tuple(min(c[i + k] for i in range(0, len(c), 3)) * 10.0 for k in range(3))
    hi = tuple(max(c[i + k] for i in range(0, len(c), 3)) * 10.0 for k in range(3))
    dlo, dhi = box_transform(matrix, lo, hi)
    try:
        shape = hashlib.sha256(array('f', c).tobytes() + array('I', idx).tobytes()).hexdigest()[:16]
    except Exception:
        shape = None
    return cheap_signature(0.0, 0.0, (tris, len(c) // 3, 0), (lo, hi)), [list(dlo), list(dhi)], tris, shape


def probe(job):
    """One call covers as many occurrences as the budget allows.

    job  uses "doc", "not_printed" (regex list: a component or body name that matches is not a printed part),
         "cursor" (None = start; see below), "deadline_s", "args" ({"limit": n}: at most n occurrences in this call;
         0 = none, the call only reports which documents are open, in the envelope)

    Returns {"facts": {...}, "done": bool, "cursor": where the next call goes on or None, "sig": None} with facts
      "parts": [{"name": "Motor plate"            component name; "<component> / <body>" when the component has several solid bodies
                 "component_id": "...", "occurrence": "Base:1+Motor plate:1" (fullPathName; None for the root component),
                 "body": "Body1", "sig": 64 hex (gate_signature), "pose": 16 hex (pose_hash of gate_matrix),
                 "matrix": [16 floats], "bbox": [[x, y, z], [x, y, z]] part frame, "volume": mm3, "area": mm2, "faces": n}]
               one entry per printed solid body; a component placed several times is listed once (its first occurrence)
               with "copies": n
      "refs":  [{"name", "component_id", "occurrence", "body", "sig": 16 hex over volume, area, counts, bbox (cheap),
                 "pose", "bbox_doc": [[x, y, z], [x, y, z]] document frame, "faces": n, "mesh": bool (a mesh body, no
                 B-rep), "primitive": bool (a plain box or cylinder: at most 6 faces), "visible": bool,
                 "shape": 16 hex over its faces (gate_ref_shape) or, for a mesh, over its nodes; None when unreadable}]
               every body that is NOT a printed part: bought parts, hardware, hidden components
    Lists of several calls are concatenated by the host (gate_fusion_rpc.merge_facts).

    More keys than the list above: a part also has "visible" (its occurrence's light bulb), "external" and "linked"
    (_scan_linked: the part comes from a linked document, and which one) and "copy_paths" (the paths of its other
    occurrences, at most 50) and "shown" (every light bulb on its path and its own are on: the visibility the walks of
    the units see). facts "copy_bodies" lists the further copies of the parts: [{"name", "component_id", "occurrence",
    "body", "pose", "matrix", "bbox" (part frame), "faces", "visible", "shown"}], one per printed body of a further
    occurrence: no signature (it is the part's), only where it is. Refs have "shown" too. The host keeps every body of
    the document with its signature, pose and visibility (gate_deps.world): what the units of the parts depend on.
    "several solid bodies" counts the PRINTED solid bodies of the component (part_name of
    _geom.py). A ref's name is the component name when the component holds one body, else "<component> / <body>".
    "copies" counts the occurrences whose path is not under a not-printed name (the same component inside a bought
    assembly is a ref there and is listed as one). A mesh ref without a display mesh has "sig" and "bbox_doc" None.
    facts "occurrences" is the number of occurrences + 1 (the root component). The first call also gives "references"
    (_scan_references: the documents this one links, the version it uses, the newest, and whether it is out of date).

    The cursor. A whole number i: go on with occurrence i (0 is the root component, i the occurrence i - 1 of
    rootComponent.allOccurrences). An object {"i", "b", "sign"}: occurrence i was begun; its solid bodies before index
    "b" are already listed, and body "b" has a signature in progress ("sign", the state of gate_sign_step)."""
    args = job.get('args') or {}
    limit = args.get('limit')
    if limit is not None and int(limit) <= 0:
        return {'facts': {'parts': [], 'refs': []}, 'done': True, 'cursor': None, 'sig': None}
    root = gate_design(job).rootComponent
    own_name = _gate_doc_name(gate_document(job))
    references = _scan_references(gate_document(job)) if job.get('cursor') is None else None
    occs = list(root.allOccurrences)
    first, paths = {}, {}
    for i, o in enumerate(occs):                        # cheap: the first occurrence of a component that can hold parts, and its copies
        cid, path = o.component.id, o.fullPathName
        if _scan_printed(path, o.component.name, '', job):                   # an occurrence inside a bought assembly holds refs only
            first.setdefault(cid, i + 1)
            paths.setdefault(cid, []).append(path)
    parts, refs, copies, memo = [], [], [], {}
    cur = job.get('cursor')
    if isinstance(cur, dict):
        i, skip, sign = int(cur.get('i') or 0), int(cur.get('b') or 0), cur.get('sign')
    else:
        i, skip, sign = int(cur or 0), 0, None
    n_done, stop = 0, None
    while i <= len(occs):
        if limit is not None and n_done >= int(limit):
            break
        if n_done and gate_time_up(job):
            break
        occ = occs[i - 1] if i else None
        comp = occ.component if occ is not None else root
        path = occ.fullPathName if occ is not None else None
        cid = comp.id
        matrix = gate_matrix(occ)
        pose = pose_hash(matrix)
        occ_on = True if occ is None else bool(occ.isLightBulbOn)
        shown = _scan_shown(occ, memo)
        solids = [b for b in comp.bRepBodies if b.isSolid]
        meshes = list(comp.meshBodies)
        printed = [bool(b.isLightBulbOn) and _scan_printed(path, comp.name, b.name, job) for b in solids]
        n_bodies = len(solids) + len(meshes)
        for j, (b, is_part) in enumerate(zip(solids, printed)):
            if j < skip:
                continue                                # listed by the call that began this occurrence
            lo, hi = gate_bbox(b)
            if is_part:
                if occ is not None and first.get(cid) != i:
                    # a further copy: counted in "copies" of the first one, and listed in "copy_bodies" with its place
                    # (another body of the document: a neighbour of other parts, whose units depend on where it is)
                    copies.append({'name': part_name(comp.name, b.name, sum(printed)), 'component_id': cid, 'occurrence': path, 'body': b.name,
                                   'pose': pose, 'matrix': [round(x, 6) for x in matrix], 'bbox': [list(lo), list(hi)], 'faces': b.faces.count,
                                   'visible': occ_on, 'shown': shown and bool(b.isLightBulbOn)})
                    continue
                state = gate_sign_step(b, sign if j == skip else None, job)
                if state['k'] < state['n']:             # the time is used up inside this body: the next call goes on with it
                    stop = {'i': i, 'b': j, 'sign': state}
                    note = 'the signature of %r (%d faces) takes more than one call: %d faces are in' % (b.name, state['n'], state['k'])
                    if note not in _GATE_NOTES:
                        _GATE_NOTES.append(note)
                    break
                sig = gate_sign_done(b, state)
                gate_remember_signature(b, sig)
                others = [p for p in paths.get(cid, []) if p != path]
                external, linked = _scan_linked(occ, own_name)
                parts.append({'name': part_name(comp.name, b.name, sum(printed)), 'component_id': cid, 'occurrence': path,
                              'body': b.name, 'sig': sig, 'pose': pose, 'matrix': [round(x, 6) for x in matrix],
                              'bbox': [list(lo), list(hi)], 'volume': round(b.volume * 1000.0, 2), 'area': round(b.area * 100.0, 2),
                              'faces': b.faces.count, 'copies': len(others) + 1, 'copy_paths': others[:50],
                              'visible': occ_on, 'shown': shown and bool(b.isLightBulbOn), 'external': external, 'linked': linked})
            else:
                dlo, dhi = box_transform(matrix, lo, hi)
                refs.append({'name': comp.name if n_bodies == 1 else '%s / %s' % (comp.name, b.name), 'component_id': cid,
                             'occurrence': path, 'body': b.name,
                             'sig': cheap_signature(b.volume * 1000.0, b.area * 100.0, (b.faces.count, b.edges.count, b.vertices.count), (lo, hi)),
                             'shape': gate_ref_shape(b),
                             'pose': pose, 'bbox_doc': [list(dlo), list(dhi)], 'faces': b.faces.count, 'mesh': False,
                             'primitive': b.faces.count <= 6, 'visible': occ_on and bool(b.isLightBulbOn), 'shown': shown and bool(b.isLightBulbOn)})
        if stop is not None:
            break
        for mb in meshes:
            sig, box, tris, shape = _scan_mesh(mb, matrix)
            refs.append({'name': comp.name if n_bodies == 1 else '%s / %s' % (comp.name, mb.name), 'component_id': cid,
                         'occurrence': path, 'body': mb.name, 'sig': sig, 'shape': shape, 'pose': pose, 'bbox_doc': box, 'faces': tris,
                         'mesh': True, 'primitive': False, 'visible': occ_on and bool(mb.isLightBulbOn), 'shown': shown and bool(mb.isLightBulbOn)})
        skip, sign = 0, None
        i += 1
        n_done += 1
    done = stop is None and i > len(occs)
    facts = {'parts': parts, 'refs': refs, 'copy_bodies': copies, 'occurrences': len(occs) + 1}
    if references is not None:
        facts['references'] = references
    return {'facts': facts, 'done': done,
            'cursor': None if done else (stop if stop is not None else i), 'sig': None}
