"""Build cnc/station/jobs.js from cnc/jobs/*/job.json for the operator page CNC.html.

cnc/station.json: {"machine": "<machine> · gSender", "lang": "en" | "uk", "root": "C:\\\\cnc"}
job.json: title, status (ready | draft | later | done), summary, facts [[label, value]], image (file in the job folder),
files [{name, tool, time, allow}], steps [text | {text, note, danger}]. The id is the folder name.
Text fields take **bold** and `code`. rev changes whenever any file in the job folder changes, so ticked steps reset
on a new version of the job.

Every listed G-code file goes through nc_check.py. Its depths, height and time go on the page next to the file, read from
the file itself, and a file with an error (G28, G53, a rapid sideways below Z0, ...) stops the build. A job may accept a
code with "allow": ["G53"] on that file.
"""
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CNC = os.path.dirname(HERE)
JOBS = os.path.join(CNC, 'jobs')
sys.path.insert(0, CNC)
import nc_check  # noqa: E402

DEFAULTS = {'machine': 'CNC', 'lang': 'en', 'root': 'C:\\cnc'}


def station():
    f = os.path.join(CNC, 'station.json')
    return dict(DEFAULTS, **(json.load(open(f, encoding='utf-8')) if os.path.isfile(f) else {}))


def rev(folder):
    h = hashlib.sha1()
    for n in sorted(os.listdir(folder)):
        p = os.path.join(folder, n)
        if os.path.isfile(p):
            h.update(n.encode('utf-8'))
            h.update(open(p, 'rb').read())
    return h.hexdigest()[:10]


def build():
    jobs, problems = [], []
    for d in sorted(os.listdir(JOBS)) if os.path.isdir(JOBS) else []:
        f = os.path.join(JOBS, d, 'job.json')
        if not os.path.isfile(f):
            continue
        j = json.load(open(f, encoding='utf-8'))
        j['id'] = d
        j['rev'] = rev(os.path.join(JOBS, d))
        for x in j.get('files', []):
            p = os.path.join(JOBS, d, x['name'])
            if not os.path.isfile(p):
                problems.append('%s: missing %s' % (d, x['name'])); continue
            r = nc_check.check(open(p, encoding='utf-8', errors='replace').read(), allow=[a.upper() for a in x.get('allow', [])])
            x['check'] = {'cut': r['cut'], 'top': r['all'].get('Z', [None, None])[1], 'minutes': r['minutes'],
                          'tools': r['tools'], 'warnings': r['warnings']}
            problems += ['%s/%s: %s' % (d, x['name'], e) for e in r['errors']]
        jobs.append(j)
    if problems:
        raise SystemExit('not built:\n  ' + '\n  '.join(problems))
    order = {'ready': 0, 'draft': 1, 'later': 2, 'done': 3}
    jobs.sort(key=lambda j: (order.get(j.get('status'), 3), j['id']))
    version = hashlib.sha1(json.dumps(jobs, sort_keys=True).encode('utf-8')).hexdigest()[:10]
    data = dict(station(), version=version, built=time.strftime('%d.%m %H:%M'), jobs=jobs)
    with open(os.path.join(HERE, 'jobs.js'), 'w', encoding='utf-8') as out:
        out.write('window.CNC_DATA = ' + json.dumps(data, ensure_ascii=False, indent=1) + ';\n')
    return data


if __name__ == '__main__':
    d = build()
    print('jobs.js: %d job(s), version %s' % (len(d['jobs']), d['version']))
    for j in d['jobs']:
        for x in j.get('files', []):
            c = x['check']
            print('  %s/%s: cuts down to Z %s, rises to Z %s, ≈ %s min%s' % (j['id'], x['name'], (c['cut'].get('Z') or ['?'])[0], c['top'],
                                                          c['minutes'], ''.join('\n    warn ' + w for w in c['warnings'])))
