"""Host-side probe: one registered .3mf project against its STL folder, the stamps and its build table.
Reads files only. No Fusion, no slicer, nothing is written.

How an object of the project is tied to an STL, strongest first ("tie" of each object says which one held):

  write     the ledger has a `write` event of this project file with the object's pose hash, and that event names the STL
            and its geometry hash at the time ("model"). This is what the tools leave behind when they place a mesh.
  geometry  no such event (the project is older than the journal, or the mesh was carried). The stored vertices are then
            compared with the STLs that could be meant: the same triangle count and the same set of vertices after a
            move and one of the 24 quarter turns. That is as exact as a geometry hash; a part turned by a free angle is
            not found this way.
  name      only the name fits an STL (the rule of bambu3mf.update_meshes: the object name before ' - '). This is a hint
            and proves nothing: the object gets no stamp from it. "hint" says whether the shape (triangle count, volume,
            area) agrees with that STL.

The state of a stamp comes from gate_stamp.verify(). Meshes are read with meshid.read_project(), so the pose hashes are
the ones the guard and the ledger use.

The fit settings (read_fit_settings) are the slicer settings every fit is measured under. A project holds each one once
as a GLOBAL value (Metadata/project_settings.config, JSON) and any number of times as an OVERRIDE of one object or of
one part of an object (Metadata/model_settings.config, <metadata key=... value=.../> under <object> or <part>). Both
are read; the judge project_settings_match compares them with the declared target of the project, and the guard
compares the archive it is about to write with the file on disk (settings_diff)."""
import array, ast, json, os, re, sys, zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import gate_common
except ImportError:                                  # run from another folder: tools/gate (or gate/tools) is one folder up
    sys.path.insert(0, os.path.dirname(_HERE))
    import gate_common
from gate_common import GateError

_PROJECTS = {}           # (path, mtime, size) -> what _read_project() returns
_STLS = {}               # (path, mtime, size) -> what _stl_ids() returns
_PROOFS = {}             # (pose hash, model hash) -> bool
_QUANTUM = 0.001         # mm: two vertices are the same spot when they agree to this (one step either way)
_TABLE_NAMES = ('PLATES', 'PLATE_LIST', 'plates', 'plate_list')
_SETTINGS = 'Metadata/project_settings.config'
_MODEL_SETTINGS = 'Metadata/model_settings.config'
# the slicer settings every fit is measured under, by their keys in the project settings: the default of the parameter
# target.fit_settings, used when no registry can be read
FIT_SETTINGS = ('nozzle_diameter', 'line_width', 'layer_height', 'initial_layer_print_height', 'xy_hole_compensation',
                'xy_contour_compensation', 'elefant_foot_compensation', 'wall_generator', 'filament')
# "filament" is no key of the project settings: it is the filament type an object prints with. Its global value is the
# type of the first filament slot (an object without an "extruder" of its own prints with that one), an object's or a
# part's override is the type of the slot its "extruder" names. A fit printed in another material is another fit.
FILAMENT = 'filament'
TARGET_KEYS = {'nozzle_diameter': 'nozzle', FILAMENT: 'material'}       # how a setting is called in the declared target when not by its own key
# the print settings a declared target may hold under "print" (project.settings_match compares them like the fit
# settings: the value every object prints with). Supports decide whether an overhang prints at all.
PRINT_SETTINGS = ('enable_support', 'support_type', 'support_remove_small_overhang', 'wall_sequence')
_OVERRIDE_LIST = 'different_settings_to_system'   # project settings: the keys that differ from the system presets, ';' joined, one text per preset
# archive members that give single objects their own layer heights (a height-range modifier, a variable layer profile).
# The names are the ones the slicer's source uses; no project file was at hand that holds one.
LAYER_MEMBERS = ('Metadata/layer_config_ranges.xml', 'Metadata/layer_heights_profile.txt')
_OBJECT = re.compile(r'<object\b([^>]*)>(.*?)</object\s*>', re.S)
_OBJECT_TAG = re.compile(r'<object\b')
_PART = re.compile(r'<part\b([^>]*)>(.*?)</part\s*>', re.S)
_META = re.compile(r'<metadata\b([^>]*?)/?>', re.S)
_ATTR = re.compile(r'([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')


def _file_key(path):
    st = os.stat(path)
    return os.path.abspath(path), st.st_mtime_ns, st.st_size


def _name_key(name):
    """What bambu3mf.update_meshes() matches an object and an STL by: the name before ' - ', without '.stl'; here also
    without regard to case and punctuation ('Motor plate' is motor_plate.stl)."""
    base = os.path.basename(str(name))
    if base.lower().endswith('.stl'):
        base = base[:-4]
    return gate_common.slug(base.split(' - ')[0])


def _bambu_name(name):
    """The name as bambu3mf writes it into a project (bambu3mf.bambu_name)."""
    try:
        return gate_common.sibling('bambu3mf').bambu_name(name)
    except Exception:                                # the writer is not vendored here: the same rule, spelled out
        name = str(name).replace(': ', ' - ').replace(':', '-').replace('/', '-')
        return ''.join(c for c in name if c not in '<>\\|?*"')


def _number(text, of=None):
    """A setting as a number: '0.42', ['0.4'], '105%' (of `of`). None when it is not one."""
    if isinstance(text, (list, tuple)):
        text = text[0] if text else None
    if text is None:
        return None
    try:
        s = str(text).strip()
        if s.endswith('%'):
            return round(float(s[:-1]) / 100.0 * of, 4) if of else None
        return float(s)
    except ValueError:
        return None


def fit_keys(value):
    """The list of fit settings out of the parameter target.fit_settings ("a b c", commas allowed) or out of a list;
    FIT_SETTINGS when it names nothing."""
    if isinstance(value, str):
        value = value.replace(',', ' ').split()
    keys = [str(k).strip() for k in (value or []) if str(k).strip()]
    return list(dict.fromkeys(keys)) or list(FIT_SETTINGS)


def target_key(setting):
    """The key of a fit setting in the declared target of gate/project.json: "nozzle" for nozzle_diameter, else its own."""
    return TARGET_KEYS.get(setting, setting)


def setting_value(raw, of=None):
    """A slicer setting as the facts hold it: a number when it reads as one ('0.125', '105%' of `of`), else the text.
    A list (one value per nozzle or filament) becomes its one value when all are equal, else the list. None stays None.
    A percent is a number only where `of` is given: the caller hands the nozzle over for a line width and for nothing
    else ('12.5%' as a hole compensation stays the text it is, and equals no declared number)."""
    if isinstance(raw, (list, tuple)):
        vals = [setting_value(x, of) for x in raw]
        return None if not vals else vals[0] if all(same_value(v, vals[0]) for v in vals) else vals
    if raw is None or isinstance(raw, bool):
        return None if raw is None else str(raw).lower()
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    n = _number(text, of)
    return n if n is not None else text


def _of(key, nozzle):
    """What a percent value of this setting is a share of: the nozzle for a line width, nothing for any other key."""
    return nozzle if str(key).endswith('line_width') else None


def same_value(a, b, tol=5e-7):
    """Are two setting values the same? Numbers within `tol`, texts equal, lists item by item."""
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if num(a) and num(b):
        return abs(a - b) <= tol
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y, tol) for x, y in zip(a, b))
    return a == b


def _unescape(text):
    return text.replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').replace('&apos;', "'").replace('&amp;', '&')


