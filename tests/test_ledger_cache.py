"""The slicer job cache reader of the print ledger: off unless this machine switched it on, a private copy, four fields,
and nothing that identifies an account or a printer in any output or stored file.
The cache database is synthetic and built here with sqlite3 and plistlib; every "secret" in it is invented."""
import contextlib, hashlib, io, json, os, plistlib, shutil, sqlite3, stat, subprocess, sys, tempfile, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'ledger'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger, ledger_cache, ledger_mesh
from test_ledger import write_stl, block, Clock, BRACKET, BRACKET_HOLE_MOVED

# planted fakes: none of these may reach an output, a return value or a stored file
HOST = 'jobs.storage.fake-printer-cloud.example'
MODEL_ID, JOB_ID, OTHER_JOB_ID = 'US7f3a9c1e5b2d4f60', '5550001234', '5550009876'
SIGNATURE = 'fa4e51a9d0c3b7e2fa4e51a9d0c3b7e2fa4e51a9d0c3b7e2fa4e51a9d0c3b7e2'
CREDENTIAL = 'FAKEKEYID0000EXAMPLE'
ACCESS_CODE = '27182818'
SERIAL = '00X11Y223344556'
COOKIE = 'c00k1e-FAKE-9d8c7b6a'
ACCOUNT = 'u_4815162342'
SECRETS = (HOST, 'fake-printer-cloud', MODEL_ID, JOB_ID, OTHER_JOB_ID, SIGNATURE, 'X-Amz-Signature', 'X-Amz-Credential', CREDENTIAL, ACCESS_CODE, SERIAL, COOKIE, ACCOUNT,
           'Set-Cookie', 'https://')


def png(seed):
    return ledger_cache.PNG + hashlib.sha256(seed.encode()).digest() * 40


def signed(model, job, plate, n=0):
    return ('https://%s/private/%s/%s/3mf/Metadata/plate_%d.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=%s%%2F20260304&X-Amz-Expires=86400'
            '&X-Amz-Signature=%s%02d&access_code=%s&dev_id=%s' % (HOST, model, job, plate, CREDENTIAL, SIGNATURE, n, ACCESS_CODE, SERIAL))


