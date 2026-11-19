"""Units of the gate. fusion_*.py are Fusion-side probes: their text is pasted into a script (gate_fusion_rpc.build_script)
and they are never imported on the host. _geom.py and _geom_rings.py are pure helpers, imported on the host and pasted
into those scripts. stl_walls.py, stl_mesh.py and project_consistency.py run on the host and are imported as
probes.<name> with tools/gate (or gate/tools) on sys.path."""