def _member(source, name):
    """The text of one archive member out of a path of a .3mf or out of {name: bytes}; None when it is not there."""
    if isinstance(source, dict):
        data = source.get(name)
    else:
        with zipfile.ZipFile(source) as z:
            data = z.read(name) if name in z.namelist() else None
    if data is None:
        return None
    return data.decode('utf-8', 'replace') if isinstance(data, (bytes, bytearray)) else str(data)


def _attrs(text):
    """The attributes of one XML tag as a dict: any order, double or single quotes, spaces round the '='."""
    return {m.group(1): _unescape(m.group(2) if m.group(2) is not None else m.group(3)) for m in _ATTR.finditer(text or '')}


def _slot_type(filaments, slot):
    """The filament type of a 1-based slot, as a setting value; a text that says so when the project has no such slot."""
    try:
        i = int(str(slot).strip())
    except (TypeError, ValueError):
        return 'extruder %s (not a slot number)' % slot
    if not filaments or i < 1 or i > len(filaments):
        return 'slot %d (the project has no such filament)' % i
    return setting_value(filaments[i - 1])


def _metas(text, want, nozzle, where, repeated, filaments=None):
    """(name, {key: value}) out of the <metadata key=... value=.../> tags of one object head or one part body. A fit key
    that is written twice with two values goes to `repeated`: the reader keeps the last one, what the slicer takes is
    not known. With FILAMENT wanted, an "extruder" tag gives the override FILAMENT: the type of that slot."""
    name, over, seen = None, {}, {}
    for m in _META.finditer(text):
        a = _attrs(m.group(1))
        k = a.get('key')
        if k == 'extruder' and FILAMENT in want and 'value' in a:
            k, v = FILAMENT, _slot_type(filaments, a['value'])
            if filaments and same_value(v, setting_value(filaments[0])):
                continue                                 # the type of slot 1: what it prints with anyway, no override
            if k in seen and not same_value(seen[k], v):
                repeated.append({'scope': where, 'key': k, 'values': [seen[k], v]})
            seen[k] = over[k] = v
        elif k == 'name':
            name = a.get('value', '')
        elif k in want and 'value' in a:
            v = setting_value(a['value'], _of(k, nozzle))
            if k in seen and not same_value(seen[k], v):
                repeated.append({'scope': where, 'key': k, 'values': [seen[k], v]})
            seen[k] = over[k] = v
    return name, over


def read_fit_settings(source, keys=None):
    """The fit settings of one project: its global values and every per-object override.

    source  path of a .3mf, or the archive as {member name: bytes} (what bambu3mf.load() returns and save() writes)
    keys    the settings to read (fit_keys of the parameter target.fit_settings); None = FIT_SETTINGS

    Returns {"keys": [the keys that were read],
             "global": {key: value or None (the project settings do not hold it)}, or None when the archive has no
                       readable Metadata/project_settings.config,
             "listed": {key: bool}: is the key in the project's list of settings that differ from the system presets
                       ("different_settings_to_system")? The slicer takes the preset's value for a key that is not
                       listed when it loads the file. None when the project settings hold no such list,
             "objects": [{"name": object name, "id": its id in the file, "overrides": {key: value},
                          "parts": [{"name", "subtype", "overrides": {key: value}}]}]
                        only objects that override at least one of the keys, on the object or on one of its parts,
             "objects_read": how many objects of Metadata/model_settings.config were read, or None when the archive has
                             no such file (then nothing is known about overrides),
             "objects_unread": how many <object> tags of that file the reader could not take apart (0 = every one read),
             "object_names": the names of all of them (None with "objects_read" None),
             "repeated": [{"scope", "key", "values"}] fit keys written twice with two values on one object or part,
             "layer_members": the members of LAYER_MEMBERS the archive holds with content: single objects then print
                              with layer heights that are not read here}.
    Values are numbers where the setting reads as one (setting_value); a percent line width is taken of the nozzle.
    Raises GateError when a path cannot be read as an archive."""
    keys = fit_keys(keys)
    try:
        ps_text, ms_text = _member(source, _SETTINGS), _member(source, _MODEL_SETTINGS)
        layers = [n for n in LAYER_MEMBERS if (_member(source, n) or '').strip()]
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        raise GateError('%s cannot be read as a .3mf project (%s: %s)' % (source, type(e).__name__, e))
    out = {'keys': keys, 'global': None, 'listed': None, 'objects': [], 'objects_read': None, 'objects_unread': 0, 'object_names': None,
           'repeated': [], 'layer_members': layers}
    try:
        ps = json.loads(ps_text) if ps_text is not None else None
    except ValueError:
        ps = None
    nozzle, filaments = None, None
    if isinstance(ps, dict):
        nozzle = _number(ps.get('nozzle_diameter'))
        kinds = ps.get('filament_type')
        filaments = [kinds] if isinstance(kinds, str) else [str(x) for x in kinds] if isinstance(kinds, list) else None
        out['global'] = {k: setting_value(ps.get(k), _of(k, nozzle)) for k in keys}
        if FILAMENT in keys:
            out['global'][FILAMENT] = setting_value(filaments[0]) if filaments else None
        lists = ps.get(_OVERRIDE_LIST)
        lists = [lists] if isinstance(lists, str) else lists
        if isinstance(lists, list):
            named = {k.strip() for text in lists if isinstance(text, str) for k in text.split(';') if k.strip()}
            out['listed'] = {k: k in named for k in keys if k != FILAMENT}      # the filament is no setting of that list
    if ms_text is not None:
        want, n, out['object_names'] = set(keys), 0, []
        for attrs, body in _OBJECT.findall(ms_text):
            n += 1
            oid = _attrs(attrs).get('id', '')
            head = body.split('<part', 1)[0]
            name, over = _metas(head, want, nozzle, None, [], filaments)
            name = name or ''
            _metas(head, want, nozzle, 'object %s' % name, out['repeated'], filaments)
            row = {'name': name, 'id': oid, 'parts': [], 'overrides': over}
            for pattrs, pbody in _PART.findall(body):
                pa = _attrs(pattrs)
                pname, _ = _metas(pbody, want, nozzle, None, [], filaments)
                pname = pname if pname is not None else 'part %s' % pa.get('id', '')
                if FILAMENT in want and pa.get('subtype', 'normal_part') != 'normal_part':
                    pwant = want - {FILAMENT}               # a modifier or blocker prints no filament of its own
                else:
                    pwant = want
                _, pover = _metas(pbody, pwant, nozzle, 'part %s of object %s' % (pname, name), out['repeated'], filaments)
                if pover:
                    row['parts'].append({'name': pname, 'subtype': pa.get('subtype'), 'overrides': pover})
            out['object_names'].append(row['name'])
            if row['overrides'] or row['parts']:
                out['objects'].append(row)
        out['objects_read'] = n
        out['objects_unread'] = max(0, len(_OBJECT_TAG.findall(ms_text)) - n)
    return out


def _override_scopes(settings):
    """{(scope text, object name, key): [values]} of every override in read_fit_settings() facts. Two objects of one
    name share a scope: their values are kept side by side."""
    out = {}
    for o in (settings or {}).get('objects') or []:
        for k, v in sorted((o.get('overrides') or {}).items()):
            out.setdefault(('object %s' % o.get('name'), o.get('name'), k), []).append(v)
        for p in o.get('parts') or []:
            for k, v in sorted((p.get('overrides') or {}).items()):
                out.setdefault(('part %s of object %s' % (p.get('name'), o.get('name')), o.get('name'), k), []).append(v)
    return out


