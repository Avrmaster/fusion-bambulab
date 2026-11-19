"""Fusion-side helpers and the entry point of every gate unit. NOT importable on the host: it needs adsk.

gate_fusion_rpc.build_script() builds one script per unit:

    import json
    JOB = json.loads('...')          the header: what to do, where to write (gate_fusion_rpc.py lists the keys)
    <text of _geom.py>               pure helpers
    <text of _geom_rings.py>         pure helpers
    <text of this file>              Fusion helpers + run()
    <text of the probe>              defines probe(job)

That text is NOT what Fusion gets at top level: the add-in keeps one namespace for the scripts of every session and
calls whatever run() it finds there. build_script() therefore sends the unit as one string inside a small wrapper: the
wrapper's run() execs the unit in a private dict, calls the run() below, and then leaves a run() that does nothing
behind. So none of the names here reaches the shared namespace, and a later script of any session cannot start this
unit a second time.

The wrapper calls run(). run() calls probe(JOB) and writes ONE JSON file, JOB["out"]; nothing is printed (a broken add-in
stdout can roll a call back or return nothing). A probe returns
    {"facts": {...}, "done": True | False, "cursor": anything JSON or None, "sig": signature of the body it measured or None}
and run() wraps it into the envelope
    {"job", "doc", "doc_version", "part", "sig", "probe", "facts", "done", "cursor", "elapsed_s", "error",
     "open_documents": [names of every open document], "seen": {"doc", "body", "occurrence"} as they arrived in Fusion
     (the host compares them with what it sent: a name damaged on the way must not read as "document not open"),
     "notes": [texts: what was resolved another way than asked, for example an occurrence found by its component id]}.
An exception inside the probe becomes "error" (text, with the end of the traceback); the file is still written.
A document that is not open is such an error; "open_documents" is filled all the same.
One answer is made here and not by the probe: when the signature of a large body needs more time than the call has left
(gate_signature raises GateNotYet), the envelope is {"facts": {"signing": {"faces_done", "faces"}}, "done": False} with
the cursor the call came with. The host calls the unit again and the signature goes on.

Rules every probe keeps (skills/fusion-modeling): the document is taken BY NAME with gate_document(), never the active
one; nothing is opened, activated, saved or closed; no timeline edit, no body is deleted; temporary geometry only through
adsk.fusion.TemporaryBRepManager; the API works in cm, facts are in mm; between features the probe asks gate_time_up()
and returns {"done": False, "cursor": ...} when the budget is used up."""
import adsk.core, adsk.fusion, json, os, re, sys, time, traceback

_GATE_STARTED = time.time()
_GATE_NOTES = []
_GATE_SIG_STEP = 50                         # faces added to a signature between two looks at the clock
_GATE_HW = r'(?i)dowel|\bpin\b'             # hardware by name that has no pattern parameter of its own


def _gate_t0():
    """When the budget of this call began: the moment the host sent it (JOB["sent"], same machine, same clock), so a
    unit that waited behind another session's call does not work on after the host has stopped waiting. A send time in
    the future or more than two minutes old is not believed: then it is the start of the script."""
    sent = JOB.get('sent')
    if isinstance(sent, (int, float)) and 0.0 <= _GATE_STARTED - sent <= 120.0:
        return float(sent)
    return _GATE_STARTED



def _gate_doc_name(doc):
    return re.sub(r' v\d+$', '', doc.name)


def gate_document(job):
    """The open document named job["doc"] (with or without the " v12" the tab shows). KeyError when it is not open,
    ValueError when two different open documents carry the name. Never the active document."""
    name = job['doc']
    app = adsk.core.Application.get()
    docs = [d for d in app.documents if d.name == name or _gate_doc_name(d) == name]
    if not docs:
        raise KeyError('document %r is not open (open: %s)' % (name, ', '.join(sorted(_gate_doc_name(d) for d in app.documents))))
    if len({_gate_file_id(d) for d in docs}) != 1:
        raise ValueError('two different open documents are named %r' % name)
    if len(docs) > 1:                                   # two versions of one file are open: the newest one, and say so
        docs.sort(key=lambda d: (d.name == name, gate_doc_version(d) or 0), reverse=True)
        note = 'document %r is open %d times (%s): %s was read' % (name, len(docs), ', '.join(d.name for d in docs), docs[0].name)
        if note not in _GATE_NOTES:
            _GATE_NOTES.append(note)
    return docs[0]


