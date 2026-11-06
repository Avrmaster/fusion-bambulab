"""Bambu Studio project for {{NAME}}: bambulab/{{PROJ}}.
Build: python3 bambulab/tools/with_bambu_closed.py bambulab/{{PROJ}} python3 bambulab/build_3mf.py
(for the very first build, while the project does not exist yet: python3 bambulab/build_3mf.py)
The first run creates the project from bambulab/template.3mf. Later runs sync it: new meshes go in place and whatever was
changed by hand in Bambu Studio stays. See the bambu-project skill."""
import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'tools')); import bambu3mf, sync3mf
PROJ, TEMPLATE, STL = os.path.join(ROOT, '{{PROJ}}'), os.path.join(ROOT, 'template.3mf'), os.path.join(ROOT, '..', 'stl')
TITLE = '{{NAME}}'
FILAMENT, COLOURS = None, None     # e.g. 'PETG Basic', ['#1A1A1A']: one template filament, one slot per colour; None keeps the template's

# (plate name, [(object name, STL, the model face that goes on the bed[, {per-object exceptions}])])
# face: False as modelled, '+x' '-x' '+y' '-y' '+z'; add ',z45' to turn it on the bed. A comment on each part says why that face.
PLATES = [
    # ('[TODO] bracket', [('Bracket', 'bracket.stl', '-z')]),
]
# Everything that is the same on every part goes here, never per object.
PROJECT = {'enable_support': '1', 'support_type': 'tree(auto)', 'support_on_build_plate_only': '0', 'support_remove_small_overhang': '0'}

if not PLATES: sys.exit('PLATES is empty: add the parts first')
if os.path.exists(PROJ):
    if bambu3mf.studio_running(): sys.exit('Bambu Studio is open: run this through bambulab/tools/with_bambu_closed.py')
    bambu3mf.backup(PROJ)     # bambulab/.backups keeps the last 10
    rep = sync3mf.sync(PROJ, PLATES, stl_dir=STL, project=PROJECT, title=TITLE)
    sync3mf.print_report(os.path.basename(PROJ), rep); sys.exit(bool(rep.get('refused')))
else:
    bambu3mf.new_project(TEMPLATE, PROJ, FILAMENT, COLOURS)
    bambu3mf.build(PROJ, STL, PLATES, project=PROJECT, title=TITLE)