def _asked(notes, key, scope_name, value):
    """Did the caller name this change? A note is {"key", "value" (None = any value), "scope": "global" | an object
    name | "*", "tool"}; scope_name is "global" or the object's name."""
    for n in notes or []:
        if not isinstance(n, dict) or n.get('key') != key or n.get('scope', '*') not in ('*', scope_name):
            continue
        want = n.get('value')
        if want is None or same_value(setting_value(want), value) or (isinstance(value, list) and any(same_value(setting_value(want), v) for v in value)):
            return n.get('tool') or 'the caller'
    return None


def print_keys(target=None):
    """The print settings to read for a declared target: PRINT_SETTINGS and whatever else its "print" blocks name."""
    keys = list(PRINT_SETTINGS)
    blocks = [target.get('print')] if isinstance(target, dict) else []
    objects = target.get('objects') if isinstance(target, dict) and isinstance(target.get('objects'), dict) else {}
    blocks += [o.get('print') for o in objects.values() if isinstance(o, dict)]
    for b in blocks:
        if isinstance(b, dict):
            keys += [k for k in b if isinstance(k, str) and k not in keys]
    return keys


def declared_value(target, key, name=None):
    """The value a declared target gives a setting, for one object (`name`: its own value under "objects" first) or
    for the project (name None). The nozzle diameter is "nozzle", the filament is "material". None when it says none."""
    if not isinstance(target, dict):
        return None
    objects = target.get('objects') if isinstance(target.get('objects'), dict) else {}
    own = objects.get(name) if isinstance(name, str) else None
    if isinstance(own, dict) and own.get(target_key(key)) is not None:
        return own.get(target_key(key))
    return target.get(target_key(key))


def _declares_object(target, name, key):
    objects = target.get('objects') if isinstance(target, dict) else None
    own = objects.get(name) if isinstance(objects, dict) else None
    return isinstance(own, dict) and own.get(target_key(key)) is not None


def meets(value, declared):
    """Does a setting value equal the declared one? Numbers to 0.0005, texts without regard to case (a declared
    true/false is 1/0), a list when every item does. None never does."""
    if declared is None or value is None:
        return False
    if isinstance(declared, bool):
        declared = 1.0 if declared else 0.0
    declared = setting_value(declared)
    if isinstance(value, list):
        return bool(value) and all(meets(v, declared) for v in value)
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if num(value) and num(declared):
        return abs(value - declared) <= 5e-4
    return str(value).strip().lower() == str(declared).strip().lower()


def settings_diff(before, after, asked=None, target=False):
    """What a write changes in the fit settings: `before` and `after` are read_fit_settings() of the file on disk and of
    the archive about to be written; `asked` the notes of the settings the caller means to change (bambu3mf.note_setting).

    Returns {"keys": [...], "compared": bool, "why": text when nothing could be compared,
             "changed": [{"key", "scope": "global" | "object <name>" | "part <part> of object <name>",
                          "before": value or None (not set), "after": value or None, "asked": bool, "by": tool or None}]}.
    Two more things come from the project's list of settings that differ from the system preset (read_fit_settings
    "listed"); the slicer takes the preset's value for a key that is not in it when it loads the file:
      a row with "note": the key LEAVES that list while its value stays. No note of the caller covers it.
      a row with "unlisted": True: the value changes and the key is not in the list afterwards. Whether the new value
        is the preset's (a template saved by the slicer holds preset values unlisted) cannot be known here, so the row
        is judged like any other and its text says what to check.
    The global value of every key is compared. An override is compared for every object that both files hold, by its
    name: an override that appears, vanishes or changes is a row. An object that only one of the two holds is a new or
    a removed object, not a changed setting. One case apart: an archive that holds NO object while the file on disk
    holds some (the empty template laid over a project before a rebuild) drops every override with them, and each is
    a row: the rebuild that follows knows only its plate table. A file on disk without project settings has no global value to lose:
    nothing is compared there.
    target: the declared target of the project ({} = none), or False (not given: a note covers whatever it names). With
    a target a note covers a change only when the value the object then prints with (the new override, else the new
    global value) equals the declared one (declared_value): the row of a noted change away from it, or of a key the
    target does not say, has "asked" False and "off_target" (why). Pure: no file is read here."""
    keys = list((after or {}).get('keys') or (before or {}).get('keys') or FIT_SETTINGS)
    out = {'keys': keys, 'compared': False, 'why': None, 'changed': []}
    if not isinstance(before, dict) or not isinstance(after, dict):
        out['why'] = 'one of the two projects could not be read'
        return out
    out['compared'] = True

    g0, g1 = before.get('global'), after.get('global')

    def row(key, scope, name, a, b):
        by = _asked(asked, key, name, b)
        r = {'key': key, 'scope': scope, 'before': a, 'after': b, 'asked': by is not None, 'by': by}
        if by is not None and target is not False:
            obj = None if name == 'global' else name
            want = declared_value(target, key, obj)
            prints = b if b is not None or obj is None else (g1 or {}).get(key) if isinstance(g1, dict) else None
            if want is None:
                r.update(asked=False, off_target='the declared target of this project says nothing about %s%s, so no note can cover the change'
                                                 % (target_key(key), '' if target else ' (the project has no declared target)'))
            elif not meets(prints, want):
                r.update(asked=False, off_target='the declared target says %s = %s%s and the %s would print with %s'
                                                 % (target_key(key), want, ' for this object' if obj and _declares_object(target, obj, key) else '',
                                                    'object' if obj else 'project', prints))
        out['changed'].append(r)
        return r

    if isinstance(g0, dict):                         # an archive without project settings drops every one of them
        g1 = g1 if isinstance(g1, dict) else {}
        l0, l1 = before.get('listed'), after.get('listed')
        for k in keys:
            moved = not same_value(g0.get(k), g1.get(k))
            unlisted = isinstance(l1, dict) and l1.get(k) is False and g1.get(k) is not None
            if moved:
                r = row(k, 'global', 'global', g0.get(k), g1.get(k))
                if unlisted:
                    r['unlisted'] = True             # said with the row; whether the slicer loads the value is not known here
            elif unlisted and isinstance(l0, dict) and l0.get(k) is True:
                # the value stays and the key leaves the list of settings that differ from the system preset: the slicer
                # takes the preset's value for it on load. No note of the caller covers this.
                out['changed'].append({'key': k, 'scope': 'global', 'before': 'listed', 'after': 'not listed', 'asked': False, 'by': None,
                                       'note': 'takes %s out of "%s" and keeps its value %s: the slicer takes the value of the system preset for it when '
                                               'it loads the file' % (k, _OVERRIDE_LIST, g1.get(k))})
    s0, s1 = _override_scopes(before), _override_scopes(after)
    if before.get('objects_read') and not after.get('objects_read'):
        # the archive holds no object at all (a template laid over the project): every override the file held goes with them
        for scope, name, k in sorted(s0, key=str):
            if k in keys:
                a = s0[(scope, name, k)]
                row(k, '%s, removed with every other object' % scope, name, a[0] if len(a) == 1 else a, None)
        return out
    if before.get('objects_read') is None or after.get('objects_read') is None:
        return out
    held0, held1 = before.get('object_names'), after.get('object_names')
    for scope, name, k in sorted(set(s0) | set(s1), key=str):
        if k not in keys:
            continue
        if (held0 is not None and name not in held0) or (held1 is not None and name not in held1):
            continue                                 # the object is new or gone: not a changed setting
        a, b = s0.get((scope, name, k)), s1.get((scope, name, k))
        one = lambda v: None if v is None else v[0] if len(v) == 1 else v
        if a is None or b is None or len(a) != len(b) or not all(same_value(x, y) for x, y in zip(sorted(a, key=str), sorted(b, key=str))):
            row(k, scope, name, one(a), one(b))
    return out


