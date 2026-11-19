"""A fake adsk that COUNTS: a toothed pulley with a flange, a bore with a bearing seat and six screw holes, and two
neighbours of a few thousand faces each. Every attribute read on a fake Fusion object is one API call, counted under
the stage the probe says it is in (COUNTER.stage, a callable); pointContainment is counted apart ("pc"). The
coordinates of a point (x, y, z) are not counted: in this fake a point is a plain value.

The shapes are analytic and only roughly consistent with the faces they list (a tooth flank answers pointContainment
from the smooth tooth curve, and lists itself as an arc or a line through samples of it): the census must be
deterministic on it and split over calls exactly as in one call, which is what the tests ask. Its numbers are not a
model of a real pulley. Used by tests/test_census_steps.py and by the measurement it documents."""
import contextlib, math, sys, types

PI2 = 2.0 * math.pi


class Counter:
    def __init__(self):
        self.stage = lambda: 'other'
        self.api, self.pc, self.on = {}, {}, True

    def hit(self, kind):
        if not self.on:
            return
        s = self.stage()
        book = self.pc if kind == 'pc' else self.api
        book[s] = book.get(s, 0) + 1

    def reset(self):
        self.api, self.pc = {}, {}


COUNTER = Counter()


class Api:
    """A Fusion object: every public attribute read is one API call (pointContainment is counted as "pc")."""
    def __getattribute__(self, name):
        if name[:1] != '_':
            COUNTER.hit('pc' if name == 'pointContainment' else 'api')
        return object.__getattribute__(self, name)


class V:
    """Point3D / Vector3D, cm (a plain value here: its coordinates are not counted)"""
    def __init__(self, x, y, z): self.x, self.y, self.z = float(x), float(y), float(z)
    def asArray(self): return (self.x, self.y, self.z)


def P(p):
    return V(p[0] / 10.0, p[1] / 10.0, p[2] / 10.0)


class Coll(Api):
    def __init__(self, items): self._items = list(items)
    @property
    def count(self): return len(self._items)
    def item(self, i): return self._items[i]
    def __iter__(self):                                 # a walk over a collection: one read per item, as Fusion fetches them
        for x in self._items:
            COUNTER.hit('api')
            yield x
    def __len__(self): return len(self._items)


class Geo(Api):
    def __init__(self, kind, **kw):
        self._kind = kind
        for k, v in kw.items(): object.__setattr__(self, k, v)
    @property
    def objectType(self): return 'adsk::core::' + self._kind


class Vertex(Api):
    def __init__(self, p): self.geometry = P(p)


class Evaluator(Api):
    def __init__(self, fn): self._fn = fn
    def getParameterExtents(self): return (True, 0.0, 1.0)
    def getPointAtParameter(self, t): return (True, P(self._fn(t)))


class Edge(Api):
    def __init__(self, kind, fn, centre=None, radius=None):
        self.geometry = Geo(kind, center=None if centre is None else P(centre), radius=None if radius is None else radius / 10.0)
        self.startVertex, self.endVertex = Vertex(fn(0.0)), Vertex(fn(1.0))
        self.evaluator = Evaluator(fn)


class Loop(Api):
    def __init__(self, edges, outer=False): self.isOuter, self.edges = outer, Coll(edges)


class BBox(Api):
    def __init__(self, lo, hi): self.minPoint, self.maxPoint = P(lo), P(hi)


class Face(Api):
    def __init__(self, geometry, on, loops, area=1.0, box=None):
        self.geometry, self.pointOnFace, self.centroid, self.area = geometry, P(on), P(on), area / 100.0
        self.loops = Coll(loops)
        self.edges = Coll([e for lp in loops for e in lp.edges])
        lo, hi = box or (on, on)
        self.boundingBox = BBox(lo, hi)


