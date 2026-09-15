"""Assembly variant inspired by the user's plate-and-nuts reference.

Generates a separate GLB, without overwriting the original round clamp.
Dimensions are illustrative, in metres; holes and nut bores are actual geometry.
"""
import math
from pathlib import Path

from build_clamp_model import Mesh, write_glb


def face(mesh, points, outward):
    a, b, c = points[:3]
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
    length = math.sqrt(sum(n*n for n in normal))
    if length < 1e-12:
        return
    normal = [n / length for n in normal]
    if sum(normal[i] * outward[i] for i in range(3)) < 0:
        points = list(reversed(points))
        normal = [-n for n in normal]
    start = len(mesh.positions) // 3
    for point in points:
        mesh.vertex(point, normal)
    for i in range(1, len(points) - 1):
        mesh.indices.extend((start, start+i, start+i+1))


def body(mesh, half, rod, profile='plana'):
    top, bend = 0.260, 0.025
    path = [(-half, 0, 0, 1)]
    if profile == 'plana':
        # Preserve the approved flat-top model byte for byte.
        for i in range(25):
            angle = math.pi - i * math.pi / 48
            path.append((-half+bend+bend*math.cos(angle), top-bend+bend*math.sin(angle),
                         math.sin(angle), -math.cos(angle)))
        for i in range(25):
            angle = math.pi/2 - i * math.pi / 48
            path.append((half-bend+bend*math.cos(angle), top-bend+bend*math.sin(angle),
                         math.sin(angle), -math.cos(angle)))
    else:
        # Round crown versus shallower elliptical crown, both tangent to the legs.
        rise = half if profile == 'curva' else 0.030
        for i in range(49):
            angle = math.pi - i * math.pi / 48
            tx, ty = half*math.sin(angle), -rise*math.cos(angle)
            magnitude = math.hypot(tx, ty)
            path.append((half*math.cos(angle), top-rise+rise*math.sin(angle),
                         tx/magnitude, ty/magnitude))
    path.append((half, 0, 0, -1))
    rings = []
    for x, y, tx, ty in path:
        ring = []
        for j in range(32):
            a = 2*math.pi*j/32
            n = (ty*math.cos(a), -tx*math.cos(a), math.sin(a))
            ring.append(((x+rod*n[0], y+rod*n[1], rod*n[2]), n))
        rings.append(ring)
    mesh.rings(rings)
    for x in (-half, half):
        for j in range(32):
            a, b = 2*math.pi*j/32, 2*math.pi*(j+1)/32
            face(mesh, [(x,0,0),(x+rod*math.cos(a),0,rod*math.sin(a)),
                        (x+rod*math.cos(b),0,rod*math.sin(b))], (0,-1,0))
        rings = []
        steps = 34*16
        for i in range(steps+1):
            angle = 2*math.pi*34*i/steps
            y = .001+.078*i/steps
            ring = []
            for j in range(5):
                a = 2*math.pi*j/5
                n = (math.cos(angle)*math.cos(a), math.sin(a), math.sin(angle)*math.cos(a))
                ring.append(((x+rod*math.cos(angle)+.00055*n[0], y+.00055*n[1],
                              rod*math.sin(angle)+.00055*n[2]), n))
            rings.append(list(reversed(ring)))
        mesh.rings(rings)


def plate_half(mesh, cx, rod, left, right):
    # Two rectangular halves meeting at x=0 form a plate with two real holes.
    depth, bottom, top = .014, .043, .051
    angles = {2*math.pi*i/48 for i in range(48)}
    for x in (left, right):
        for z in (-depth, depth):
            angles.add(math.atan2(z, x-cx) % (2*math.pi))
    angles = sorted(angles)
    hole, outer = [], []
    for a in angles:
        dx, dz = math.cos(a), math.sin(a)
        distance = min((right-cx)/dx if dx > 1e-10 else (left-cx)/dx if dx < -1e-10 else math.inf,
                       depth/abs(dz) if abs(dz) > 1e-10 else math.inf)
        outer.append((cx+dx*distance, dz*distance))
        hole.append((cx+(rod+.0007)*dx, (rod+.0007)*dz))
    def point(p, y):
        return (p[0], y, p[1])
    for i in range(len(angles)):
        j = (i+1) % len(angles)
        a,b,c,d = hole[i],hole[j],outer[j],outer[i]
        face(mesh,[point(p,top) for p in (a,b,c,d)],(0,1,0))
        face(mesh,[point(p,bottom) for p in (a,b,c,d)],(0,-1,0))
        face(mesh,[point(a,bottom),point(b,bottom),point(b,top),point(a,top)],
             (cx-(a[0]+b[0])/2,0,-(a[1]+b[1])/2))
        # Omit the internal join so the two halves share one open seam.
        if abs(c[0]) + abs(d[0]) > 1e-10:
            face(mesh,[point(c,bottom),point(d,bottom),point(d,top),point(c,top)],
                 ((c[0]+d[0])/2-cx,0,(c[1]+d[1])/2))


def nut(mesh, cx, rod):
    hole, radius = rod+.0001, .0115
    bottom, top, bevel = .005, .022, .0014
    angles = [2*math.pi*i/48 for i in range(48)]
    def hex_radius(a):
        # Hexagon vertices at 0, 60, ... degrees, with broad planar faces.
        relative = (a % (math.pi/3))-math.pi/6
        return radius*math.cos(math.pi/6)/math.cos(relative)
    levels = [(bottom,.89),(bottom+bevel,1),(top-bevel,1),(top,.89)]
    for i,a in enumerate(angles):
        b = angles[(i+1)%len(angles)]
        def point(angle,y,scale=1,inner=False):
            r = hole if inner else hex_radius(angle)*scale
            return (cx+r*math.cos(angle),y,r*math.sin(angle))
        middle = a + math.pi/48
        for (lo,s0),(hi,s1) in zip(levels,levels[1:]):
            face(mesh,[point(a,lo,s0),point(b,lo,s0),point(b,hi,s1),point(a,hi,s1)],
                 (math.cos(middle), 0, math.sin(middle)))
        for y,sign in ((bottom,-1),(top,1)):
            face(mesh,[point(a,y,.89),point(b,y,.89),point(b,y,inner=True),point(a,y,inner=True)],(0,sign,0))
        face(mesh,[point(a,bottom,inner=True),point(b,bottom,inner=True),
                   point(b,top,inner=True),point(a,top,inner=True)],(-math.cos(middle),0,-math.sin(middle)))


def make_assembly(profile='plana'):
    if profile not in ('plana', 'curva', 'semicurva'):
        raise ValueError(f'Unknown clamp profile: {profile}')
    mesh = Mesh()
    half, rod = .045, .00635
    body(mesh,half,rod,profile)
    plate_half(mesh,-half,rod,-.067,0)
    plate_half(mesh,half,rod,0,.067)
    for x in (-half,half):
        nut(mesh,x,rod)
    return mesh


if __name__ == '__main__':
    destination = Path(__file__).resolve().parents[1] / 'core/static/core/models'
    for profile, filename in (
        ('plana', 'abrazadera-plaqueta-tuercas.glb'),
        ('curva', 'abrazadera-curva-plaqueta-tuercas.glb'),
        ('semicurva', 'abrazadera-semicurva-plaqueta-tuercas.glb'),
    ):
        write_glb(make_assembly(profile), destination / filename)
