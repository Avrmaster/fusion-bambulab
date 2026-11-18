#!/usr/bin/env python3
"""Find and close open edges in the meshes of a Bambu Studio project, in place: nothing moves, no setting changes.
    python3 bambulab/tools/weld_3mf.py <project.3mf> [--check]
Bambu Studio shows "Info: N open edges" on an object whose mesh has two vertices on the same spot (a seam that was never
welded). This merges such vertices (within 1e-7 mm) in every mesh that has open edges, and writes a mesh only if that closes
all of them and keeps the triangle count. --check only reports and exits 1 when a mesh has open edges.
Refuses to write while Bambu Studio runs: wrap it in with_bambu_closed.py."""
import os, re, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf
GUARD = True      # tests switch it off

def run(proj, check=False):
    infos, files = bambu3mf.load(proj); cfg = files['Metadata/model_settings.config'].decode()
    part_name = {}
    for oid, body in re.findall(r'<object id="(\d+)">(.*?)</object>', cfg, re.S):
        name = re.search(r'<metadata key="name" value="([^"]*)"', body).group(1)
        for pid in re.findall(r'<part id="(\d+)"', body): part_name[pid] = name
    fixed, left = [], []
    for path in sorted(n for n in files if n.startswith('3D/') and n.endswith('.model')):
        om = files[path].decode(); out = []; pos = 0
        for m in re.finditer(r'(<object id="(\d+)"[^>]*>\s*<mesh>)(.*?)(</mesh>)', om, re.S):
            V = [tuple(float(x) for x in v) for v in re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', m.group(3))]
            F = [tuple(int(x) for x in t) for t in re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', m.group(3))]
            n0 = bambu3mf.open_edges(F)
            if not n0: continue
            name = part_name.get(m.group(2), 'object %s' % m.group(2))
            W, G = bambu3mf.weld(V, F); n1 = bambu3mf.open_edges(G)
            if n1 == 0 and len(G) == len(F) and not check:
                out.append(om[pos:m.start(3)] + bambu3mf.mesh_xml(W, G)); pos = m.end(3); fixed.append((name, n0, len(V) - len(W)))
            else: left.append((name, n0, n1, len(F) - len(G)))
        if out: files[path] = (''.join(out) + om[pos:]).encode()
    for name, n0, k in fixed: print('welded  %-50s open edges %d -> 0 (%d vertices merged)' % (name, n0, k))
    for name, n0, n1, k in left:
        print('OPEN    %-50s open edges %d%s' % (name, n0, '' if check else ' -> %d after welding, %d triangles collapse: not written' % (n1, k)))
    if not fixed and not left: print('no open edges in', proj)
    if fixed:
        if GUARD and bambu3mf.studio_running(): sys.exit('Bambu Studio is running: nothing written (use with_bambu_closed.py)')
        bambu3mf.backup(proj); bambu3mf.save(proj, files, infos, zipfile.ZIP_STORED)
    return 1 if left else 0

if __name__ == '__main__':
    sys.exit(run(sys.argv[1], '--check' in sys.argv))