def _target_of_settings(settings):
    """{"nozzle", "line_width", "materials"} out of Metadata/project_settings.config, or None when the file has none."""
    if not isinstance(settings, dict):
        return None
    nozzle = _number(settings.get('nozzle_diameter'))
    line = _number(settings.get('line_width'), nozzle)
    kinds = settings.get('filament_type')
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
    out = {'nozzle': nozzle, 'line_width': line, 'materials': sorted(set(str(k) for k in kinds if k))}
    for extra in ('outer_wall_line_width', 'inner_wall_line_width'):
        v = _number(settings.get(extra), nozzle)
        if v is not None:
            out[extra] = v
    names = settings.get('filament_settings_id')
    if names:
        out['filaments'] = [names] if isinstance(names, str) else [str(n) for n in names]
    return out


def _read_project(path):
    """{"objects": [...], "settings": dict or None} of one .3mf, read once per file state. An object is
    {"name", "id", "plate", "plate_index", "printable", "pose", "triangles", "shape", "xyz" (flat array of the stored
    vertices), "parts"}. GateError when the file cannot be read as a project."""
    key = _file_key(path)
    if key in _PROJECTS:
        return _PROJECTS[key]
    meshid = gate_common.sibling('meshid')
    try:
        meshes = meshid.read_project(path)
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if '3D/3dmodel.model' not in names:
                raise GateError('%s is not a .3mf project: it holds no 3D/3dmodel.model' % path)
            settings = None
            if _SETTINGS in names:
                try:
                    settings = json.loads(z.read(_SETTINGS).decode('utf-8', 'replace'))
                except ValueError:
                    settings = None
    except GateError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, UnicodeError) as e:
        raise GateError('%s cannot be read as a .3mf project (%s: %s)' % (path, type(e).__name__, e))
    objects = []
    for m in meshes:
        V, F = m['vertices'], m['triangles']
        index = m.get('plate_index')
        plate = m.get('plate')
        if index is not None and not plate:
            plate = 'plate %s' % index                # a plate the owner never named: the slicer shows it by its number
        objects.append({'name': m['object'], 'id': m.get('id'), 'plate': plate if index is not None or plate else None,
                        'plate_index': index, 'printable': bool(m.get('printable', True)),
                        'pose': meshid.geom_hash(V, len(F)), 'triangles': len(F), 'shape': meshid.shape_id(V, F),
                        'xyz': array.array('d', [c for v in V for c in v]), 'parts': m.get('parts', 1)})
    if len(_PROJECTS) > 16:
        _PROJECTS.clear()
    _PROJECTS[key] = out = {'objects': objects, 'settings': settings}
    return out


def _stl_ids(path):
    """meshid.stl_ids() of one STL, read once per file state; {"error": text, "sha256"} when it is no STL."""
    key = _file_key(path)
    if key not in _STLS:
        if len(_STLS) > 4096:
            _STLS.clear()
        try:
            _STLS[key] = gate_common.sibling('meshid').stl_ids(path)
        except (ValueError, OSError, IndexError) as e:
            _STLS[key] = {'error': '%s: %s' % (type(e).__name__, e), 'sha256': gate_common.sha256_file(path)}
    return _STLS[key]


def _quarter_turns():
    turns = getattr(gate_common.sibling('meshid'), 'QUARTER_TURNS', None)
    if turns:
        return turns
    import itertools
    out = []
    for p in itertools.permutations((0, 1, 2)):
        even = p in ((0, 1, 2), (1, 2, 0), (2, 0, 1))
        for s in itertools.product((1, -1), repeat=3):
            if (s[0] * s[1] * s[2] > 0) == even:
                out.append((p, s))
    out.sort(key=lambda r: r != ((0, 1, 2), (1, 1, 1)))
    return out


def _same_geometry(xyz, n_triangles, stl_path):
    """Is the mesh stored in the project (flat x, y, z array) the mesh of this STL, moved and turned by quarter turns
    only? Both have the same triangle count, and every vertex of one lies on a vertex of the other within one quantum."""
    try:
        V, F = gate_common.sibling('meshid').read_stl(stl_path)
    except (ValueError, OSError, IndexError):
        return False
    n = len(xyz) // 3
    if not V or not n or len(F) != n_triangles:
        return False
    inv = 1.0 / _QUANTUM
    olo = [min(xyz[k::3]) for k in range(3)]; osize = [max(xyz[k::3]) - olo[k] for k in range(3)]
    slo = [min(v[k] for v in V) for k in range(3)]; shi = [max(v[k] for v in V) for k in range(3)]
    ssize = [shi[k] - slo[k] for k in range(3)]
    mine = set()
    for i in range(0, 3 * n, 3):
        mine.add((int(round((xyz[i] - olo[0]) * inv)), int(round((xyz[i + 1] - olo[1]) * inv)), int(round((xyz[i + 2] - olo[2]) * inv))))
    around = [(a, b, c) for a in (0, -1, 1) for b in (0, -1, 1) for c in (0, -1, 1)]
    for p, s in _quarter_turns():
        if any(abs(ssize[p[k]] - osize[k]) > 0.004 for k in range(3)):
            continue
        base = [slo[p[k]] if s[k] > 0 else -shi[p[k]] for k in range(3)]       # the low corner of the turned STL
        found = set()
        for count, v in enumerate(V):
            q = (int(round((s[0] * v[p[0]] - base[0]) * inv)), int(round((s[1] * v[p[1]] - base[1]) * inv)), int(round((s[2] * v[p[2]] - base[2]) * inv)))
            hit = None
            for a, b, c in around:
                cand = (q[0] + a, q[1] + b, q[2] + c)
                if cand in mine:
                    hit = cand; break
            if hit is None:
                found = None; break
            found.add(hit)
        if found is not None and len(found) == len(mine):
            return True
    return False


def _table_literal(root, rel_path):
    """(the plate list of a build script as Python data, None), or (None, why it cannot be read). Read with ast: the
    script is never run. Only a plain literal assigned at the top level to one of _TABLE_NAMES counts."""
    path = os.path.join(root, rel_path)
    try:
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError) as e:
        return None, 'the build table cannot be read (%s)' % type(e).__name__
    value = None
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else []
        for t in targets:
            if isinstance(t, ast.Name) and t.id in _TABLE_NAMES:
                value = node.value
    if value is None:
        return None, 'no plate list (%s) is assigned at the top level' % ' / '.join(_TABLE_NAMES[:2])
    try:
        plates = ast.literal_eval(value)
        for plate in plates:
            if not isinstance(plate[0], str) or not all(isinstance(o[0], str) for o in plate[1]):
                raise ValueError('names must be text')
    except (ValueError, TypeError, IndexError, KeyError, SyntaxError, MemoryError, RecursionError):
        return None, 'the plate list is computed, not a plain literal'
    return plates, None