def _gate_file_id(doc):
    """The id of the saved file behind a document; the document object itself for one that was never saved."""
    try:
        f = doc.dataFile
        return f.id if f else id(doc)
    except Exception:
        return id(doc)


def gate_design(job):
    """The adsk.fusion.Design of gate_document(job)."""
    design = adsk.fusion.Design.cast(gate_document(job).products.itemByProductType('DesignProductType'))
    if design is None:
        raise ValueError('document %r has no design' % job['doc'])
    return design


def gate_doc_version(doc):
    """Saved version number of the document, or None for a document that was never saved."""
    try:
        return doc.dataFile.versionNumber if doc.dataFile else None
    except Exception:                                   # a document that was never saved may have no data file to ask
        return None


def gate_left(job):
    """Seconds left of job["deadline_s"] (default 20), counted from the moment the host sent the call (_gate_t0)."""
    return float(job.get('deadline_s') or 20) - (time.time() - _gate_t0())


def gate_time_up(job, reserve=1.5):
    """True when less than `reserve` seconds are left: stop and return a cursor."""
    return gate_left(job) < reserve


def gate_mm(p):
    """A Point3D or Vector3D (cm) as an (x, y, z) tuple in mm."""
    return (p.x * 10.0, p.y * 10.0, p.z * 10.0)


def gate_point(p):
    """An (x, y, z) tuple in mm as an adsk.core.Point3D (cm)."""
    return adsk.core.Point3D.create(p[0] / 10.0, p[1] / 10.0, p[2] / 10.0)


def gate_vector(v):
    """A direction tuple as an adsk.core.Vector3D."""
    return adsk.core.Vector3D.create(v[0], v[1], v[2])


def gate_matrix(occ):
    """16 floats, row-major, translation in mm: the occurrence's own frame -> the document frame. None (the root
    component) gives the identity. Use mat_point / mat_vector / mat_inverse of _geom.py with it."""
    if occ is None:
        return IDENTITY
    m = list(occ.transform2.asArray())
    for i in (3, 7, 11):
        m[i] *= 10.0
    return tuple(m)


def gate_occurrence(design, job):
    """The occurrence of the unit's part: the one whose fullPathName is job["occurrence"], else the first one of the
    component with the id job["component_id"]. None when the part is the root component. KeyError when it is gone."""
    root = design.rootComponent
    if job.get('component_id') and root.id == job['component_id'] and not job.get('occurrence'):
        return None
    occs = list(root.allOccurrences)
    for o in occs:
        if job.get('occurrence') and o.fullPathName == job['occurrence']:
            return o
    for o in occs:
        if job.get('component_id') and o.component.id == job['component_id']:
            note = 'occurrence %r is gone: the part was read from %r, another occurrence of the same component' % (job.get('occurrence'), o.fullPathName)
            if note not in _GATE_NOTES:
                _GATE_NOTES.append(note)
            return o
    raise KeyError('no occurrence %r and no component with the id %r in %r' % (job.get('occurrence'), job.get('component_id'), job['doc']))


def gate_body(design, job):
    """(occurrence or None, component, body) of the unit's part. The body is the NATIVE body of the component (its
    coordinates are the part frame), found by job["body"]; with no name, the only solid body. KeyError when it is gone."""
    occ = gate_occurrence(design, job)
    comp = occ.component if occ is not None else design.rootComponent
    solids = [b for b in comp.bRepBodies if b.isSolid]
    if job.get('body'):
        solids = [b for b in solids if b.name == job['body']]
    if len(solids) != 1:
        raise KeyError('component %r: expected one solid body%s, found %d' % (comp.name, (' named %r' % job['body']) if job.get('body') else '', len(solids)))
    return occ, comp, solids[0]


def gate_printed(name, job):
    """Is a component or body of that name a printed part: True unless it matches one of job["not_printed"] (regex)."""
    return not any(re.search(p, name) for p in job.get('not_printed') or [])


