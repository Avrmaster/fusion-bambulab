"""Set per-object slicer settings in a Bambu project (stdlib only); run it through with_bambu_closed.sh.
   python3 bambulab/tools/set_3mf_setting.py <project.3mf> "<name prefix>" key=value … [--plate PREFIX | --plate-exact NAME]
"" matches every object; --plate limits it to plates whose name starts with PREFIX, --plate-exact to the plate named NAME (default: every plate)."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf
a = sys.argv[1:]; plate = ''; exact = False
for opt in ('--plate', '--plate-exact'):
    if opt in a: i = a.index(opt); plate = a[i + 1]; exact = opt == '--plate-exact'; del a[i:i + 2]
proj, prefix, kvs = a[0], a[1], [x.split('=', 1) for x in a[2:]]
hit = bambu3mf.set_settings(proj, prefix, kvs, plate, exact)
if not hit: sys.exit('no object%s starts with %r' % (' on plates ' + repr(plate) if plate else '', prefix))
print('set', ' '.join('='.join(kv) for kv in kvs), 'on:', *hit, sep='\n  ')