def bed_normal(orient):
    """Outward normal, in the frame of the STL, of the model face that bambu3mf.build() puts on the bed for this
    orient value, or None when the value cannot be read. False, '' and '-z': (0, 0, -1), the part lies as modelled.
    True and '+z': (0, 0, 1). '+x', '-x', '+y', '-y': that face. 'n<x>/<y>/<z>': that normal. A turn on the bed
    (',z<deg>') changes nothing here. The print axis points the other way."""
    faces = {'-z': (0.0, 0.0, -1.0), '+z': (0.0, 0.0, 1.0), '+y': (0.0, 1.0, 0.0), '-y': (0.0, -1.0, 0.0), '+x': (1.0, 0.0, 0.0), '-x': (-1.0, 0.0, 0.0)}
    if orient is False or orient is None:
        return faces['-z']
    if orient is True:
        return faces['+z']
    if not isinstance(orient, str):
        return None
    face = orient.split(',')[0].strip()
    if face == '':
        return faces['-z']
    if face in faces:
        return faces[face]
    if face.startswith('n'):
        try:
            n = [float(x) for x in face[1:].split('/')]
        except ValueError:
            return None
        length = sum(c * c for c in n) ** 0.5
        if len(n) == 3 and length > 1e-9:
            return tuple(c / length for c in n)
    return None


def orientations(root, layout, stl_rel):
    """How the registered .3mf projects lay the part whose STL is `stl_rel` (relative to the root):
    [{"project": key, "table": file, "plate": plate name, "object": object name, "orient": the value as written,
      "down": bed_normal(orient) or None}], one row per object that is built from that STL.
    Also returns the reasons why a table could not be read: (rows, [texts]).

    The saved project comes first (probes.project_bed.placed): an object whose mesh is this STL turned as a rigid body
    gives the face it really lies on, measured, with "source": "project" ("table" is then the .3mf). A build table is
    read only for the projects that hold no such object yet (the part is not placed there): its rows have "source":
    "table". So a project the slicer or an arrange step has turned since the build is judged as it is saved."""
    from probes import project_bed
    rows, why = [], []
    try:
        placed, problems, done = project_bed.placed(root, layout, stl_rel)
    except Exception as e:                       # what could not be measured is unknown; the build tables may still say it
        placed, problems, done = [], ['the saved projects could not be measured (%s: %s)' % (type(e).__name__, e)], set()
    rows += placed
    why += problems
    want = os.path.normpath(os.path.join(root, *str(stl_rel).replace(os.sep, '/').split('/'))) if stl_rel else None
    for entry in (layout or {}).get('projects') or []:
        if not isinstance(entry, dict) or not entry.get('build_table') or entry.get('key') in done:
            continue
        rel_table = str(entry['build_table']).replace(os.sep, '/')
        plates, problem = _table_literal(root, rel_table)
        if plates is None:
            why.append('%s: %s' % (rel_table, problem))
            continue
        folder = os.path.join(root, *str(entry.get('stl_dir') or '').replace(os.sep, '/').split('/')) if entry.get('stl_dir') else root
        for plate in plates:
            for o in plate[1]:
                if len(o) < 2 or not isinstance(o[1], str) or want is None:
                    continue
                if os.path.normpath(o[1] if os.path.isabs(o[1]) else os.path.join(folder, o[1])) != want:
                    continue
                orient = o[2] if len(o) > 2 else False
                rows.append({'project': entry.get('key'), 'table': rel_table, 'plate': plate[0], 'object': o[0], 'orient': orient,
                             'down': bed_normal(orient), 'source': 'table'})
    return rows, why


def _build_table(root, rel_path, objects, exists):
    """The "build_table" fact: the plate list of the build script read with ast, compared with the saved project."""
    out = {'file': rel_path, 'literal': False, 'differs': []}
    plates, why = _table_literal(root, rel_path)
    if plates is None:
        out['why'] = why
        return out
    table = [(i + 1, _bambu_name(plate[0]), [_bambu_name(o[0]) for o in plate[1]]) for i, plate in enumerate(plates)]    # [(plate index, plate name, [object names])]
    out['literal'] = True
    out['plates'] = len(table)
    if not exists:
        out['why'] = 'the project file does not exist: not compared'
        return out
    where = lambda i, name: 'plate %d (%s)' % (i, name) if i is not None else 'no plate'
    in_table, in_project = {}, {}
    for i, pname, names in table:
        for n in names:
            in_table.setdefault(n, []).append((i, pname))
    for o in objects:
        in_project.setdefault(o['name'], []).append((o['plate_index'], o['plate']))
    order = lambda place: (place[0] is None, place[0] or 0)
    for name in sorted(set(in_table) | set(in_project)):
        a, b = sorted(in_table.get(name, []), key=order), sorted(in_project.get(name, []), key=order)
        for k in range(max(len(a), len(b))):
            if k >= len(b):
                out['differs'].append({'what': 'object missing in the project', 'object': name, 'table': '%s on %s' % (name, where(*a[k])), 'project': None})
            elif k >= len(a):
                out['differs'].append({'what': 'object missing in the table', 'object': name, 'table': None, 'project': '%s on %s' % (name, where(*b[k]))})
            elif a[k][0] != b[k][0]:
                out['differs'].append({'what': 'object on another plate', 'object': name, 'table': '%s on %s' % (name, where(*a[k])), 'project': '%s on %s' % (name, where(*b[k]))})
    plate_names = {}
    for o in objects:
        if o['plate_index'] is not None:
            plate_names[o['plate_index']] = o['plate']
    for i, pname, _names in table:
        if i in plate_names and plate_names[i] != pname:
            out['differs'].append({'what': 'plate name', 'plate_index': i, 'table': 'plate %d is "%s"' % (i, pname), 'project': 'plate %d is "%s"' % (i, plate_names[i])})
    return out


_SETTINGS_NAMES = ('PROJECT', 'PROJECT_SETTINGS', 'SETTINGS', 'project_settings')


def _build_table_settings(root, rel_path, saved, keys):
    """The fit settings a build script writes into its project, compared with the saved project (read with ast, the
    script is never run): {"literal": [keys the script gives as plain literals], "computed": [keys it may set from values
    that are not literals: an unpacked dict, a name], "differs": [{"what", "table", "project"}]} or None when the script
    assigns no settings dict at the top level (_SETTINGS_NAMES). Only keys of the fit settings are looked at.
    A dict with a "**" unpacking holds keys nobody can read without running the script: "computed" says so and those
    keys are not compared; a literal key that the unpacking could override is not compared either."""
    path = os.path.join(root, rel_path)
    try:
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return None
    node = None
    for st in tree.body:
        targets = st.targets if isinstance(st, ast.Assign) else []
        if any(isinstance(t, ast.Name) and t.id in _SETTINGS_NAMES for t in targets) and isinstance(st.value, ast.Dict):
            node = st.value
    if node is None:
        return None
    literal, computed, unpacked = {}, [], False
    for k, v in zip(node.keys, node.values):
        if k is None:
            unpacked = True
            continue
        try:
            key = ast.literal_eval(k)
        except (ValueError, SyntaxError):
            continue
        if key not in keys:
            continue
        try:
            literal[key] = ast.literal_eval(v)
        except (ValueError, SyntaxError):
            computed.append(key)
    if unpacked:
        computed = sorted(set(computed) | set(k for k in keys if k not in literal))
    out = {'literal': sorted(literal), 'computed': sorted(computed), 'differs': []}
    if not isinstance(saved, dict):
        return out
    num = lambda x: _number(x[0] if isinstance(x, list) and x else x)
    for key in sorted(literal):
        if unpacked:
            continue                                     # an unpacked dict before or after it may set the same key
        have, want = saved.get(key), literal[key]
        a, b = num(want), num(have)
        same = (a is not None and b is not None and abs(a - b) <= 5e-4) or (a is None and str(want).strip().lower() == str(have).strip().lower())
        if not same:
            out['differs'].append({'what': 'fit setting %s' % key, 'table': want, 'project': have})
    return out