class Cache(object):
    """A synthetic URL cache database in the shape the real one has: three tables, a binary plist per response, the
    pictures in fsCachedData/."""

    def __init__(self, folder, wal=False):
        self.folder, self.db, self.fs, self.n = folder, os.path.join(folder, 'Cache.db'), os.path.join(folder, 'fsCachedData'), 0
        os.makedirs(self.fs)
        self.conn = sqlite3.connect(self.db)
        if wal:
            self.conn.execute('PRAGMA journal_mode=WAL'); self.conn.execute('PRAGMA wal_autocheckpoint=0')
        self.conn.executescript(
            'CREATE TABLE cfurl_cache_response(entry_ID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, version INTEGER, hash_value INTEGER, storage_policy INTEGER, '
            'request_key TEXT UNIQUE, time_stamp NOT NULL DEFAULT CURRENT_TIMESTAMP, partition TEXT);'
            'CREATE TABLE cfurl_cache_blob_data(entry_ID INTEGER PRIMARY KEY, response_object BLOB, request_object BLOB, proto_props BLOB, user_info BLOB);'
            'CREATE TABLE cfurl_cache_receiver_data(entry_ID INTEGER PRIMARY KEY, isDataOnFS INTEGER, receiver_data BLOB);')
        self.conn.commit()

    def add(self, url, sent='Wed, 04 Mar 2026 18:30:00 GMT', picture=None, etag=None, inline=False, on_disk=True):
        self.n += 1
        headers = {'Content-Type': 'image/png', 'x-amz-expiration': 'expiry-date="Tue, 02 Jun 2026 00:00:00 GMT"', 'Set-Cookie': 'session=%s; Path=/' % COOKIE,
                   'X-Access-Code': ACCESS_CODE, 'X-Device-Serial': SERIAL, 'X-Account': ACCOUNT}
        if sent: headers['Last-Modified'] = sent
        if picture is not None or etag: headers['Etag'] = '"%s"' % (etag or hashlib.md5(picture).hexdigest())
        blob = plistlib.dumps({'Version': 1, 'Array': [{'_CFURLString': url, '_CFURLStringType': 15}, 794343840.0, 0, 200, headers, 'image/png']}, fmt=plistlib.FMT_BINARY)
        cur = self.conn.execute('INSERT INTO cfurl_cache_response(version, hash_value, storage_policy, request_key) VALUES (0, ?, 0, ?)', (self.n, url))
        eid = cur.lastrowid
        self.conn.execute('INSERT INTO cfurl_cache_blob_data(entry_ID, response_object) VALUES (?, ?)', (eid, blob))
        if inline:
            self.conn.execute('INSERT INTO cfurl_cache_receiver_data VALUES (?, 0, ?)', (eid, picture))
        else:
            name = 'A1B2C3D4-0000-4000-8000-%012d' % self.n
            if picture is not None and on_disk:
                with open(os.path.join(self.fs, name), 'wb') as f: f.write(picture)
            self.conn.execute('INSERT INTO cfurl_cache_receiver_data VALUES (?, 1, ?)', (eid, name.encode()))
        self.conn.commit()

    def close(self): self.conn.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        self.machine = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.machine)       # stands for the slicer's cache folder
        os.makedirs(os.path.join(self.d, 'bambulab', 'ledger')); os.makedirs(os.path.join(self.d, 'stl'))
        self.proj = os.path.join(self.d, 'bambulab', 'Demo.3mf')
        self.clock = Clock()
        p = mock.patch.object(ledger, '_now', self.clock); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(ledger_cache, 'cache_db_path', lambda: None); p.start(); self.addCleanup(p.stop)   # never this machine's real cache
        self.cache = Cache(self.machine); self.addCleanup(self.cache.close)
        self.outputs = []

    def three_jobs(self):
        """Two fetches of one job of this project, one job of another project, one row that is no job."""
        c = self.cache
        c.add(signed(MODEL_ID, JOB_ID, 2, 1), picture=png('bracket plate'))
        c.add(signed(MODEL_ID, JOB_ID, 2, 2), picture=png('bracket plate'))                                    # the same job, fetched again with a new signature
        c.add(signed(MODEL_ID, OTHER_JOB_ID, 7), sent='Thu, 05 Mar 2026 08:15:30 GMT', picture=png('a tea box'))
        c.add('https://%s/avatars/%s.png?token=%s' % (HOST, ACCOUNT, COOKIE), picture=png('avatar'))
        return c.db

    def switch_on(self, quote='yes, read the slicer cache on this computer', cache_db=None):
        """The supported way: ledger_cache.switch() with the owner's words, as `ledger.py cache on --quote "..." [--db PATH]` calls it."""
        ledger_cache.switch(self.d, True, quote, cache_db=cache_db)

    def typed_by_hand(self, **content):
        """A local.json somebody typed: what the reader must NOT take for the owner's switch unless every other condition holds too."""
        with open(ledger_cache.switch_file(self.d), 'w') as f: json.dump(content, f)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ledger.main(['--root', self.d] + list(argv))
        self.outputs += [out.getvalue(), err.getvalue()]
        return code, out.getvalue(), err.getvalue()

    def journal(self):
        with open(ledger.events_file(self.d)) as f: return f.read()

    def write_bracket(self, index=2):
        path = write_stl(os.path.join(self.d, 'stl', 'bracket.stl'), block(**BRACKET))
        V, F = ledger_mesh.read_stl(path)
        mesh = {'object': 'bracket', 'plate': '[TODO] base - bracket', 'plate_index': index, 'stl': path, 'vertices': [(x + 100, y + 90, z) for x, y, z in V], 'triangles': F}
        return ledger.record_writes(self.proj, [mesh])[0]

    def stored_files(self):
        for dirpath, _, names in os.walk(self.d):
            for n in names: yield os.path.join(dirpath, n)

    def assert_clean(self, *texts):
        """None of the planted fakes in these texts, in any output so far, or in any file under the project."""
        for text in list(texts) + self.outputs:
            for s in SECRETS: self.assertNotIn(s, text)
        for path in self.stored_files():
            with open(path, 'rb') as f: data = f.read()
            for s in SECRETS: self.assertNotIn(s.encode(), data, '%s in %s' % (s, os.path.relpath(path, self.d)))