def gate_geo(obj):
    """The class name of a geometry object, exactly: "Cylinder", "Cone", "Plane", "Circle3D", "Arc3D", "Line3D",
    "EllipticalCylinder", ... Compare with ==, never with endswith: an EllipticalCylinder ends with "Cylinder" too and
    has no .radius."""
    return str(obj.objectType).rsplit('::', 1)[-1]


def gate_path_printed(path, comp_name, body_name, job):
    """The printed rule of the scan, by names alone: no name on the occurrence path (copy numbers dropped), nor the
    component name, nor the body name matches a not-printed pattern. Everything inside a bought assembly "ref motor:1"
    is not printed. (The scan also wants the body's own light bulb on; gate_world() adds that.)"""
    names = [n.rsplit(':', 1)[0] for n in (path or '').split('+') if n] + [comp_name, body_name]
    return all(gate_printed(n, job) for n in names)


def gate_ref_patterns(job):
    """{"screw", "washer", "bearing", "insert", "nut"}: the name patterns of the hardware refs, from the parameters of
    the job (their registry defaults when a parameter is missing)."""
    p = job.get('params') or {}
    return {'screw': p.get('screws.ref_pattern') or '(?i)screw|bolt', 'washer': p.get('washers.ref_pattern') or '(?i)washer',
            'bearing': p.get('bearings.ref_pattern') or '(?i)bearing', 'insert': p.get('inserts.ref_pattern') or '(?i)insert',
            'nut': p.get('screws.nut_ref_pattern') or r'(?i)(?<![a-z])nut(?![a-z])|locknut'}


def gate_search(pattern, text):
    """re.search that never raises: a pattern that does not compile matches nothing."""
    try:
        return bool(pattern) and re.search(pattern, text or '') is not None
    except re.error:
        return False


def gate_hardware(name, job):
    """Is a body of that name a hardware ref (screw, washer, bearing, insert, nut, dowel, pin) by its name?"""
    return any(gate_search(p, name) for p in list(gate_ref_patterns(job).values()) + [_GATE_HW])


def gate_mesh_box(mb, matrix):
    """((x, y, z) min, (x, y, z) max) in mm, document frame, of a mesh body of a component placed by `matrix`; None when
    it cannot be read. From the display mesh (the way the scan reads it): whether a MeshBody has a boundingBox is not
    relied on."""
    try:
        c = mb.displayMesh.nodeCoordinatesAsDouble
        if not len(c):
            return None
        lo = tuple(min(c[i + k] for i in range(0, len(c), 3)) * 10.0 for k in range(3))
        hi = tuple(max(c[i + k] for i in range(0, len(c), 3)) * 10.0 for k in range(3))
    except Exception:
        try:
            lo, hi = gate_bbox(mb)
        except Exception:
            return None
    return box_transform(matrix, lo, hi)


def gate_above(occ):
    """The names of the components above an occurrence's own component, outermost first: ["Gearbox", "Motor"] for the
    occurrence of "Rotor" at Gearbox:1+Motor:2+Rotor:1; [] for a top-level occurrence and for the root (None).
    Read from the occurrence objects (assemblyContext), never cut out of fullPathName: a component name may hold "+"
    and ":" itself ("clamp + foot", "shaft: pin 6x50 + sleeves"). Only when the chain cannot be read is the path text
    cut, and then at ":<digits>+" alone."""
    if occ is None:
        return []
    try:
        names, o, steps = [], occ.assemblyContext, 0
        while o is not None and steps < 50:
            names.append(str(o.component.name))
            o, steps = o.assemblyContext, steps + 1
        return names[::-1]
    except Exception:
        try:
            return [n for n in re.split(r':\d+\+', str(occ.fullPathName))[:-1] if n]
        except Exception:
            return []