def measure(root, project_entry, layout, census, declared_target=None, reg=None, fit_settings=None):
    """Facts of one .3mf project.

    root             project root (the folder with gate/project.json)
    project_entry    one entry of gate/layout.json "projects": {"key", "file", "stl_dir", "build_table"?}
    layout           gate_common.load_layout(root) (every project and STL folder, to know where else an STL is placed)
    census           gate_common.load_census(root) (part ids, STL names, current signatures)
    declared_target  gate/project.json "targets"[key] or None
    reg              gate_registry.load_default(root), or None to load it here (gate_stamp.verify needs it)
    fit_settings     the value of the parameter target.fit_settings (text or list); None = FIT_SETTINGS

    Returns {"project": key, "file": path relative to the root, "exists": bool,
             "target_declared": {"nozzle", "line_width", "material"} or None,
             "target_file": {"nozzle": 0.6, "line_width": 0.62, "materials": ["PETG"]} read from the project settings,
             "objects": [{"name": "housing", "plate": "plate name" or None (on no plate), "plate_index": 1 or None,
                          "printable": bool, "pose": geom_hash of the mesh as placed, "part": part id or None,
                          "stl": path of the STL it was written from (the last `write` event of the ledger) or None,
                          "model": geom_hash of that STL at the time of the write or None,
                          "stamp": {"state": "valid" | "stale" | "none", "why": text, "sig12": "..." or None,
                                    "target": {"nozzle", "line_width", "material"} or None}}],
             "stls": [{"path": "stl/housing.stl", "part": part id or None, "sha256", "geom_hash",
                       "stamp": "valid" | "stale" | "none", "why": text,
                       "objects": ["main:housing"]}]      "<project key>:<object>" of every registered project that holds this geometry
             "build_table": {"file": "bambulab/build_3mf.py", "literal": bool (the plate list is a pure literal that can
                             be read with ast), "differs": [{"what": "object on another plate" | "object missing in the
                             project" | "object missing in the table" | "plate name", "table": ..., "project": ...}]}
                            or None when the entry names no build table}.
    The state of a stamp comes from gate_stamp.verify(), with the parameters resolved for `declared_target`: a fit that
    is measured (M) for another project and not for this one makes the stamp stale here. An object is tied to an STL by
    the ledger's `write` events (pose hash) and, failing that, by the geometry itself (meshid.same_shape as a hint only).
    Raises gate_common.GateError when the .3mf cannot be read.

    More exactly, and with the keys added to the list above (the top of this file explains the three ties):
      an object also has "id" (its id in the file), "triangles", "tie" ("write" | "geometry" | "name" | None) and "hint"
      ({"stl": path, "same_shape": bool} when only the name fits an STL, else None). With tie "geometry" the "stl" and
      "model" are those of the STL that was proven equal, as it lies in the folder today. An unnamed plate is called
      "plate <n>". An object with several instances is on the first plate that lists it.
      an STL row also has "triangles" and "named" (objects whose name fits the file, whatever their geometry); an STL
      that cannot be read has "geom_hash" None and stamp "none" with the reason in "why".
      "target_file" is None when the file has no project settings; its "line_width" is the process setting of that name.
      a "differs" row also names its "object" (or "plate_index"); "table" and "project" are short texts or None.
      "build_table" has "why" when the table was not compared, and "plates" (how many the literal holds).
      "stl_dir", "writes" (how many `write` events of the journal name this project file) and "unread_projects" (keys of
      other registered projects that could not be read: their objects are missing from "stls"[].objects).
      "fit_settings": read_fit_settings() of the file: {"keys", "global": {key: value}, "listed": {key: bool},
      "objects": [{"name", "id", "overrides": {key: value}, "parts": [...]}] (only those that override a fit setting),
      "objects_read", "objects_unread", "object_names", "repeated", "layer_members"}; each object row also has "part"
      (its part id, when the object is tied to one). None when the file does not exist. "settings_write": None here (no write is under way: the guard fills it at a save).
      "print_settings": read_fit_settings() of the file for print_keys(declared_target) (supports, wall order): what the
      "print" block of the declared target is compared with. None when the file does not exist.
      "overhangs": overhang_facts() (the geometry pre-check of every object as it lies on its plate, with whether the
      slicer supports it; print.overhang_angle) and "slice": probes.project_slice.facts_for() (the support of every
      object in the last slice of the gate against the owner's baseline; print.slice_support_growth); both None when
      the file does not exist.
    When the project file does not exist: "exists" False, no objects, the STL folder is still listed."""
    import gate_stamp
    key = project_entry.get('key')
    rel_file = project_entry.get('file') or ''
    path = rel_file if os.path.isabs(rel_file) else os.path.join(root, rel_file)
    rel_file = gate_common.rel(root, path)
    census = census if census is not None else gate_common.load_census(root)
    layout = layout if layout is not None else gate_common.load_layout(root)
    state = {'reg': reg}

    def registry():
        if state['reg'] is None:
            import gate_registry
            state['reg'] = gate_registry.load_default(root)
        return state['reg']

    # ---- parts by STL path, STL files of every registered folder
    part_of_stl = {}
    for pid, entry in sorted((census.get('parts') or {}).items()):
        if isinstance(entry, dict) and entry.get('stl') and not entry.get('gone'):
            part_of_stl.setdefault(str(entry['stl']).replace(os.sep, '/'), pid)
    for g in layout.get('generators') or []:
        if isinstance(g, dict) and g.get('stl') and g.get('part'):
            part_of_stl.setdefault(str(g['stl']).replace(os.sep, '/'), g['part'])
    folders = []
    for entry in list(layout.get('projects') or []) + list(layout.get('documents') or []) + [project_entry]:
        d = entry.get('stl_dir') if isinstance(entry, dict) else None
        if d and d not in folders:
            folders.append(d)

    def stls_in(folder):
        full = os.path.join(root, folder)
        if not os.path.isdir(full):
            return []
        return [gate_common.rel(root, os.path.join(full, n)) for n in sorted(os.listdir(full))
                if n.lower().endswith('.stl') and os.path.isfile(os.path.join(full, n))]

    every_stl = []
    for folder in folders:
        for rel_stl in stls_in(folder):
            if rel_stl not in every_stl:
                every_stl.append(rel_stl)
    by_name = {}
    for rel_stl in every_stl:
        by_name.setdefault(_name_key(rel_stl), []).append(rel_stl)

    # ---- the write events of the journal, newest last
    writes = {}                                      # project file -> {pose: event}; an event that names its STL wins over one that does not
    for e in gate_common.read_jsonl(gate_common.events_path(root)):
        if not isinstance(e, dict) or e.get('kind') != 'write' or not e.get('pose'):
            continue
        slot = writes.setdefault(str(e.get('project') or '').replace(os.sep, '/'), {})
        old = slot.get(e['pose'])
        if old is None or e.get('model') or not old.get('model'):
            slot[e['pose']] = e

    stamp_cache = {}

    def params_here():
        """Today's parameters for the declared target of THIS project: a stamp is held against the project it lies in,
        never against another declared target that has the same nozzle and material. None when the project declares
        no target or its parameters cannot be read (gate_stamp.verify then looks itself and says why)."""
        if 'resolved' not in state:
            state['resolved'] = None
            if isinstance(declared_target, dict):
                try:
                    state['resolved'] = gate_stamp._resolved_for(root, registry(), declared_target)
                except (GateError, KeyError, TypeError):
                    state['resolved'] = None
        return state['resolved']

    def stamp_of(geom, part):
        """{"state", "why", "sig12", "target", "part"} for a mesh with this model-form geometry hash."""
        if (geom, part) in stamp_cache:
            return stamp_cache[(geom, part)]
        out = {'state': 'none', 'why': 'no stamp holds this geometry', 'sig12': None, 'target': None, 'part': part}
        found = gate_stamp.find_by_geom(root, geom) if geom else []
        if found:
            best = None
            for s in found:
                verdict, why = gate_stamp.verify(root, registry(), s, geom_hash=geom, census=census, resolved=params_here())
                if best is None or verdict == 'valid':
                    best = (verdict, why, s)
                if verdict == 'valid':
                    break
            verdict, why, s = best
            out = {'state': verdict, 'why': why, 'sig12': gate_common.sig12(s.get('sig')) if s.get('sig') else None,
                   'target': s.get('target'), 'part': s.get('part') or part}
        elif part:
            other = gate_stamp.all_stamps(root, part)
            if other:
                out = {'state': 'stale', 'sig12': gate_common.sig12(other[0].get('sig')) if other[0].get('sig') else None, 'target': None, 'part': part,
                       'why': 'this geometry has no stamp; %s was stamped at %s with another geometry' % (part, gate_common.sig12(other[0].get('sig')))}
        stamp_cache[(geom, part)] = out
        return out

    def tie(project_rel, o, stamps):
        """One object of one project -> the object row of the facts (with its stamp when `stamps`)."""
        row = {'name': o['name'], 'id': o['id'], 'plate': o['plate'], 'plate_index': o['plate_index'], 'printable': o['printable'],
               'pose': o['pose'], 'triangles': o['triangles'], 'part': None, 'stl': None, 'model': None, 'tie': None, 'hint': None}
        e = writes.get(project_rel, {}).get(o['pose'])
        if e and e.get('model'):
            row.update(tie='write', stl=e.get('stl'), model=e['model'])
            named = str(e.get('stamp') or '').split('@')[0]
            row['part'] = (named if named.count('/') == 1 else None) or part_of_stl.get(str(e.get('stl') or '').replace(os.sep, '/'))
        else:
            named = by_name.get(_name_key(o['name']), [])
            others = [s for s in every_stl if s not in named and _stl_ids(os.path.join(root, s)).get('triangles') == o['triangles']]
            for rel_stl in named + others:
                ids = _stl_ids(os.path.join(root, rel_stl))
                if ids.get('triangles') != o['triangles']:
                    continue
                proof = (o['pose'], ids['geom_hash'])
                if proof not in _PROOFS:
                    if len(_PROOFS) > 20000:
                        _PROOFS.clear()
                    _PROOFS[proof] = _same_geometry(o['xyz'], o['triangles'], os.path.join(root, rel_stl))
                if _PROOFS[proof]:
                    row.update(tie='geometry', stl=rel_stl, model=ids['geom_hash'], part=part_of_stl.get(rel_stl))
                    break
            else:
                if named:
                    ids = _stl_ids(os.path.join(root, named[0]))
                    same = bool(ids.get('shape_id')) and gate_common.sibling('meshid').same_shape(ids['shape_id'], o['shape'])
                    row.update(tie='name', part=part_of_stl.get(named[0]), hint={'stl': named[0], 'same_shape': same})
        if not stamps:
            return row
        if row['model']:
            s = stamp_of(row['model'], row['part'])
            row['part'] = row['part'] or s['part']
            row['stamp'] = {'state': s['state'], 'why': s['why'], 'sig12': s['sig12'], 'target': s['target']}
        elif row['tie'] == 'name':
            h = row['hint']
            why = ('no write event ties this mesh to an STL; by its name it is %s and the shape agrees, but that proves nothing (placed at a free angle?): place it again with the tools'
                   if h['same_shape'] else 'no write event ties this mesh to an STL; by its name it is %s, but the shape differs: the project holds another geometry') % h['stl']
            row['stamp'] = {'state': 'none', 'why': why, 'sig12': None, 'target': None}
        else:
            row['stamp'] = {'state': 'none', 'why': 'this mesh is the geometry of no STL in the registered folders and no write event names its source', 'sig12': None, 'target': None}
        return row

    # ---- the objects of every registered project
    exists = os.path.isfile(path)
    mine = _read_project(path) if exists else {'objects': [], 'settings': None}          # GateError when it cannot be read
    rows_of, unread, seen = {}, [], set()
    for entry in [project_entry] + list(layout.get('projects') or []):
        if not isinstance(entry, dict) or not entry.get('file'):
            continue
        other = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, entry['file'])
        other_rel = gate_common.rel(root, other)
        if other_rel in seen:
            continue
        seen.add(other_rel)
        if other_rel == rel_file:
            data = mine
        elif not os.path.isfile(other):
            continue
        else:
            try:
                data = _read_project(other)
            except GateError:
                unread.append(entry.get('key')); continue
        rows_of[other_rel] = (entry.get('key'), [tie(other_rel, o, other_rel == rel_file) for o in data['objects']])
    objects = rows_of[rel_file][1] if rel_file in rows_of else []

    # ---- the STL folder of this project
    holders, namesakes = {}, {}                      # model hash -> ["key:object"]; name key -> ["key:object"]
    for other_rel, (other_key, rows) in sorted(rows_of.items(), key=lambda kv: str(kv[1][0])):
        for r in rows:
            label = '%s:%s' % (other_key, r['name'])
            if r['model']:
                holders.setdefault(r['model'], []).append(label)
            namesakes.setdefault(_name_key(r['name']), []).append(label)
    stls = []
    for rel_stl in (stls_in(project_entry['stl_dir']) if project_entry.get('stl_dir') else []):
        ids = _stl_ids(os.path.join(root, rel_stl))
        part = part_of_stl.get(rel_stl)
        row = {'path': rel_stl, 'part': part, 'sha256': ids.get('sha256'), 'geom_hash': ids.get('geom_hash'), 'triangles': ids.get('triangles'),
               'named': sorted(set(namesakes.get(_name_key(rel_stl), [])))}
        if ids.get('error'):
            row.update(stamp='none', why='the file cannot be read as an STL (%s)' % ids['error'], objects=[])
        else:
            s = stamp_of(ids['geom_hash'], part)
            row.update(stamp=s['state'], why=s['why'], objects=sorted(set(holders.get(ids['geom_hash'], []))))
            row['part'] = part or s['part']
        stls.append(row)

    table = None
    if project_entry.get('build_table'):
        table = _build_table(root, str(project_entry['build_table']).replace(os.sep, '/'), objects, exists)
    public = ('name', 'id', 'plate', 'plate_index', 'printable', 'pose', 'triangles', 'part', 'stl', 'model', 'tie', 'hint', 'stamp')
    settings = None
    if exists:
        settings = read_fit_settings(path, fit_settings)
        part_of = {str(r['id']): r['part'] for r in objects if r.get('id') is not None}
        by_name_part = {r['name']: r['part'] for r in objects}
        for o in settings['objects']:
            o['part'] = part_of.get(str(o['id'])) or by_name_part.get(o['name'])
    printing = None
    if exists:
        printing = read_fit_settings(path, print_keys(declared_target))
        for o in printing['objects']:
            o['part'] = part_of.get(str(o['id'])) or by_name_part.get(o['name'])
    if table is not None:
        table['fit_settings'] = _build_table_settings(root, str(project_entry['build_table']).replace(os.sep, '/'),
                                                      (settings or {}).get('global') if exists else None, fit_keys(fit_settings) if fit_settings else list(FIT_SETTINGS))
    rows = [{k: r[k] for k in public} for r in objects]
    return {'project': key, 'file': rel_file, 'exists': exists, 'target_declared': declared_target, 'fit_settings': settings, 'settings_write': None,
            'print_settings': printing,
            'target_file': _target_of_settings(mine['settings']) if exists else None,
            'objects': rows, 'stls': stls, 'build_table': table,
            'stl_dir': project_entry.get('stl_dir'), 'writes': len(writes.get(rel_file, {})), 'unread_projects': unread,
            'overhangs': overhang_facts(root, path, rows, registry, declared_target) if exists else None,
            'placed_walls': placed_wall_facts(root, path, rows, registry, declared_target) if exists else None,
            'slice': _slice_facts(root, key, rows, path) if exists else None}


