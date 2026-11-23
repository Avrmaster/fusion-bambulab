import os
import sys, json, glob, os, time
M=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(M)),'tools','fusion')); import fusion_rpc
R=os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
parts=json.load(open(R+'/fusion/parts.json'))['parts']
todo=[]
for f in sorted(glob.glob(R+'/gate/results/heatset/*.json')):
    if os.path.basename(f).startswith('_'): continue
    r=json.load(open(f))
    for p in r['pocket_list']: todo.append({'part':r['part'],'at':p['at'],'out':p['out']})
src=open(M+'/audit_job.py').read(); rows=[]; t=time.time()
while len(rows)<len(todo):
    res=fusion_rpc.run_job(src,{'doc':json.load(open(R+'/fusion/parts.json'))['doc'],'parts':parts,'pockets':todo[len(rows):],'budget':40},timeout=200)
    rows+=res['rows']; print(len(rows),'/',len(todo), round(time.time()-t,1),'s', flush=True)
json.dump(rows,open(os.path.join(R,'gate','results','heatset','_audit_rows.json'),'w'))
print('done')
