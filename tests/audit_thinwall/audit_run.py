"""Run the Fusion side of the thin-wall audit on every part of a project, in calls of at most ~40 s, and keep the
rows in OUT after every call. AUDIT_FACES=<audit_faces.py result> lets it skip pairs of two tooth faces (a second run resumes there; default: audit_rows.json in the current folder; not a gate result, keep it out of the project).

    python3 tests/audit_thinwall/audit_run.py <project> [PART ...] [--out OUT]"""
import sys, json, os, time
M = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(M)), 'tools', 'fusion')); import fusion_rpc
R = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
pj = json.load(open(os.path.join(R, 'fusion', 'parts.json')))
args = sys.argv[2:]
out = args[args.index('--out') + 1] if '--out' in args else 'audit_rows.json'
args = [a for k, a in enumerate(args) if a != '--out' and (k == 0 or args[k - 1] != '--out')]
names = args or list(pj['parts'])
faces_p = args_faces = None
sys.path.insert(0, M); import audit_judge
rules = json.load(open(os.path.join(os.path.dirname(os.path.dirname(M)), 'rules', 'thinwall.json')))
faces = json.load(open(os.environ['AUDIT_FACES'])) if os.environ.get('AUDIT_FACES') else {}
parts = [{'name': n, 'component': pj['parts'][n]['component'], 'body': pj['parts'][n]['body'], 'tmax': 1.6,
          'teeth': sorted(i for i in audit_judge.tooth_faces(faces[n], rules) if not faces[n][i].get('hole')) if n in faces else []} for n in names]
src = open(os.path.join(M, 'audit_job.py')).read()
state = json.load(open(out)) if os.path.exists(out) else {'rows': [], 'start': [0, 0], 'names': names}
if state.get('names') != names:
    state = {'rows': [], 'start': [0, 0], 'names': names}
rows, start, t = state['rows'], state['start'], time.time()
while True:
    for attempt in range(6):                          # Fusion sometimes answers 'count > 0' while another script runs: wait, retry
        try:
            res = fusion_rpc.run_job(src, {'doc': pj['doc'], 'parts': parts, 'start': start, 'budget': 40}, timeout=200, name='audit_thinwall')
            break
        except fusion_rpc.FusionError as e:
            print('retry after: %s' % str(e).splitlines()[0], flush=True); time.sleep(10)
    else:
        sys.exit('Fusion kept failing')
    rows += res['rows']; start = res['next']
    json.dump({'rows': rows, 'start': start, 'names': names, 'done': res['done']}, open(out, 'w'))
    print('part %d/%d, pair %d of %s (setup %s s), rows %d, %.0f s' % (start[0], len(parts), start[1], res.get('pairs'), res.get('setup_s'), len(rows), time.time() - t), flush=True)
    if res['done']:
        break
print('done')