class Reader(Base):
    def setUp(self):
        super().setUp(); self.switch_on()

    def test_without_the_switch_it_refuses_and_opens_nothing(self):
        db = self.three_jobs()
        def never(*a, **kw): raise AssertionError('the cache was opened without the local switch')
        for label, prepare in (('never switched on', lambda: os.remove(ledger_cache.switch_file(self.d))),
                               ('typed by hand, without the owner\'s words', lambda: self.typed_by_hand(studio_cache=True, cache_db=db)),
                               ('switched off again', lambda: (self.switch_on(), ledger_cache.switch(self.d, False)))):
            prepare()
            with self.subTest(label), mock.patch.object(ledger_cache, '_read_jobs', never), mock.patch.object(ledger_cache.sqlite3, 'connect', never), \
                    mock.patch.object(ledger_cache, 'cache_db_path', never):
                self.assertEqual(ledger_cache.read_jobs(self.d, db), []); self.assertEqual(ledger_cache.read_jobs(self.d), [])
                self.assertTrue(ledger_cache.LAST_ERROR.startswith('off: ')); self.assertIn('local.json', ledger_cache.LAST_ERROR)
        with self.assertRaises(TypeError): ledger_cache.read_jobs()                                               # no project, no reading
        self.switch_on()
        self.assertEqual(len(ledger_cache.read_jobs(self.d, db)), 2); self.assertIsNone(ledger_cache.LAST_ERROR)

    def test_four_fields_per_job_and_nothing_else(self):
        jobs = ledger_cache.read_jobs(self.d, self.three_jobs())
        self.assertEqual([sorted(j) for j in jobs], [['plate_index', 'sent_at', 'thumb', 'thumb_md5']] * 2)
        self.assertEqual([(j['sent_at'], j['plate_index']) for j in jobs], [('2026-03-04T18:30:00Z', 2), ('2026-03-05T08:15:30Z', 7)])       # oldest first; two fetches = one job
        self.assertEqual(jobs[0]['thumb'], png('bracket plate')); self.assertEqual(jobs[0]['thumb_md5'], hashlib.md5(png('bracket plate')).hexdigest())
        self.assert_clean(repr([dict(j, thumb=None) for j in jobs]))

    def test_rows_that_are_not_a_job_picture_are_ignored(self):
        c = self.cache
        c.add('https://%s/private/%s/%s/3mf/Metadata/plate_1.png.json?x=1' % (HOST, MODEL_ID, JOB_ID), picture=png('x'))
        c.add('https://%s/static/logo.png?next=/3mf/Metadata/plate_3.png' % HOST, picture=png('y'))            # the pattern only in the query string
        c.add(signed(MODEL_ID, JOB_ID, 4), sent=None, picture=png('z'))                                         # no upload time: not a job
        c.add(signed(MODEL_ID, JOB_ID, 5), sent='not a date', picture=png('z'))
        self.assertEqual(ledger_cache.read_jobs(self.d, c.db), [])

    def test_only_a_real_picture_is_taken(self):
        c = self.cache
        c.add(signed(MODEL_ID, '1', 1), picture=png('inline'), inline=True)                                     # stored in the database itself
        c.add(signed(MODEL_ID, '2', 2), sent='Wed, 04 Mar 2026 20:00:00 GMT', picture=png('gone'), on_disk=False)   # the picture file was evicted
        c.add(signed(MODEL_ID, '3', 3), sent='Wed, 04 Mar 2026 21:00:00 GMT', picture=b'<html>%s</html>' % COOKIE.encode())
        c.add(signed(MODEL_ID, '4', 4), sent='Wed, 04 Mar 2026 22:00:00 GMT', picture=png('other'), etag='0' * 32)   # not the picture the header names
        c.add(signed(MODEL_ID, '5', 5), sent='Wed, 04 Mar 2026 23:00:00 GMT', picture=png('odd'), etag='W/' + SERIAL)  # an etag that is no MD5 is dropped
        jobs = ledger_cache.read_jobs(self.d, c.db)
        self.assertEqual([j['plate_index'] for j in jobs], [1, 2, 3, 4, 5])
        self.assertEqual(jobs[0]['thumb'], png('inline'))
        self.assertEqual([(j['thumb'], j['thumb_md5']) for j in jobs[1:4]], [(None, hashlib.md5(png('gone')).hexdigest()), (None, hashlib.md5(b'<html>%s</html>' % COOKIE.encode()).hexdigest()), (None, '0' * 32)])
        self.assertEqual((jobs[4]['thumb'], jobs[4]['thumb_md5']), (png('odd'), hashlib.md5(png('odd')).hexdigest()))

    def test_a_picture_name_cannot_leave_the_cache_folder(self):
        c = self.cache
        outside = os.path.join(self.machine, 'secret.png')
        with open(outside, 'wb') as f: f.write(png('outside'))
        c.add(signed(MODEL_ID, JOB_ID, 1))
        c.conn.execute("UPDATE cfurl_cache_receiver_data SET receiver_data = ?", (b'../secret.png',)); c.conn.commit()
        self.assertEqual([j['thumb'] for j in ledger_cache.read_jobs(self.d, c.db)], [None])

    def test_it_reads_a_private_copy_that_is_gone_afterwards(self):
        db = self.three_jobs()
        with open(db, 'rb') as f: before = f.read()
        seen, real = {}, sqlite3.connect

        def connect(path, *a, **kw):
            folder = os.path.dirname(path)
            seen.update(path=path, folder=folder, names=sorted(os.listdir(folder)), folder_mode=stat.S_IMODE(os.stat(folder).st_mode),
                        modes=[stat.S_IMODE(os.stat(os.path.join(folder, n)).st_mode) for n in sorted(os.listdir(folder))])
            return real(path, *a, **kw)
        with mock.patch.object(ledger_cache.sqlite3, 'connect', connect):
            self.assertEqual(len(ledger_cache.read_jobs(self.d, db)), 2)
        self.assertNotEqual(os.path.dirname(seen['path']), self.machine); self.assertNotIn(self.d, seen['path'])   # neither the original nor the project
        self.assertEqual(seen['names'], ['cache.db'])
        if os.name == 'posix':
            self.assertEqual(seen['folder_mode'], 0o700); self.assertEqual(seen['modes'], [0o600])
        self.assertFalse(os.path.exists(seen['folder']))                                                          # deleted
        with open(db, 'rb') as f: self.assertEqual(f.read(), before)                                              # the original is untouched
        self.assertEqual(sorted(os.listdir(self.machine)), ['Cache.db', 'fsCachedData'])

    def test_the_copy_is_deleted_when_reading_fails(self):
        db = self.three_jobs(); made = []
        real = tempfile.mkdtemp

        def mkdtemp(*a, **kw):
            made.append(real(*a, **kw)); return made[-1]

        def boom(*a, **kw): raise sqlite3.DatabaseError('unable to open %s' % signed(MODEL_ID, JOB_ID, 2))
        with mock.patch.object(ledger_cache.tempfile, 'mkdtemp', mkdtemp), mock.patch.object(ledger_cache.sqlite3, 'connect', boom), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(ledger_cache.read_jobs(self.d, db), [])
        self.assertEqual(len(made), 1); self.assertFalse(os.path.exists(made[0]))
        self.assertEqual(out.getvalue() + err.getvalue(), '')                                                     # the error text (it holds a link) is not printed
        self.assertEqual(ledger_cache.LAST_ERROR, 'DatabaseError: unable to open <link>')                         # what is kept for the report is redacted
        self.assert_clean(ledger_cache.LAST_ERROR)
        self.switch_on(cache_db=db)
        with mock.patch.object(ledger_cache.sqlite3, 'connect', boom):
            code, shown, _ = self.run_cli('jobs', '--sync')
        self.assertIn('could not be read (DatabaseError: unable to open <link>)', shown); self.assert_clean()

    def test_a_copy_left_by_a_killed_run_is_swept(self):
        old = tempfile.mkdtemp(prefix='ledger-cache-'); self.addCleanup(shutil.rmtree, old, True)
        fresh = tempfile.mkdtemp(prefix='ledger-cache-'); self.addCleanup(shutil.rmtree, fresh, True)
        os.utime(old, (1, 1))
        ledger_cache.read_jobs(self.d, self.three_jobs())
        self.assertFalse(os.path.exists(old)); self.assertTrue(os.path.exists(fresh))                             # another run may be using the fresh one

    def test_the_newest_jobs_are_in_the_wal_file(self):
        folder = os.path.join(self.machine, 'wal'); os.makedirs(folder)
        c = Cache(folder, wal=True); self.addCleanup(c.close)
        c.add(signed(MODEL_ID, JOB_ID, 2), picture=png('bracket plate'))
        c.add(signed(MODEL_ID, OTHER_JOB_ID, 7), sent='Thu, 05 Mar 2026 08:15:30 GMT', picture=png('newest'))
        self.assertGreater(os.path.getsize(c.db + '-wal'), 0); self.assertTrue(os.path.exists(c.db + '-shm'))    # the application still has it open
        seen, real = [], sqlite3.connect

        def connect(path, *a, **kw):
            seen.extend(sorted(os.listdir(os.path.dirname(path)))); return real(path, *a, **kw)
        with mock.patch.object(ledger_cache.sqlite3, 'connect', connect):
            jobs = ledger_cache.read_jobs(self.d, c.db)
        self.assertEqual(seen, ['cache.db', 'cache.db-shm', 'cache.db-wal'])
        self.assertEqual([j['plate_index'] for j in jobs], [2, 7])
        shutil.copyfile(c.db, os.path.join(self.machine, 'alone.db'))                                             # the main file alone misses them
        self.assertEqual(ledger_cache.read_jobs(self.d, os.path.join(self.machine, 'alone.db')), [])

    def test_a_database_it_cannot_read_gives_no_jobs_and_no_error(self):
        bad = os.path.join(self.machine, 'bad.db')
        with open(bad, 'wb') as f: f.write(b'not a database ' + SIGNATURE.encode())
        empty = os.path.join(self.machine, 'empty.db'); sqlite3.connect(empty).close()
        for path in (bad, empty, os.path.join(self.machine, 'missing.db'), None, self.machine):
            with self.subTest(path=path): self.assertEqual(ledger_cache.read_jobs(self.d, path), [])

    def test_no_network_code_and_no_other_file_of_the_slicer(self):
        with open(os.path.join(ROOT, 'tools', 'ledger', 'ledger_cache.py'), encoding='utf-8') as f: text = f.read()
        for word in ('import socket', 'urllib', 'http.client', 'urlopen', 'requests', 'BambuStudio.conf', 'binarycookies', 'NetworkEngine', 'access_code.'):
            self.assertNotIn(word, text)