def gate_world(design, job, box, margin, own_path, own_body):
    """Every solid and mesh body whose bounding box comes within `margin` mm of `box`, document frame, the part itself
    included. ONE walker for bearings, openings, screws and gap, with the rule and the names of the scan:

      printed   the body's own light bulb is on and gate_path_printed() holds (so a body inside a bought assembly is a
                ref, and a switched-off tool body is one too)
      name      a printed part: part_name(component, body, number of PRINTED bodies of the component) - the name its
                part id is made from; a ref: the component name when the component holds one body, else
                "<component> / <body>"
      visible   every light bulb on the path and the body's own are on. Hidden bodies ARE listed: hiding a component
                must not take it out of a check.

    No occurrence is skipped by its own bounding box (what that box holds for a hidden or mesh-only occurrence is not
    known): the box of every body is read. Returns
    [{"name", "comp", "body_name", "path", "above", "body", "box", "printed", "hardware", "visible", "mesh", "own",
      "kind": "printed" | "hardware" | "bought" | "mesh", "part": part id or None}], sorted by path and body name;
    "above" = gate_above() of the occurrence (the components it lies in, outermost first); a mesh record
    also has "matrix" (its component -> document). A solid body
    is the occurrence's proxy (document frame). A mesh body whose box cannot be read (it was never drawn: no display
    mesh) is left out and named in the envelope's "notes": it is not seen as an obstacle."""
    root = design.rootComponent
    out, unread = [], []
    for occ in [None] + list(root.allOccurrences):
        comp = root if occ is None else occ.component
        path = '' if occ is None else occ.fullPathName
        solids = [b for b in (root.bRepBodies if occ is None else occ.bRepBodies) if b.isSolid]
        try:
            meshes = list(comp.meshBodies)
        except Exception:
            meshes = []
        if not solids and not meshes:
            continue
        vis, o = True, occ
        while o is not None:
            if not o.isLightBulbOn:
                vis = False
                break
            o = o.assemblyContext
        printed = [bool(b.isLightBulbOn) and gate_path_printed(path, comp.name, b.name, job) for b in solids]
        above = gate_above(occ)
        n_bodies = len(solids) + len(meshes)
        for b, is_part in zip(solids, printed):
            bb = gate_bbox(b)
            if box_gap(bb[0], bb[1], box[0], box[1]) > margin:
                continue
            own = path == own_path and b.name == own_body
            if is_part or own:
                name = part_name(comp.name, b.name, max(1, sum(printed)))
            else:
                name = comp.name if n_bodies == 1 else '%s / %s' % (comp.name, b.name)
            hardware = (not is_part) and (gate_hardware(comp.name, job) or gate_hardware(b.name, job))
            out.append({'name': name, 'comp': comp.name, 'body_name': b.name, 'path': path, 'above': above, 'body': b, 'box': bb, 'printed': is_part,
                        'hardware': hardware, 'visible': vis and bool(b.isLightBulbOn), 'mesh': False, 'own': own,
                        'kind': 'printed' if (own or is_part) else ('hardware' if hardware else 'bought'),
                        'part': job.get('part') if own else ('%s/%s' % (job.get('doc_key'), part_slug(name)) if is_part else None)})
        m = gate_matrix(occ)
        for mb in meshes:
            bb = gate_mesh_box(mb, m)
            if bb is None:
                unread.append('%s / %s' % (comp.name, mb.name))
                continue
            if box_gap(bb[0], bb[1], box[0], box[1]) > margin:
                continue
            out.append({'name': comp.name if n_bodies == 1 else '%s / %s' % (comp.name, mb.name), 'comp': comp.name, 'body_name': mb.name,
                        'path': path, 'above': above, 'body': mb, 'box': bb, 'printed': False, 'hardware': False, 'visible': vis and bool(mb.isLightBulbOn),
                        'mesh': True, 'own': False, 'kind': 'mesh', 'part': None, 'matrix': m})
    if unread:
        note = '%d mesh bodies have no readable box and were not looked at: %s' % (len(unread), ', '.join(sorted(unread)[:8]))
        if note not in _GATE_NOTES:
            _GATE_NOTES.append(note)
    out.sort(key=lambda r: (r['path'], r['body_name']))
    return out


class GateNotYet(Exception):
    """The signature of the unit's body is not complete and the time of this call is used up. run() answers "not done"
    with the cursor the call came with; the next call of the unit goes on where this one stopped."""


