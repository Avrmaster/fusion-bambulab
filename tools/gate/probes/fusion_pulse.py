"""The pulse of one document: one digest that changes whenever anything a scan reads could have changed, read without
the faces of any body. The host runs it before a scan and skips the scan when the pulse is the one of the last scan.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: nothing is opened, saved or changed.

What goes in (gate_pulse of _fusion.py): every occurrence with its full path, component id and name and its transform
(to 0.0001 mm); every solid body with its name and _gate_sig_key (revision id, face count, volume, area: the key the
signature memory of every unit already trusts); every mesh body with its name, node coordinates and triangle indices
(all the scan's "shape" of a mesh digests: fusion_openings and fusion_inserts read the triangles); the documents this one
links, with the version each uses and whether it is out of date. Not the light bulbs: what the gate measures does not
depend on them.
Why not the saved version of the document: an edit that is not saved does not change it.
When a body gives no revision id the pulse is None: nothing can vouch for it, and the host scans in full."""


def probe(job):
    """job  uses "doc", "deadline_s"

    Returns {"facts": {"pulse": 16 hex or None, "bodies": n, "why": text or None}, "done": True, "cursor": None, "sig": None}.
    "why" says why the pulse is None (a body with no revision id, the time of the call used up)."""
    design = gate_design(job)
    pulse, n, why = gate_pulse(design, job, gate_document(job))
    return {'facts': {'pulse': pulse, 'bodies': n, 'why': why}, 'done': True, 'cursor': None, 'sig': None}