class Switch(Base):
    def test_off_by_default_the_reader_does_not_run(self):
        db = self.three_jobs()
        self.assertFalse(ledger_cache.enabled(self.d)); self.assertIn('local.json', ledger_cache.why_off(self.d))

        def never(*a, **kw): raise AssertionError('the cache was read without the local switch')
        with mock.patch.object(ledger_cache, 'read_jobs', never), mock.patch.object(ledger_cache, '_read_jobs', never), mock.patch.object(ledger_cache, 'cache_db_path', never):
            self.assertEqual(ledger_cache.sync(self.d), 0); self.assertEqual(ledger_cache.sync(self.d, db), 0)
            self.assertEqual(ledger_cache.local_jobs(self.d), [])
            code, out, _ = self.run_cli('jobs', '--sync')
            self.assertEqual(code, 0); self.assertIn('OFF on this machine', out); self.assertIn("owner's yes", out)
            self.assertEqual(self.run_cli('doctor')[0], 0); self.assertIn('cache reader is OFF', self.outputs[-2])
        self.assertEqual(sorted(os.listdir(os.path.join(self.d, 'bambulab', 'ledger'))), [])                    # nothing was created

    def test_only_true_in_the_local_file_switches_it_on(self):
        for content in ('{"studio_cache": false}', '{"studio_cache": "yes"}', '{"studio_cache": 1}', '{}', '[true]', 'not json', '',
                        '{"studio_cache": true}', '{"studio_cache": true, "quote": "  "}', '{"studio_cache": true, "quote": 1}'):       # true, but not in the owner's words
            with open(ledger_cache.switch_file(self.d), 'w') as f: f.write(content)
            with self.subTest(content=content): self.assertFalse(ledger_cache.enabled(self.d)); self.assertEqual(ledger_cache.sync(self.d, self.cache.db), 0)
        self.switch_on()
        self.assertTrue(ledger_cache.enabled(self.d)); self.assertIsNone(ledger_cache.why_off(self.d))

    def test_a_committed_switch_in_the_project_does_not_count(self):
        # there is no committed place the reader looks at: a "studio_cache" key anywhere else is ignored
        with open(os.path.join(self.d, 'bambulab', 'ledger', 'config.json'), 'w') as f: json.dump({'studio_cache': True, 'sources': {'studio_cache': True}}, f)
        self.assertFalse(ledger_cache.enabled(self.d)); self.assertEqual(ledger_cache.sync(self.d, self.three_jobs()), 0)

    def test_switching_on_needs_the_owners_words_and_says_what_is_read(self):
        code, _, err = self.run_cli('cache', 'on')
        self.assertEqual(code, 4); self.assertIn("owner's yes", err); self.assertIn('signed', err); self.assertFalse(ledger_cache.enabled(self.d))
        code, out, _ = self.run_cli('cache', 'on', '--quote', 'yes, read the slicer cache on this computer')
        self.assertEqual(code, 0); self.assertTrue(ledger_cache.enabled(self.d))
        for text in ('fresh temporary folder', 'mode 600', 'signed', 'never stores, prints or opens a link', 'upload time, plate index, the plate picture and the MD5',
                     'no settings, no cookie store, no logs', 'also those\n  of other projects', 'this machine only'):
            self.assertIn(text, out)
        with open(ledger_cache.switch_file(self.d)) as f: self.assertEqual(json.load(f)['quote'], 'yes, read the slicer cache on this computer')
        if os.name == 'posix': self.assertEqual(stat.S_IMODE(os.stat(ledger_cache.switch_file(self.d)).st_mode), 0o600)
        self.assertEqual(self.run_cli('cache', 'off')[0], 0); self.assertFalse(ledger_cache.enabled(self.d))
        self.assertIn('OFF', self.run_cli('cache', 'status')[1])

    @unittest.skipUnless(shutil.which('git'), 'git is not installed')
    def test_in_git_the_switch_and_the_jobs_must_be_private_to_this_machine(self):
        git = lambda *a: subprocess.run(['git', '-C', self.d, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false'] + list(a),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        git('init', '-q')
        db = self.three_jobs()
        code, _, err = self.run_cli('cache', 'on', '--quote', 'yes')                                             # local.json would not be ignored here
        self.assertEqual(code, 4); self.assertIn('not git-ignored', err); self.assertFalse(os.path.exists(ledger_cache.switch_file(self.d)))
        self.typed_by_hand(studio_cache=True, quote='yes', at='2026-03-04T18:00:00Z')                              # typed by hand, complete, still not ignored
        self.assertIn('not git-ignored', ledger_cache.why_off(self.d)); self.assertEqual(ledger_cache.sync(self.d, db), 0)
        self.assertEqual(ledger_cache.read_jobs(self.d, db), []); self.assertIn('not git-ignored', ledger_cache.LAST_ERROR)
        git('add', '-f', 'bambulab/ledger/local.json'); git('commit', '-q', '-m', 'a switch that travels with the repository')
        self.assertIn('committed to git', ledger_cache.why_off(self.d)); self.assertEqual(ledger_cache.sync(self.d, db), 0)
        self.assertFalse(os.path.exists(ledger_cache.jobs_file(self.d)))
        git('rm', '-q', '--cached', 'bambulab/ledger/local.json'); git('commit', '-q', '-m', 'remove it')
        self.assertEqual(self.run_cli('init')[0], 0)                                                             # adds the two lines to .gitignore
        self.assertIsNone(ledger_cache.why_off(self.d)); self.assertEqual(ledger_cache.sync(self.d, db), 2)
        status = subprocess.run(['git', '-C', self.d, 'status', '--porcelain', '--untracked-files=all'], stdout=subprocess.PIPE).stdout.decode()
        self.assertNotIn('local', status)                                                                         # neither the switch nor a job is offered to a commit
        self.assertEqual(subprocess.run(['git', '-C', self.d, 'check-ignore', '-q', 'bambulab/ledger/local/jobs.jsonl']).returncode, 0)


class Sync(Base):
    def test_jobs_and_pictures_go_to_the_local_folder(self):
        self.switch_on()
        self.assertEqual(ledger_cache.sync(self.d, self.three_jobs()), 2)
        local = os.path.join(self.d, 'bambulab', 'ledger', 'local')
        with open(os.path.join(local, '.gitignore')) as f: self.assertEqual(f.read(), '*\n')                    # the folder ignores itself
        jobs = ledger_cache.local_jobs(self.d)
        md5 = hashlib.md5(png('bracket plate')).hexdigest()
        self.assertEqual(sorted(jobs[0]), ['id', 'plate_index', 'seen', 'sent_at', 'source', 'thumb', 'thumb_md5'])
        self.assertEqual((jobs[0]['id'], jobs[0]['sent_at'], jobs[0]['plate_index'], jobs[0]['thumb_md5'], jobs[0]['thumb']),
                         ('J-20260304-1830-p2', '2026-03-04T18:30:00Z', 2, md5, 'bambulab/ledger/local/thumbs/%s.png' % md5))
        self.assertEqual(jobs[1]['id'], 'J-20260305-0815-p7')
        with open(os.path.join(self.d, jobs[0]['thumb']), 'rb') as f: self.assertEqual(f.read(), png('bracket plate'))
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(os.stat(local).st_mode), 0o700); self.assertEqual(stat.S_IMODE(os.stat(ledger_cache.jobs_file(self.d)).st_mode), 0o600)
        self.assertEqual(ledger_cache.sync(self.d, self.cache.db), 0)                                           # nothing new
        self.cache.add(signed(MODEL_ID, '5550005555', 2), picture=png('bracket plate'))                         # the same plate sent again in the same minute
        self.assertEqual(ledger_cache.sync(self.d, self.cache.db), 0)                                           # same time, plate and picture: one job
        self.cache.add(signed(MODEL_ID, '5550006666', 2), sent='Wed, 04 Mar 2026 18:30:40 GMT', picture=png('bracket plate'))
        self.assertEqual(ledger_cache.sync(self.d, self.cache.db), 1)
        self.assertEqual([j['id'] for j in ledger_cache.local_jobs(self.d)], ['J-20260304-1830-p2', 'J-20260304-1830-p2-2', 'J-20260305-0815-p7'])
        self.assert_clean()

    def test_the_database_named_in_the_local_file(self):
        db = self.three_jobs()
        self.assertEqual(self.run_cli('cache', 'on', '--quote', 'yes, read the slicer cache on this computer', '--db', db)[0], 0)      # the supported way to name it
        with open(ledger_cache.switch_file(self.d)) as f: self.assertEqual(json.load(f)['cache_db'], db)
        self.assertEqual(ledger_cache.source(self.d), db)
        code, out, _ = self.run_cli('jobs', '--sync')
        self.assertEqual(code, 0); self.assertIn('2 new jobs', out); self.assertIn('J-20260304-1830-p2', out); self.assertIn('not complete', out); self.assertIn('not a kept part', out)
        self.assertEqual(self.run_cli('cache', 'off')[0], 0)                                                     # switched off again: the local jobs are not shown
        self.assertEqual(ledger_cache.local_jobs(self.d), []); self.assertIn('OFF', self.run_cli('jobs')[1])

    def test_unrelated_jobs_never_reach_the_committed_events_file(self):
        self.write_bracket(index=2); before = self.journal()
        self.switch_on(cache_db=self.three_jobs())
        self.assertEqual(self.run_cli('jobs', '--sync')[0], 0); self.assertEqual(self.run_cli('jobs', '--json')[0], 0); self.assertEqual(self.run_cli('doctor')[0], 0)
        self.assertEqual(self.journal(), before)                                                                 # reading and listing writes nothing committed
        self.assertEqual(ledger.read_events(self.d, 'job'), [])
        code, out, _ = self.run_cli('tie', 'J-20260304-1830-p2', '--object', 'bracket', '--basis', 'picture')
        self.assertEqual(code, 0)
        jobs = ledger.read_events(self.d, 'job')
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0]['id'], jobs[0]['sent_at'], jobs[0]['plate_index'], jobs[0]['basis'], jobs[0]['project'], jobs[0]['source']),
                         ('J-20260304-1830-p2', '2026-03-04T18:30:00Z', 2, 'picture', 'bambulab/Demo.3mf', 'studio_cache'))
        text = self.journal()
        for other in ('2026-03-05T08:15', 'J-20260305', hashlib.md5(png('a tea box')).hexdigest(), 'thumbs', 'tea'):
            self.assertNotIn(other, text)                                                                        # the other project's print is not in the repository
        committed = [p for p in self.stored_files() if os.sep + 'local' + os.sep not in p and not p.endswith('local.json')]
        for path in committed:
            with open(path, 'rb') as f: self.assertNotIn(b'0815', f.read(), path)
        self.assert_clean(text)

    def test_a_job_can_only_be_tied_to_an_object_of_this_project(self):
        self.write_bracket(); before = self.journal()
        self.switch_on(cache_db=self.three_jobs()); ledger_cache.sync(self.d)
        for argv in (['tie', 'J-20260305-0815-p7', '--object', 'tea box', '--basis', 'picture'],                # not an object of the write journal
                     ['tie', 'J-20260305-0815-p7', '--object', 'bracket'],                                       # neither a basis nor the owner's words
                     ['tie', 'J-19990101-0000-p1', '--object', 'bracket', '--basis', 'picture'],                 # no such job
                     ['tie', 'J-20260305-0815-p7', '--basis', 'picture']):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 4); self.assertIn('LEDGER ERROR', err)
        self.assertEqual(self.journal(), before)

    def test_a_tied_job_is_a_guess_until_the_owner_says_so(self):
        w = self.write_bracket()
        self.switch_on(cache_db=self.three_jobs()); ledger_cache.sync(self.d)
        code, out, _ = self.run_cli('jobs')
        self.assertIn("the write journal had on that plate number then: 'bracket'", out); self.assertIn('a hint, look at the picture', out)
        write_stl(os.path.join(self.d, 'stl', 'bracket.stl'), block(**BRACKET_HOLE_MOVED))                       # the STL moved on after the job
        code, out, _ = self.run_cli('tie', 'J-20260304-1830-p2', '--object', 'bracket', '--basis', 'picture')
        self.assertIn('still a guess', out); self.assertIn(w['model'][:8], out)
        job = ledger.read_events(self.d, 'job')[0]
        self.assertEqual(job['objects'], [{'object': 'bracket', 'pose': w['pose'], 'model': w['model']}]); self.assertEqual(job['by'], 'agent')
        copy = ledger.printed_path(self.d, 'bracket', w['model'])                                                # the STL of that write, from the pool
        self.assertEqual(ledger_mesh.stl_ids(copy)['geom_hash'], w['model'])
        st = ledger.status(self.d, 'bracket')
        self.assertEqual(st['level'], 'ask'); self.assertIn('A sent job is not a kept part', st['why'])
        self.assertIn('TIED to bracket (picture)', self.run_cli('jobs')[1])
        self.clock.t = self.clock.t.replace(day=6)
        code, out, _ = self.run_cli('tie', 'J-20260304-1830-p2', '--object', 'bracket', '--quote', 'yes, that is the bracket on the frame')
        self.assertEqual(code, 0)
        job = ledger.read_events(self.d, 'job')[-1]
        self.assertEqual((job['basis'], job['by'], job['quote']), ('owner', 'owner', 'yes, that is the bracket on the frame'))
        st = ledger.status(self.d, 'bracket')
        self.assertEqual((st['level'], len(st['prints']), st['prints'][0]['basis']), ('printed-changed', 1, 'job+owner'))     # the later tie of the same job replaces the guess
        self.assertEqual(len(st['changed']['boxes']), 1)

    def test_a_job_from_before_the_journal_has_no_version(self):
        self.cache.add(signed(MODEL_ID, JOB_ID, 2), sent='Sun, 01 Mar 2026 09:00:00 GMT', picture=png('early'))
        self.write_bracket()                                                                                     # the journal starts on 2026-03-04
        self.switch_on(cache_db=self.cache.db); ledger_cache.sync(self.d)
        code, out, _ = self.run_cli('tie', 'J-20260301-0900-p2', '--object', 'bracket', '--quote', 'that was the first bracket')
        self.assertEqual(code, 0); self.assertIn('version unknown', out)
        self.assertEqual(ledger.read_events(self.d, 'job')[0]['objects'], [{'object': 'bracket', 'pose': None, 'model': None}])
        self.assertEqual(ledger.status(self.d, 'bracket')['level'], 'printed-unknown-version')


