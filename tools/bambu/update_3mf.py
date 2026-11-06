"""Swap changed meshes into a Bambu project (stdlib only); run it through with_bambu_closed.sh.
   python3 bambulab/tools/update_3mf.py <project.3mf> <stl-dir> [--plate PREFIX]
Objects whose name matches an STL (same name, or the part name before ' - ') get the new mesh if it differs;
position, rotation, per-object settings and pauses are kept. --plate limits it to plates whose name starts with PREFIX."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf
a = sys.argv[1:]; plate = ''
if '--plate' in a: i = a.index('--plate'); plate = a[i + 1]; del a[i:i + 2]
if plate and not bambu3mf.plates(bambu3mf.load(a[0])[1]['Metadata/model_settings.config'].decode(), plate): sys.exit('no plate named ' + plate)
changed = bambu3mf.update_meshes(a[0], a[1], plate)
print('updated:' if changed else 'nothing changed', *changed, sep='\n  ')
