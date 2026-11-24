"""Reader of the slicer's job cache (layer 0B of the print ledger). OFF by default.

It is switched on per machine in the git-ignored file bambulab/ledger/local.json ({"studio_cache": true}), never by a
committed switch. It reads a PRIVATE COPY of the application's URL cache database (mode 600, in a fresh temporary
folder that is deleted afterwards) and takes only: upload time, plate index, picture file, picture MD5.
Signed query strings, hosts, ids, serials, cookies and access codes are never stored and never printed.
Jobs go to the git-ignored bambulab/ledger/local/jobs.jsonl with their pictures. A job becomes a committed `job` event
only after it is tied to an object of THIS project; unrelated prints never enter the repository.

How nothing else gets out:
    the query string of each cached link is cut off inside the SQL statement, and only rows of plate pictures are
    selected, so no query string, and with it no signature, is ever handed to this code;
    of each cached response only two header values are taken, by name: Last-Modified and Etag. The rest of that
    object is dropped without being looked at;
    the two ids in the path are used only to tell jobs apart inside read_jobs() and are dropped there;
    what is stored is built from validated pieces: a time this module formatted, an integer, 32 hex characters of an
    MD5, and a file name made from that MD5. No text of the cache is ever copied into a file or a message;
    a picture is taken only when it is a PNG, and its file name can only be a plain name inside the cache folder;
    an error is kept as its type and a redacted text (LAST_ERROR); redact() is the last net, not the mechanism.
No link of the cache is ever opened: this module has no network code. It opens the cache database and the picture
files beside it, and no other file of the application.

The switch: bambulab/ledger/local.json, {"studio_cache": true, "quote": "<the owner's words>"}, written by switch()
(`ledger.py cache on --quote "..." [--db PATH]`). "cache_db" in that file names the database when it is not in the usual
place. A switch typed into the file without his words does not count. The reader also stays off when the switch or the
local job list is committed to git, or the switch is not git-ignored. read_jobs() itself looks at the switch: no caller
can read the cache on a machine that did not switch it on.

Limits to say out loud: the cache is not a history (the system may empty it, the application may stop writing it); a
job is in it only when the slicer's device page showed it, so the list is not complete; a sent job is not a kept part
(a job cancelled after two minutes looks the same)."""
import datetime, email.utils, glob, hashlib, json, os, plistlib, re, shutil, sqlite3, subprocess, sys, tempfile, time

SWITCH = 'studio_cache'
LAST_ERROR = None          # why the last read_jobs() gave nothing, already redacted; None when it read the database
PNG = b'\x89PNG\r\n\x1a\n'
MAX_PICTURE = 4 * 1024 * 1024
CONSENT = '\n'.join((
    'What the slicer cache reader does, on this machine only, with no network:',
    '  it copies the slicer\'s URL cache database into a fresh temporary folder (mode 600), reads the copy and deletes it;',
    '  that database holds download links to the plate pictures of jobs sent through the cloud. The links are signed and',
    '  stay valid for about a day. The reader never stores, prints or opens a link;',
    '  per job it takes four things: upload time, plate index, the plate picture and the MD5 of that picture;',
    '  it opens that database and the picture files beside it, and no other file of the slicer: no settings, no cookie store, no logs;',
    '  the jobs go to the git-ignored bambulab/ledger/local/. They are every job this computer\'s slicer showed, also those',
    '  of other projects: they stay local. A job enters the committed journal only when it is tied to an object of this project.'))

_PLATE = re.compile(r'/3mf/Metadata/plate_(\d{1,3})\.png$')
_MD5 = re.compile(r'^[0-9a-f]{32}$')
_FS_NAME = re.compile(r'^[A-Za-z0-9-]{8,64}$')
_SQL = ("SELECT CASE WHEN instr(r.request_key, '?') > 0 THEN substr(r.request_key, 1, instr(r.request_key, '?') - 1) ELSE r.request_key END, "
        "b.response_object, d.isDataOnFS, d.receiver_data "
        "FROM cfurl_cache_response r JOIN cfurl_cache_blob_data b ON b.entry_ID = r.entry_ID "
        "LEFT JOIN cfurl_cache_receiver_data d ON d.entry_ID = r.entry_ID "
        "WHERE r.request_key LIKE '%/3mf/Metadata/plate%' ORDER BY r.entry_ID")