class Redaction(Base):
    def test_no_planted_secret_reaches_any_output_or_stored_file(self):
        """A fake signed link, a fake access code, a fake serial, a fake cookie, fake ids and a fake host are in the
        cache. Every command is run; every output and every file under the project is searched."""
        w = self.write_bracket()
        db = self.three_jobs()
        self.cache.add(signed(SERIAL, ACCESS_CODE, 3), sent='Fri, 06 Mar 2026 10:00:00 GMT', picture=png('ids in the path'))
        made, real = [], tempfile.mkdtemp

        def mkdtemp(*a, **kw):
            made.append(real(*a, **kw)); return made[-1]
        with mock.patch.object(ledger_cache.tempfile, 'mkdtemp', mkdtemp):
            self.run_cli('cache', 'status'); self.run_cli('jobs', '--sync')                                      # still off
            self.run_cli('cache', 'on', '--quote', 'yes, read the slicer cache here')
            with open(ledger_cache.switch_file(self.d)) as f: local = json.load(f)
            self.switch_on(cache_db=db, quote=local['quote'])
            returned = ledger_cache.read_jobs(self.d, db)
            for argv in (['jobs', '--sync'], ['jobs'], ['jobs', '--all', '--json'], ['doctor'], ['status'], ['status', '--json'], ['cache', 'status'],
                         ['tie', 'J-20260304-1830-p2', '--object', 'bracket', '--basis', 'picture'], ['tie', 'J-20260306-1000-p3', '--object', 'bracket', '--basis', 'write-journal'],
                         ['tie', 'J-20260305-0815-p7', '--object', 'nothing here', '--basis', 'picture'], ['tie', 'J-0', '--object', 'bracket', '--basis', 'picture'],
                         ['status', 'bracket'], ['may-change', 'bracket'], ['say', 'bracket', 'printed', '--quote', 'the bracket is printed'], ['status', 'bracket', '--json'],
                         ['plates', 'bambulab/Demo.3mf'], ['jobs', '--sync'], ['doctor']):
                self.run_cli(*argv)
        self.assertEqual(len(ledger_cache.local_jobs(self.d)), 3)
        self.assertGreaterEqual(len(made), 3)
        for folder in made: self.assertFalse(os.path.exists(folder))                                             # every private copy is gone
        self.assertIn('J-20260304-1830-p2', ''.join(self.outputs)); self.assertIn(w['model'][:8], ''.join(self.outputs))   # the commands did run
        self.assert_clean(repr([dict(j, thumb=None) for j in returned]), json.dumps(ledger_cache.local_jobs(self.d)), json.dumps(ledger.read_events(self.d)))
        names = ' '.join(os.path.relpath(p, self.d) for p in self.stored_files())
        for s in SECRETS: self.assertNotIn(s, names)

    def test_redact_free_text(self):
        home = os.path.expanduser('~')
        text = ('unable to open %s (Set-Cookie: session=%s; Path=/) access_code=%s serial: %s token "%s" X-Access-Code: %s\n'
                'file %s/Library/Caches/app/Cache.db host %s and 192.0.2.17:8883 id %s job %s account=%s'
                % (signed(MODEL_ID, JOB_ID, 2), COOKIE, ACCESS_CODE, SERIAL, SIGNATURE, ACCESS_CODE, home, HOST, MODEL_ID, JOB_ID, ACCOUNT))
        out = ledger_cache.redact(text)
        for s in SECRETS + ('192.0.2.17', 'AWS4', '86400'):
            self.assertNotIn(s, out)
        if home not in ('', '~', '/'): self.assertNotIn(home, out)
        self.assertIn('unable to open <link>', out); self.assertIn('<host>', out); self.assertIn('Library/Caches/app/Cache.db', out)
        plain = 'no such table: jobs (plate 2, 2026-03-04T18:30:00Z, stl/bracket.stl)'
        self.assertEqual(ledger_cache.redact(plain), plain)                                                       # ordinary text stays readable

    def test_redact_says_what_it_cannot_know(self):
        self.assertEqual(ledger_cache.redact('the code is %s' % ACCESS_CODE), 'the code is <id>')                # eight digits have a shape
        self.assertEqual(ledger_cache.redact('the code is a1b2c3d4'), 'the code is a1b2c3d4')                    # a short mixed code has none ...
        self.assertIn('SHORT secret', ledger_cache.redact.__doc__)                                               # ... and the docstring says so


if __name__ == '__main__':
    unittest.main()