def _gate_cache():
    """What this Fusion process remembers between two unit scripts: finished signatures ("done") and signatures in
    progress ("partial"). It lives in a module object of its own in sys.modules, not in the namespace the scripts of
    every session share, and it is gone when Fusion quits."""
    mod = sys.modules.get('_gate_signature_cache')
    if mod is None:
        mod = type(sys)('_gate_signature_cache')
        mod.done, mod.partial = {}, {}
        sys.modules['_gate_signature_cache'] = mod
    return mod


def _gate_sig_key(body):
    """The text that names one state of one body: the id of its component and its own name (which body it is), its
    revision id (Fusion changes it whenever the body is modified in any way), the face count, the volume and the area.
    None when Fusion gives no revision id: then nothing is remembered and every signature is computed from the faces.
    The component and the name are part of the key because nothing says that two bodies never share a revision id:
    two equal bodies with one key would get one signature, and one of them would read as changed for ever."""
    try:
        rev = body.revisionId
    except Exception:
        return None
    if not rev:
        return None
    try:
        owner = body.parentComponent.id
    except Exception:                                   # no component to ask: the name alone tells the bodies of one component apart
        owner = ''
    return '%s|%s|%s|%d|%.6f|%.6f' % (owner, body.name, rev, body.faces.count, body.volume, body.area)


def gate_sign_step(body, state, job, reserve=1.5):
    """Add faces of a NATIVE body to a signature in progress, until every face is in or the time of the call is used up.

    state  {"n": faces of the body, "k": faces already in, "acc": hex of the running faces_sum}; None = start. A state
           made for another face count starts again.
    job    the job whose time budget holds; None = no limit, every face in this call.
    Returns the new state; the signature is complete when state["k"] == state["n"] (gate_sign_done). The clock is read
    every _GATE_SIG_STEP faces, and that many faces are added in every call, so a call always makes progress.
    Per face two values are read, the area and the centroid. The surface type is not read: a change that keeps the area
    and the centroid of every face to 0.01 and still alters the body is not an edit a person makes."""
    faces = body.faces
    n = faces.count
    if not isinstance(state, dict) or state.get('n') != n:
        state = {'n': n, 'k': 0, 'acc': '0'}
    k, acc = int(state['k']), int(str(state['acc']), 16)
    first = k
    while k < n:
        if job is not None and k > first and (k - first) % _GATE_SIG_STEP == 0 and gate_time_up(job, reserve):
            break
        f = faces.item(k)
        acc = (acc + face_digest(f.area * 100.0, gate_mm(f.centroid))) % SIG_MOD
        k += 1
    return {'n': n, 'k': k, 'acc': '%x' % acc}


def gate_sign_done(body, state):
    """The signature of a body whose faces are all in `state`: brep_signature_of() with the body's volume, area, counts
    and bounding box, in the component's own frame."""
    bb = body.boundingBox
    return brep_signature_of(body.volume * 1000.0, body.area * 100.0, (body.faces.count, body.edges.count, body.vertices.count),
                             (gate_mm(bb.minPoint), gate_mm(bb.maxPoint)), state['n'], int(str(state['acc']), 16))


def gate_remember_signature(body, sig):
    """Note the finished signature of this state of the body for the later units of this Fusion session."""
    key = _gate_sig_key(body)
    if key is not None:
        cache = _gate_cache()
        if len(cache.done) > 4000:
            cache.done.clear()
        cache.done[key] = sig
        cache.partial.pop(key, None)