def _ledger_dir(root):
    return os.path.join(root, 'bambulab', 'ledger')


def switch_file(root):
    """<root>/bambulab/ledger/local.json: the per-machine switch, git-ignored."""
    return os.path.join(_ledger_dir(root), 'local.json')


def _local(root):
    try:
        with open(switch_file(root), encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def enabled(root):
    """True only when bambulab/ledger/local.json exists, has "studio_cache": true and holds the owner's words ("quote"),
    as switch() writes it. A switch typed into the file by hand, without his words, does not count."""
    data = _local(root)
    return data.get(SWITCH) is True and isinstance(data.get('quote'), str) and bool(data['quote'].strip())


def _git(root, *args):
    """Exit code of a git command in the root, or None when git is missing or this is not a work tree."""
    try:
        r = subprocess.run(['git', '-C', root] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return None if r.returncode == 128 else r.returncode


def _git_trouble(root):
    """Why the local files are not private to this machine, or None. Only a git work tree can say."""
    if _git(root, 'rev-parse', '--is-inside-work-tree') != 0:
        return None
    for rel in ('bambulab/ledger/local.json', 'bambulab/ledger/local/jobs.jsonl'):
        if _git(root, 'ls-files', '--error-unmatch', rel) == 0:
            return '%s is committed to git: a committed switch or job list is refused (git rm --cached it)' % rel
    if _git(root, 'check-ignore', '-q', 'bambulab/ledger/local.json') != 0:
        return 'bambulab/ledger/local.json is not git-ignored here (`ledger.py init` adds the line to .gitignore)'
    return None


def why_off(root):
    """None when the reader may run on this machine, else the reason as one line: the switch is not set, or the local
    files are not private (committed, or not git-ignored)."""
    if not enabled(root):
        if _local(root).get(SWITCH) is True:
            return 'bambulab/ledger/local.json has "%s": true without the owner\'s words: it was not written by `ledger.py cache on --quote "..."`' % SWITCH
        return 'bambulab/ledger/local.json does not switch it on ("%s": true)' % SWITCH
    return _git_trouble(root)


def switch(root, on, quote=None, cache_db=None):
    """Set the per-machine switch. Switching on needs the owner's yes as `quote` (kept in the local file only) and a
    local.json that git ignores. cache_db: the database to read when it is not in the usual place (kept in the local
    file as "cache_db"; None leaves what is there). Returns the lines to show. ValueError when it may not be switched on."""
    path = switch_file(root)
    data = _local(root)
    if not on:
        if not os.path.exists(path):
            return ['the slicer cache reader is off on this machine (it was never on)']
        data[SWITCH] = False
        data['at'] = _now()
        _write_private(path, json.dumps(data, indent=1, ensure_ascii=False) + '\n')
        return ['the slicer cache reader is now OFF on this machine. What it read stays in bambulab/ledger/local/ until you delete it']
    if not (quote or '').strip():
        raise ValueError('switching the cache reader on needs the owner\'s yes: --quote "<his words>". Show him this first:\n' + CONSENT)
    trouble = _git_trouble(root)
    if trouble:
        raise ValueError('not switched on: ' + trouble)
    data.update({SWITCH: True, 'quote': quote.strip(), 'at': _now()})
    if cache_db:
        data['cache_db'] = str(cache_db)
    os.makedirs(_ledger_dir(root), exist_ok=True)
    _write_private(path, json.dumps(data, indent=1, ensure_ascii=False) + '\n')
    return [CONSENT, 'the slicer cache reader is now ON on this machine only (bambulab/ledger/local.json, never committed)']


def cache_db_path():
    """Path of the slicer's URL cache database on this machine, or None when it is not found."""
    if sys.platform != 'darwin':
        return None
    p = os.path.join(os.path.expanduser('~'), 'Library', 'Caches', 'com.bambulab.bambu-studio', 'Cache.db')
    return p if os.path.isfile(p) else None


_HOME = re.compile('/(?:' + 'Users' + '|' + 'home' + r')/[^/\s"\']+')
_RULES = (
    (re.compile(r'(?i)\b(?:https?|wss?|ftps?|mqtts?|rtsps?)://[^\s"\'<>]+'), '<link>'),
    (re.compile(r'(?i)\b(?:set-)?cookies?\b\s*[:=][^\n]*'), '<cookie>'),
    (re.compile(r'(?i)\b((?:x-amz-[a-z-]+|access[_ -]?code|passw(?:or)?d|secret|token|signature|credential|authorization|bearer|cookie|serial(?:[_ -]?number)?|sn|dev[_ -]?id|device[_ -]?id|user[_ -]?id|uid|account)\b\s*["\']?\s*[:=]\s*)["\']?[^\s"\'&,;]+'), r'\1<removed>'),
    (re.compile(r'\?[\w.%~+-]+=[^\s"\'<>]*'), ''),
    (re.compile(r'(?i)\b(?:[a-z0-9-]+\.){2,}[a-z]{2,}\b(?::\d+)?'), '<host>'),
    (re.compile(r'(?i)\b[a-z0-9-]+\.(?:com|net|org|io|cn|co|dev|app|cloud|info|me|tv|local|lan|internal|invalid|example)\b(?::\d+)?'), '<host>'),
    (re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b(?::\d+)?'), '<host>'),
    (re.compile(r'\b[0-9a-fA-F]{16,}\b'), '<id>'),
    (re.compile(r'\b\d{8,}\b'), '<id>'),
    (re.compile(r'\b(?=[A-Za-z0-9]*\d[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,}\b'), '<id>'),
    (re.compile(r'(?<![A-Za-z0-9+/_=-])(?=[A-Za-z0-9+/_=-]*\d)(?=[A-Za-z0-9+/_=-]*[A-Za-z])[A-Za-z0-9+/_=-]{24,}(?![A-Za-z0-9+/_=-])'), '<id>'),
)


def redact(text):
    """`text` with everything that could identify an account or a printer removed: query strings, host names, long
    hex or base64 ids, serial-like tokens. What is left is safe to print.

    Also removed: whole links, cookie lines, the value after a name such as access_code / token / serial / password,
    and the user's folder in a home path. A SHORT secret with no name in front of it has no shape a pattern could
    know: this is the last net for free text (an error message), never the reason a value may be passed on."""
    text = str(text)
    home = os.path.expanduser('~')
    if home and home != '~' and len(home) > 3:
        text = text.replace(home, '~')
    text = _HOME.sub('~', text)
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _write_private(path, text):
    """Write a file that only this user can read."""
    tmp = '%s.tmp%d' % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        os.write(fd, text if isinstance(text, bytes) else text.encode('utf-8'))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _private_copy(src, dst):
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as out, open(src, 'rb') as f:
        shutil.copyfileobj(f, out, 1024 * 1024)


def _md5(data):
    try:
        return hashlib.md5(data, usedforsecurity=False).hexdigest()
    except TypeError:
        return hashlib.md5(data).hexdigest()


def _headers(blob):
    """(Last-Modified as a UTC datetime or None, Etag as 32 hex or None) of one cached response. Nothing else is read."""
    try:
        tree = plistlib.loads(bytes(blob))
    except Exception:
        return None, None
    stack, when, etag = [tree], None, None
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                name = k.lower() if isinstance(k, str) else ''
                if name == 'last-modified' and isinstance(v, str) and when is None:
                    try:
                        t = email.utils.parsedate_to_datetime(v)
                        when = (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).astimezone(datetime.timezone.utc)
                    except (TypeError, ValueError, IndexError):
                        pass
                elif name == 'etag' and isinstance(v, str) and etag is None:
                    e = v.strip().strip('"').lower()
                    etag = e if _MD5.match(e) else None
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(x for x in node if isinstance(x, (dict, list)))
    return when, etag


def _picture(on_fs, data, fs_dir):
    """The bytes of a cached plate picture, or None: only a PNG of a sane size is ever returned."""
    try:
        if on_fs:
            name = data.decode('ascii') if isinstance(data, (bytes, bytearray, memoryview)) else str(data or '')
            if not _FS_NAME.match(name):
                return None
            path = os.path.join(fs_dir, name)
            if not os.path.isfile(path) or os.path.getsize(path) > MAX_PICTURE:
                return None
            with open(path, 'rb') as f:
                data = f.read()
        data = bytes(data) if data is not None else b''
    except (IOError, OSError, UnicodeError, ValueError, TypeError):
        return None
    return data if data.startswith(PNG) and len(data) <= MAX_PICTURE else None


def _sweep():
    """Remove private copies a killed run left behind (they hold links that stay valid for about a day)."""
    for d in glob.glob(os.path.join(tempfile.gettempdir(), 'ledger-cache-*')):
        try:
            if os.path.isdir(d) and time.time() - os.stat(d).st_mtime > 600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _read_jobs(db_path):
    _sweep()
    tmp = tempfile.mkdtemp(prefix='ledger-cache-')            # a fresh folder, mode 700
    try:
        copy = os.path.join(tmp, 'cache.db')
        for suffix in ('', '-wal', '-shm'):                   # the database is in WAL mode and in use: without the
            if os.path.isfile(db_path + suffix):              # -wal file the newest jobs are missing
                _private_copy(db_path + suffix, copy + suffix)
        conn = sqlite3.connect(copy)
        try:
            rows = conn.execute(_SQL).fetchall()
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    fs_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'fsCachedData')
    jobs = {}
    for path, blob, on_fs, data in rows:
        path = str(path or '').split('#', 1)[0]
        m = _PLATE.search(path)
        if not m:
            continue
        when, etag = _headers(blob)
        if when is None:
            continue
        key = hashlib.sha1(path.encode('utf-8', 'replace')).hexdigest()       # tells jobs apart; never leaves this function
        job = jobs.setdefault(key, {'sent_at': when.strftime('%Y-%m-%dT%H:%M:%SZ'), 'plate_index': int(m.group(1)), 'thumb': None, 'thumb_md5': etag})
        if job['thumb'] is None:
            pic = _picture(on_fs, data, fs_dir)
            if pic is not None:
                md5 = _md5(pic)
                if job['thumb_md5'] in (None, md5):
                    job['thumb'], job['thumb_md5'] = pic, md5
    out, seen = [], set()
    for job in sorted(jobs.values(), key=lambda j: (j['sent_at'], j['plate_index'], j['thumb_md5'] or '')):
        k = (job['sent_at'], job['plate_index'], job['thumb_md5'])
        if k not in seen:
            seen.add(k)
            out.append(job)
    return out


def read_jobs(root, db_path=None):
    """Read jobs from a private copy of the slicer's cache database, for the project at `root`. REFUSES, and opens
    nothing, unless this machine switched the reader on for that project (why_off(root) is None): then it returns []
    and LAST_ERROR says why. db_path names the database; None = source(root).
    Returns [{"sent_at": UTC time, "plate_index": n, "thumb": bytes of the plate picture or None, "thumb_md5": hex or
    None}], oldest first. Nothing else leaves this function. Never raises for a database it cannot read: returns []."""
    global LAST_ERROR
    LAST_ERROR = None
    try:
        off = why_off(root)
    except Exception as e:                                   # what cannot say it is on is off
        off = 'the switch could not be read (%s)' % type(e).__name__
    if off:
        LAST_ERROR = 'off: ' + off
        return []
    try:
        db_path = db_path or source(root)
        if not db_path or not os.path.isfile(db_path):
            LAST_ERROR = 'no database file'
            return []
        return _read_jobs(db_path)
    except Exception as e:
        LAST_ERROR = redact('%s: %s' % (type(e).__name__, e))[:200]       # the text of an error can hold a link: never raw
        return []


def _local_dir(root):
    d = os.path.join(_ledger_dir(root), 'local')
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    if not os.path.exists(os.path.join(d, '.gitignore')):       # nothing in this folder can be committed
        with open(os.path.join(d, '.gitignore'), 'w') as f:
            f.write('*\n')
    return d


def jobs_file(root):
    """<root>/bambulab/ledger/local/jobs.jsonl."""
    return os.path.join(_ledger_dir(root), 'local', 'jobs.jsonl')


def _stored(root):
    out = []
    try:
        with open(jobs_file(root), encoding='utf-8') as f:
            for line in f:
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if isinstance(j, dict) and j.get('id') and j.get('sent_at'):
                    out.append(j)
    except (IOError, OSError):
        pass
    return out


def source(root):
    """The database sync() would read on this machine: the one local.json names as "cache_db", else cache_db_path()."""
    return _local(root).get('cache_db') or cache_db_path()


def sync(root, db_path=None):
    """When enabled(root): read_jobs() of the machine's cache, append the new ones to bambulab/ledger/local/jobs.jsonl and
    store their pictures under bambulab/ledger/local/thumbs/<md5>.png. Returns the number of new jobs; 0 when off.

    db_path names another database (tests; a second location); local.json may name one as "cache_db"."""
    if why_off(root):
        return 0
    db = db_path or source(root)
    if not db:
        return 0
    jobs = read_jobs(root, db)
    if not jobs:
        return 0
    d = _local_dir(root)
    have = _stored(root)
    known = {(j.get('sent_at'), j.get('plate_index'), j.get('thumb_md5')) for j in have}
    ids = {j['id'] for j in have}
    lines, n = [], 0
    for j in jobs:
        if (j['sent_at'], j['plate_index'], j['thumb_md5']) in known:
            continue
        base = 'J-%s-%s-p%d' % (j['sent_at'][:10].replace('-', ''), j['sent_at'][11:16].replace(':', ''), j['plate_index'])
        jid, k = base, 1
        while jid in ids:
            k += 1
            jid = '%s-%d' % (base, k)
        ids.add(jid)
        thumb = None
        if j['thumb'] is not None and j['thumb_md5'] and _MD5.match(j['thumb_md5']):
            os.makedirs(os.path.join(d, 'thumbs'), exist_ok=True)
            dst = os.path.join(d, 'thumbs', j['thumb_md5'] + '.png')
            if not os.path.exists(dst):
                _write_private(dst, j['thumb'])
            thumb = 'bambulab/ledger/local/thumbs/%s.png' % j['thumb_md5']
        lines.append(json.dumps({'id': jid, 'source': 'studio_cache', 'sent_at': j['sent_at'], 'plate_index': j['plate_index'],
                                 'thumb_md5': j['thumb_md5'], 'thumb': thumb, 'seen': _now()}))
        n += 1
    if lines:
        fd = os.open(jobs_file(root), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            os.write(fd, ('\n'.join(lines) + '\n').encode('utf-8'))
        finally:
            os.close(fd)
    return n


def local_jobs(root):
    """The jobs of bambulab/ledger/local/jobs.jsonl as a list; [] when there are none or the reader is off."""
    if why_off(root):
        return []
    return sorted(_stored(root), key=lambda j: (j.get('sent_at', ''), j.get('plate_index') or 0))