def line(a, b):
    return Edge('Line3D', lambda t: tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def circle(c, r, z_axis=True):
    return Edge('Circle3D', lambda t: (c[0] + r * math.cos(PI2 * t), c[1] + r * math.sin(PI2 * t), c[2]), c, r)


def cyl_face(o, r, z0, z1, axis=(0.0, 0.0, 1.0)):
    """a full vertical cylinder face between two circles"""
    g = Geo('Cylinder', origin=P(o), axis=V(*axis), radius=r / 10.0)
    c0, c1 = (o[0], o[1], z0), (o[0], o[1], z1)
    return Face(g, (o[0] + r, o[1], (z0 + z1) / 2.0), [Loop([circle(c0, r)]), Loop([circle(c1, r)])], PI2 * r * (z1 - z0),
                ((o[0] - r, o[1] - r, z0), (o[0] + r, o[1] + r, z1)))


def plane(normal, on, loops, area=1.0):
    return Face(Geo('Plane', normal=V(*normal)), on, loops, area)


class Pulley:
    """mm, part frame. A disc z 0..height with `teeth` teeth of `per` edges each on its rim (every other edge an arc,
    the rest lines), a flange z -2..0 a little wider than the teeth, a through bore d16 with a d22 seat 7 deep from the
    top, and `holes` d3.4 through holes on a 50 mm circle."""

    def __init__(self, teeth=84, per=30, r0=40.0, depth=1.2, height=10.0, holes=6, flange=3.0):
        self.teeth, self.per, self.r0, self.depth, self.h, self.n_holes = teeth, per, r0, depth, height, holes
        self.m = teeth * per
        self.rf = r0 + flange
        self.holes = [(25.0 * math.cos(PI2 * k / holes), 25.0 * math.sin(PI2 * k / holes)) for k in range(holes)]

    def radius(self, th):
        ph = (th / PI2 * self.teeth) % 1.0
        return self.r0 - self.depth * (0.5 - 0.5 * math.cos(PI2 * ph))

    def at(self, t, z):
        """the rim at parameter t (0..m edges round), height z"""
        th = PI2 * t / self.m
        r = self.radius(th)
        return (r * math.cos(th), r * math.sin(th), z)

    def inside(self, p):
        x, y, z = p
        if not -2.0 < z < self.h:
            return False
        rho = math.hypot(x, y)
        if rho >= (self.rf if z < 0.0 else self.radius(math.atan2(y, x) % PI2)):
            return False
        if rho < (11.0 if z > 3.0 else 8.0):
            return False
        return all(math.hypot(x - hx, y - hy) >= 1.7 for hx, hy in self.holes)

    def box(self):
        return (-self.rf, -self.rf, -2.0), (self.rf, self.rf, self.h)

    def rim_edges(self, z):
        out = []
        for k in range(self.m):
            fn = (lambda t, k=k, z=z: self.at(k + t, z))
            out.append(Edge('Arc3D' if k % 2 == 0 else 'Line3D', fn))
        return out

    def faces(self):
        out = []
        for k in range(self.m):                         # the flanks of the teeth: arcs are cylinders, lines are planes
            a, b, mid = self.at(k, 0.0), self.at(k + 1, 0.0), self.at(k + 0.5, 0.0)
            a1, b1 = self.at(k, self.h), self.at(k + 1, self.h)
            sides = [line(a, a1), line(b, b1)]
            if k % 2 == 0:
                c, r = circumcircle(a, mid, b)
                g = Geo('Cylinder', origin=P((c[0], c[1], 0.0)), axis=V(0.0, 0.0, 1.0), radius=r / 10.0)
                bot, top = Edge('Arc3D', lambda t, k=k: self.at(k + t, 0.0)), Edge('Arc3D', lambda t, k=k: self.at(k + t, self.h))
                out.append(Face(g, (mid[0], mid[1], self.h / 2.0), [Loop([bot, sides[1], top, sides[0]], outer=True)]))
            else:
                n = (b[1] - a[1], a[0] - b[0], 0.0)
                ln = math.hypot(n[0], n[1])
                out.append(plane((n[0] / ln, n[1] / ln, 0.0), (mid[0], mid[1], self.h / 2.0),
                                 [Loop([line(a, b), sides[1], line(b1, a1), sides[0]], outer=True)]))
        h = self.h
        out.append(plane((0.0, 0.0, 1.0), (30.0, 9.0, h), [Loop(self.rim_edges(h), outer=True), Loop([circle((0.0, 0.0, h), 11.0)])]
                         + [Loop([circle((x, y, h), 1.7)]) for x, y in self.holes]))
        out.append(plane((0.0, 0.0, 1.0), (self.r0 + 1.5, 0.0, 0.0), [Loop([circle((0.0, 0.0, 0.0), self.rf)], outer=True),
                                                                       Loop(self.rim_edges(0.0))]))
        out.append(plane((0.0, 0.0, -1.0), (30.0, 9.0, -2.0), [Loop([circle((0.0, 0.0, -2.0), self.rf)], outer=True), Loop([circle((0.0, 0.0, -2.0), 8.0)])]
                         + [Loop([circle((x, y, -2.0), 1.7)]) for x, y in self.holes]))
        out.append(cyl_face((0.0, 0.0, 0.0), self.rf, -2.0, 0.0))
        out.append(cyl_face((0.0, 0.0, 0.0), 11.0, 3.0, h))
        out.append(cyl_face((0.0, 0.0, 0.0), 8.0, -2.0, 3.0))
        out.append(plane((0.0, 0.0, 1.0), (9.5, 0.0, 3.0), [Loop([circle((0.0, 0.0, 3.0), 11.0)], outer=True), Loop([circle((0.0, 0.0, 3.0), 8.0)])]))
        for x, y in self.holes:
            out.append(cyl_face((x, y, 0.0), 1.7, -2.0, h))
        return out


def circumcircle(a, b, c):
    ax, ay, bx, by, cx, cy = a[0], a[1], b[0], b[1], c[0], c[1]
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return (0.0, 0.0), 1e6
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


class Filler:
    """A neighbour of many faces: `n` flat faces, some cylinders across, and the coaxial cylinders `coax` [(r, z0, z1)]
    on the z axis. Only its box and its faces are read by the census."""

    def __init__(self, lo, hi, n, coax=(), cross=0):
        self.lo, self.hi, self.n, self.coax, self.cross = lo, hi, n, list(coax), cross

    def inside(self, p):
        return all(self.lo[i] < p[i] < self.hi[i] for i in range(3))

    def box(self):
        return self.lo, self.hi

    def faces(self):
        out = [cyl_face((0.0, 0.0, 0.0), r, z0, z1) for r, z0, z1 in self.coax]
        for k in range(self.cross):                     # cylinders whose axes lie across: never coaxial with a hole of the part
            th = PI2 * k / max(1, self.cross)
            ax = (math.cos(th), math.sin(th), 0.0)
            g = Geo('Cylinder', origin=P((self.lo[0] + 1.0, self.lo[1] + 1.0, (self.lo[2] + self.hi[2]) / 2.0)), axis=V(*ax), radius=0.05)
            out.append(Face(g, (self.lo[0] + 1.0, self.lo[1] + 1.0, self.lo[2]), [Loop([])], box=(self.lo, self.hi)))
        while len(out) < self.n:                        # facets
            k = len(out)
            out.append(plane((0.0, 0.0, 1.0), (self.lo[0] + (k % 97) * 0.01, self.lo[1], self.hi[2]), [Loop([])]))
        return out


class Body(Api):
    isSolid = True

    def __init__(self, name, shape, rev, on=True):
        self.name, self.isLightBulbOn, self.revisionId = name, on, rev
        self._shape, self._faces = shape, None
        lo, hi = shape.box()
        self.volume, self.area = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2]) / 1000.0, 1.0
        self.edges, self.vertices = Coll(range(10)), Coll(range(10))
        self.boundingBox = BBox(lo, hi)
        self.parentComponent = None

    @property
    def faces(self):
        if self._faces is None:
            was, COUNTER.on = COUNTER.on, False         # building the fake is no API call
            try:
                self._faces = Coll(self._shape.faces())
            finally:
                COUNTER.on = was
        return self._faces

    def pointContainment(self, p):
        return 0 if self._shape.inside((p.x * 10.0, p.y * 10.0, p.z * 10.0)) else 2