SUPPORT_KEYS = ('enable_support', 'support_type', 'support_on_build_plate_only', 'support_remove_small_overhang')


def _slice_facts(root, key, rows, path=None):
    from probes import project_slice
    try:
        return project_slice.facts_for(root, key, rows, path)
    except Exception as e:                          # unreadable state of the gate: the judge says NOT_RUN with this reason
        return {'error': '%s: %s' % (type(e).__name__, e)}


def _support_of(settings, name, oid):
    """(supports on: True | False | None, how it is known) for one object out of read_fit_settings(..., SUPPORT_KEYS).
    True means only that the slicer is asked for supports: its type (a manual type supports painted places only), "on
    the build plate only" and its own threshold angle can still leave a region bare. The text names them, and
    print.overhang_angle holds every region against the slice itself."""
    over = next((o.get('overrides') or {} for o in settings.get('objects') or [] if str(o.get('id')) == str(oid) or (oid is None and o.get('name') == name)), {})
    glob, listed = settings.get('global') or {}, settings.get('listed')
    kind = over.get('support_type', glob.get('support_type'))
    plate = over.get('support_on_build_plate_only', glob.get('support_on_build_plate_only'))
    extra = '%s%s' % (kind if kind is not None else 'type not read', ', on the build plate only' if plate not in (None, 0, 0.0, '0', 'false') else '')
    if 'enable_support' in over:
        v = over['enable_support']
        return (v not in (0, 0.0, '0', 'false', None)), 'the object overrides enable_support = %s (%s)' % (v, extra)
    v = glob.get('enable_support')
    if v is None:
        return None, 'the project settings do not hold enable_support'
    if isinstance(listed, dict) and listed.get('enable_support') is False:
        return None, 'enable_support = %s is not among the settings that differ from the preset: the slicer takes the preset\'s value' % v
    return (v not in (0, 0.0, '0', 'false')), 'enable_support = %s for the whole project (%s)' % (v, extra)


