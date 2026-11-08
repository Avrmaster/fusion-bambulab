"""Bring every CAM operation and NC program of the active Fusion document up to date. Runs inside Fusion through the MCP.

Run through fusion_mcp_execute (featureType script) with this wrapper:
    P = r'<project>/fusion/tools/cam_refresh.py'
    g = {'__file__': P}; exec(open(P, encoding='utf-8').read(), g); run = g['run']      # or g['status'] with readOnly true

status: read-only. Every setup, operation (toolpath valid or not, errors, warnings) and NC program (operations, output
        file, Safe Retracts). Ends 'OUT OF DATE n'.
run:    one call per step, call again until it prints DONE (each call returns within BUDGET s, so Fusion never blocks):
          1. design.computeAll(), then regenerate every operation whose toolpath is not valid
          2. every NC program: Safe Retracts = clearanceHeight, open-in-editor off, post; the file must be newer than the start
          3. nc_check.py on each posted file when the project has cnc/nc_check.py; an error stops before the save
          4. doc.save(DESCRIPTION)
        A model edit invalidates operations in programs you did not touch, so this always posts every program.
Never edit the posted G-code: change the operation in Fusion and run this again.
Scripts that change the document fail while a command dialog (Simulate, an operation editor) is open: ask to close it."""
import adsk.core, adsk.fusion, adsk.cam, os, sys, time, glob, types, importlib.util
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NC_CHECK = os.path.join(REPO, 'cnc', 'nc_check.py')
BUDGET = 40.0
SAFE = 'clearanceHeight'
DESCRIPTION = 'cam_refresh: regenerate and post all'
# the wrapper execs this file afresh on every call, so the progress lives in a module that stays in Fusion's Python
STATE = sys.modules.setdefault('_cam_refresh_state', types.ModuleType('_cam_refresh_state')).__dict__.setdefault('state', {})


def product(doc, kind):
    for i in range(doc.products.count):
        p = doc.products.item(i)
        if p.productType == kind: return p
    return None


def cam_of(doc):
    cam = adsk.cam.CAM.cast(product(doc, 'CAMProductType'))
    if not cam: raise RuntimeError('%s has no Manufacture data' % doc.name)
    return cam


def param(obj, name):
    try: return obj.parameters.itemByName(name)
    except Exception: return None


def out_file(prog):
    folder, name = param(prog, 'nc_program_output_folder'), param(prog, 'nc_program_filename')
    if not folder or not name: return None, None
    folder, name = folder.value.value, name.value.value
    hits = sorted(glob.glob(os.path.join(folder, glob.escape(name) + '.*')), key=os.path.getmtime)
    hits = [h for h in hits if not h.endswith(('.html', '.log'))]
    return folder, (hits[-1] if hits else None)


def stale_ops(cam):
    return [op for op in cam.allOperations if not op.isSuppressed and (not op.hasToolpath or not op.isToolpathValid)]


def status():
    doc = adsk.core.Application.get().activeDocument; cam = cam_of(doc)
    print('%s%s' % (doc.name, ' (unsaved changes)' if doc.isModified else ''))
    for setup in cam.setups:
        print('setup', setup.name)
        for op in setup.allOperations:
            flags = [] if op.hasToolpath and op.isToolpathValid else ['OUT OF DATE']
            if op.isSuppressed: flags = ['suppressed']
            if op.hasError: flags.append('ERROR')
            if op.hasWarning: flags.append('warning')
            print('  %-28s %s' % (op.name, ' '.join(flags) or 'ok'))
    for prog in cam.ncPrograms:
        try: safe = prog.postParameters.itemByName('safePositionMethod').value.value
        except Exception: safe = '?'
        _, f = out_file(prog)
        print('program %-24s ops %d  safe %s  file %s' % (prog.name, prog.operations.count, safe, f))
    print('OUT OF DATE %d' % len(stale_ops(cam)))


def run(_context=None):
    try: _run()
    except Exception:
        STATE.clear(); raise


def _run():
    t0 = time.time(); app = adsk.core.Application.get(); doc = app.activeDocument; cam = cam_of(doc)
    if STATE.get('doc') != doc.name or time.time() - STATE.get('start', 0) > 3600:
        STATE.clear(); STATE.update(doc=doc.name, start=time.time(), step='generate')
    if STATE['step'] == 'generate':
        if 'future' not in STATE:
            des = adsk.fusion.Design.cast(product(doc, 'DesignProductType'))
            if des: des.computeAll()
            todo = stale_ops(cam)
            print('regenerating %d operation(s)' % len(todo))
            STATE['future'] = cam.generateAllToolpaths(True) if todo else None
        fut = STATE['future']
        while fut and not fut.isGenerationCompleted:
            if time.time() - t0 > BUDGET:
                print('GENERATING %d/%d, call again' % (fut.numberOfCompleted, fut.numberOfOperations)); return
            adsk.doEvents(); time.sleep(0.2)
        bad = [op.name for op in cam.allOperations if op.hasError]
        left = [op.name for op in stale_ops(cam)]
        if bad or left: raise RuntimeError('not generated: errors %s, still out of date %s' % (bad, left))
        STATE['step'] = 'post'; STATE.pop('future')
    if STATE['step'] == 'post':
        posted = []
        for prog in cam.ncPrograms:
            pp = prog.postParameters
            sp = pp.itemByName('safePositionMethod')
            if sp and sp.value.value != SAFE:
                sp.value.value = SAFE; prog.updatePostParameters(pp); print('%s: Safe Retracts -> %s' % (prog.name, SAFE))
            ed = param(prog, 'nc_program_openInEditor')
            if ed and ed.value.value: ed.value.value = False
            if not prog.postProcess(adsk.cam.NCProgramPostProcessOptions.create()):
                raise RuntimeError('%s: post failed' % prog.name)
            _, f = out_file(prog)
            if not f or os.path.getmtime(f) < STATE['start'] - 1: raise RuntimeError('%s: no new file in its output folder' % prog.name)
            posted.append(f); print('posted', f)
        STATE['posted'] = posted; STATE['step'] = 'check'
    if STATE['step'] == 'check':
        if os.path.exists(NC_CHECK):
            spec = importlib.util.spec_from_file_location('nc_check', NC_CHECK); nc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(nc)
            failed = False
            for f in STATE['posted']:
                r = nc.check(open(f, encoding='utf-8', errors='replace').read())
                print('  %s: %s' % (os.path.basename(f), nc.summary(r)))
                for e in r['errors']: print('    ERROR', e); failed = True
                for w in r['warnings']: print('    warn ', w)
            if failed:
                raise RuntimeError('posted G-code has errors: fix the operations in Fusion, then run again (not saved)')
        STATE['step'] = 'save'
    if STATE['step'] == 'save':
        if not doc.save(DESCRIPTION): raise RuntimeError('save failed')
        left = len(stale_ops(cam)); STATE.clear()
        print('saved; OUT OF DATE %d' % left)
        print('DONE' if left == 0 else 'NOT DONE: something went out of date after the save, run again')