class Comp(Api):
    def __init__(self, cid, name, bodies=()):
        self.id, self.name, self.bRepBodies, self.meshBodies = cid, name, Coll(bodies), Coll([])


class Design(Api):
    def __init__(self, root): self.rootComponent = root


class Mat(Api):
    def __init__(self): pass
    def asArray(self): return (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


class Shifted(Mat):
    """the transform of an occurrence moved by (dx, dy, dz) mm"""
    def __init__(self, dx=0.0, dy=0.0, dz=0.0): self._d = (dx / 10.0, dy / 10.0, dz / 10.0)
    def asArray(self): return (1.0, 0.0, 0.0, self._d[0], 0.0, 1.0, 0.0, self._d[1], 0.0, 0.0, 1.0, self._d[2], 0.0, 0.0, 0.0, 1.0)


class Occ(Api):
    def __init__(self, comp, path):
        self.component, self.fullPathName, self.isLightBulbOn, self.assemblyContext = comp, path, True, None
        self.transform2 = Mat()
        self.bRepBodies = comp.bRepBodies


_REV = [0]


def scene(teeth=84, per=30, filler=(2000, 3000), cross=(40, 400), screw=True, depth=1.2):
    """[occurrences] of the pulley ("Pulley", printed) and its neighbours: a shaft through the bore and a bearing in the
    seat (ref bodies of `filler` faces each, with `cross` cylinders across), a screw in the first hole, and a body far
    away. The revision ids are new for every scene: the signature memory of _fusion.py never mixes two scenes. depth 0
    makes the rim a circle: the outline in the flange face is then convex and every one of its points is kept."""
    _REV[0] += 1
    rev = lambda name: 'rev-%d-%s' % (_REV[0], name)
    pulley = Pulley(teeth, per, depth=depth)
    comps = [Comp('c-pulley', 'Pulley', [Body('Body1', pulley, rev('pulley'))]),
             Comp('c-shaft', 'ref shaft', [Body('Body1', Filler((-7.9, -7.9, -30.0), (7.9, 7.9, 30.0), filler[0], [(7.9, -30.0, 30.0)], cross[0]), rev('shaft'))]),
             Comp('c-brg', 'ref bearing 6801', [Body('Body1', Filler((-11.0, -11.0, 3.0), (11.0, 11.0, 10.0), filler[1], [(11.0, 3.0, 10.0), (8.0, 3.0, 10.0)], cross[1]), rev('brg'))]),
             Comp('c-far', 'ref far', [Body('Body1', Filler((200.0, 200.0, 0.0), (210.0, 210.0, 5.0), 10), rev('far'))])]
    if screw:
        x, y = pulley.holes[0]
        sc = Filler((x - 1.5, y - 1.5, -5.0), (x + 1.5, y + 1.5, 12.0), 3)
        sc.faces = lambda x=x, y=y: [cyl_face((x, y, 0.0), 1.5, -5.0, 12.0), cyl_face((x, y, 0.0), 2.75, 10.0, 12.0)]
        comps.append(Comp('c-screw', 'ref screw M3x16', [Body('Body1', sc, rev('screw'))]))
    return [Occ(c, c.name + ':1') for c in comps], pulley


@contextlib.contextmanager
def fake_adsk(occs):
    root = Comp('root', 'Demo Assembly')
    object.__setattr__(root, 'allOccurrences', Coll(occs))
    design = Design(root)
    doc = types.SimpleNamespace(name='Demo Assembly v7', dataFile=types.SimpleNamespace(versionNumber=7, id='file-1'),
                                products=types.SimpleNamespace(itemByProductType=lambda t: design))
    app = types.SimpleNamespace(documents=[doc], activeProduct=design)
    core, fusion, adsk = types.ModuleType('adsk.core'), types.ModuleType('adsk.fusion'), types.ModuleType('adsk')
    core.Application = types.SimpleNamespace(get=lambda: app)
    core.Point3D = core.Vector3D = types.SimpleNamespace(create=lambda x, y, z: V(x, y, z))
    fusion.Design = types.SimpleNamespace(cast=lambda x: x)
    fusion.PointContainment = types.SimpleNamespace(PointInsidePointContainment=0, PointOnPointContainment=1, PointOutsidePointContainment=2)
    fusion.TemporaryBRepManager = types.SimpleNamespace(get=lambda: None)
    adsk.core, adsk.fusion = core, fusion
    mods = {'adsk': adsk, 'adsk.core': core, 'adsk.fusion': fusion}
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = old