def _walls_budget():
    """s of one process for measuring the walls of placed meshes that are not measured yet (all projects together): 20,
    or GATE_WALLS_BUDGET (seconds) for a run that has more time: a mesh whose walls were cut short at 20 s is measured
    again, whole, by `GATE_WALLS_BUDGET=120 gate.py verify`."""
    try:
        v = float(os.environ.get('GATE_WALLS_BUDGET') or 20.0)
    except ValueError:
        v = 20.0
    return max(1.0, min(600.0, v))


PLACED_WALLS_BUDGET = _walls_budget()
_PLACED_SPENT = {'s': 0.0}


def placed_wall_facts(root, path, rows, registry, declared_target=None):
    """The "placed_walls" block of the facts: stl_mesh and stl_walls on the stored mesh of every object
    (probes.placed_walls), measured with the report width the export uses (twice the largest of walls.min_wall_effective,
    two line widths and walls.slit_min), within what is left of PLACED_WALLS_BUDGET in this process. {"error": text}
    when the parameters cannot be read."""
    import time
    from probes import placed_walls
    try:
        import gate_params
        reg = registry()
        names = ['walls.min_wall_effective', 'target.line_width', 'walls.slit_min']
        vals = gate_params.values(gate_params.resolve(reg, gate_params.load_overrides(root), declared_target), names)
        under = 2 * max(float(vals.get('walls.min_wall_effective') or 0), 2 * float(vals.get('target.line_width') or 0), float(vals.get('walls.slit_min') or 0))
        t = time.time()
        out = placed_walls.facts(root, path, rows, round(under, 4), budget_s=max(0.0, _walls_budget() - _PLACED_SPENT['s']))
        _PLACED_SPENT['s'] += time.time() - t
        return out
    except (GateError, KeyError, ValueError, TypeError) as e:
        return {'error': '%s: %s' % (type(e).__name__, e)}


def overhang_facts(root, path, rows, registry, declared_target=None):
    """The "overhangs" block of the facts: the geometry pre-check (probes.mesh_overhang) of every object as it lies on
    its plate, and whether the slicer puts supports under it. {"max_angle", "max_bridge" (the values measured with),
    "objects": [{"name", "part", "plate", "plate_index", "printable", "pose", "support": True | False | None,
    "support_how", "faces", "ridges", "bridges", "totals"}]} or {"error": text}."""
    from probes import mesh_overhang, project_bed
    try:
        import gate_params
        reg = registry()
        try:
            vals = gate_params.values(gate_params.resolve(reg, gate_params.load_overrides(root), declared_target), ['overhang.max_angle', 'overhang.max_bridge'])
        except GateError:
            vals = {n: reg['params'][n]['default'] for n in ('overhang.max_angle', 'overhang.max_bridge')}
        angle, bridge = float(vals['overhang.max_angle']), float(vals['overhang.max_bridge'])
        placed = {str(o['id']): o for o in project_bed.read_placed(path)['objects']}
        sup = read_fit_settings(path, list(SUPPORT_KEYS))
        out = {'max_angle': angle, 'max_bridge': bridge, 'objects': []}
        for r in rows:
            o = placed.get(str(r.get('id')))
            row = {k: r.get(k) for k in ('name', 'part', 'plate', 'plate_index', 'printable', 'pose')}
            if o is None:
                row.update(error='the mesh of this object could not be read')
            elif not project_bed.is_rotation(o['linear']):
                row.update(error='the object is scaled or sheared by its build item: not measured')
            else:
                key = (o['pose'], tuple(round(c, 9) for c in o['linear']), angle, bridge)
                if key not in _OVERHANGS:
                    if len(_OVERHANGS) > 512:
                        _OVERHANGS.clear()
                    _OVERHANGS[key] = mesh_overhang.measure(project_bed.world(o), o['triangles'], angle, bridge)
                m = _OVERHANGS[key]
                row.update(faces=m['faces'], ridges=m['ridges'], bridges=m['bridges'], totals=m['totals'], all=m.get('all'))
            row['support'], row['support_how'] = _support_of(sup, r.get('name'), r.get('id'))
            out['objects'].append(row)
        return out
    except Exception as e:                          # what could not be measured is NOT_RUN in the judge, never a pass
        return {'error': '%s: %s' % (type(e).__name__, e)}


_OVERHANGS = {}          # (pose, turn, angle, bridge) -> mesh_overhang.measure()