def gate_signature(body, fresh=False):
    """The B-rep signature (brep_signature of _geom.py) of a NATIVE body: volume, area, counts, bounding box and every
    face's area and centroid, in the component's own frame.

    The loop over the faces costs seconds on a body with thousands of faces, and every call of every unit asks for the
    signature. So a finished signature is remembered for this state of the body (_gate_sig_key: the body's revision id)
    while Fusion runs, and the next unit gets it without a loop. The scan computes every signature from the faces and
    fills that memory.
    A body not seen in this state is signed here, inside the time of the call. When the time is used up first, the
    part done is kept and GateNotYet is raised: the unit answers "not done" and its next call goes on. The same is
    raised once when the signature is complete, was remembered, and less than half of the call is left after reading
    more than _GATE_SIG_STEP faces: the next call gets the signature from the memory and measures with all its time.
    fresh=True: never from the memory, every face in this call, no time limit. For the callers that must not rest on the
    revision id: the export, and the question "is the part still the geometry that was stamped"."""
    if fresh:
        return gate_sign_done(body, gate_sign_step(body, None, None))
    cache, key = _gate_cache(), _gate_sig_key(body)
    if key is not None and key in cache.done:
        return cache.done[key]
    slot = key or 'no revision id|%s|%s|%s' % (JOB.get('doc'), JOB.get('occurrence'), JOB.get('body'))
    was = cache.partial.get(slot)
    state = gate_sign_step(body, was, JOB, 3.0)
    if state['k'] < state['n']:
        if len(cache.partial) > 200:
            cache.partial.clear()
        cache.partial[slot] = state
        raise GateNotYet({'faces_done': state['k'], 'faces': state['n']})
    cache.partial.pop(slot, None)
    sig = gate_sign_done(body, state)
    gate_remember_signature(body, sig)
    added = state['k'] - (int(was['k']) if isinstance(was, dict) and was.get('n') == state['n'] else 0)
    if key is not None and added > _GATE_SIG_STEP and gate_left(JOB) < float(JOB.get('deadline_s') or 20) / 2.0:
        # The faces of a large body were read in this call and less than half of its time is left. The work that follows
        # does not look at the clock everywhere (the walk over the whole model, the look for screws). The signature is
        # remembered, so the unit says "not done" once more and its next call starts with all of its time.
        raise GateNotYet({'faces_done': state['n'], 'faces': state['n']})
    return sig


def gate_bbox(body_or_occ):
    """((x, y, z) min, (x, y, z) max) in mm of anything with a boundingBox, in the frame the object lives in."""
    bb = body_or_occ.boundingBox
    return gate_mm(bb.minPoint), gate_mm(bb.maxPoint)


def run(_context=''):
    """The entry point (called by the wrapper of build_script). Runs probe(JOB) and writes the envelope to JOB["out"].
    A call that starts after the host has stopped waiting for it (later than the budget + 10 s after it was sent) does
    no work: it writes an error envelope."""
    env = {'job': JOB.get('id'), 'doc': JOB.get('doc'), 'doc_version': None, 'part': JOB.get('part'), 'sig': None,
           'probe': JOB.get('probe'), 'facts': {}, 'done': False, 'cursor': JOB.get('cursor'), 'elapsed_s': 0.0, 'error': None,
           'open_documents': [], 'seen': {'doc': JOB.get('doc'), 'body': JOB.get('body'), 'occurrence': JOB.get('occurrence')}, 'notes': []}
    try:
        env['open_documents'] = sorted(_gate_doc_name(d) for d in adsk.core.Application.get().documents)
        late = _GATE_STARTED - _gate_t0()
        if late > float(JOB.get('deadline_s') or 20) + 10.0:
            raise TimeoutError('the unit started %.0f s after it was sent: the host has stopped waiting, nothing was measured' % late)
        env['doc_version'] = gate_doc_version(gate_document(JOB))
        out = probe(JOB) or {}
        env['facts'] = out.get('facts') or {}
        env['done'] = bool(out.get('done', True))
        env['cursor'] = out.get('cursor')
        env['sig'] = out.get('sig')
    except GateNotYet as e:                             # a large body: its signature needs one more call; nothing was measured yet
        env['facts'], env['done'], env['cursor'] = {'signing': e.args[0] if e.args else {}}, False, JOB.get('cursor')
    except Exception as e:
        env['error'] = '%s: %s\n%s' % (type(e).__name__, e, traceback.format_exc()[-1500:])
    env['notes'] = list(_GATE_NOTES)
    env['elapsed_s'] = round(time.time() - _GATE_STARTED, 2)
    tmp = JOB['out'] + '.tmp'
    os.makedirs(os.path.dirname(JOB['out']), exist_ok=True)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(env, f)
    os.replace(tmp, JOB['out'])
