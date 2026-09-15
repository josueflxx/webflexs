"""Build the illustrative U-bolt GLB using only the Python standard library.

Dimensions are in metres: 12.7 mm rod, 120 mm internal width, 180 mm useful
length. This is a display asset, not a manufacturing drawing. Run from anywhere
with `python scripts/build_clamp_model.py` to regenerate the committed asset.
"""
import json
import math
from pathlib import Path
import struct


class Mesh:
    def __init__(self):
        self.positions = []
        self.normals = []
        self.indices = []

    def vertex(self, position, normal):
        self.positions.extend(position)
        self.normals.extend(normal)

    def rings(self, rings):
        start = len(self.positions) // 3
        count = len(rings[0])
        for ring in rings:
            for position, normal in ring:
                self.vertex(position, normal)
        for row in range(len(rings) - 1):
            for col in range(count):
                a = start + row * count + col
                b = start + row * count + (col + 1) % count
                c, d = a + count, b + count
                self.indices.extend((a, c, b, b, c, d))


def make_bolt():
    mesh = Mesh()
    rod = 0.00635
    bend = 0.06635
    center_y = 0.120
    # Continuous centreline, travelling up the left leg and down the right.
    path = [(-bend, 0, 0, 1)]
    for i in range(49):
        angle = math.pi - math.pi * i / 48
        path.append((bend * math.cos(angle), center_y + bend * math.sin(angle),
                     math.sin(angle), -math.cos(angle)))
    path.append((bend, 0, 0, -1))
    rings = []
    for x, y, tx, ty in path:
        ring = []
        for j in range(24):
            angle = 2 * math.pi * j / 24
            normal = (ty * math.cos(angle), -tx * math.cos(angle), math.sin(angle))
            ring.append(((x + rod * normal[0], y + rod * normal[1], rod * normal[2]), normal))
        rings.append(ring)
    mesh.rings(rings)
    # Close the two exposed ends.
    for x in (-bend, bend):
        start = len(mesh.positions) // 3
        mesh.vertex((x, 0, 0), (0, -1, 0))
        for j in range(24):
            angle = 2 * math.pi * j / 24
            mesh.vertex((x + rod * math.cos(angle), 0, rod * math.sin(angle)), (0, -1, 0))
        for j in range(24):
            mesh.indices.extend((start, start + j + 1, start + (j + 1) % 24 + 1))
    # Shallow helical ridges provide visible thread detail without textures.
    for x in (-bend, bend):
        rings = []
        turns, steps = 26, 26 * 16
        for i in range(steps + 1):
            angle = 2 * math.pi * turns * i / steps
            y = 0.001 + 0.060 * i / steps
            ring = []
            for j in range(5):
                section = 2 * math.pi * j / 5
                normal = (math.cos(angle) * math.cos(section), math.sin(section),
                          math.sin(angle) * math.cos(section))
                ring.append(((x + rod * math.cos(angle) + 0.00055 * normal[0],
                              y + 0.00055 * normal[1],
                              rod * math.sin(angle) + 0.00055 * normal[2]), normal))
            # Reverse the section for the helix's upward winding.
            rings.append(list(reversed(ring)))
        mesh.rings(rings)
    return mesh


def write_glb(mesh, destination):
    binary = bytearray()
    views, accessors = [], []

    def add(values, component_type, kind, target, bounds=False):
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        code = 'f' if component_type == 5126 else 'H'
        binary.extend(struct.pack('<' + code * len(values), *values))
        views.append({'buffer': 0, 'byteOffset': offset,
                      'byteLength': len(binary) - offset, 'target': target})
        accessor = {'bufferView': len(views) - 1, 'componentType': component_type,
                    'count': len(values) // (3 if kind == 'VEC3' else 1), 'type': kind}
        if bounds:
            accessor['min'] = [min(values[i::3]) for i in range(3)]
            accessor['max'] = [max(values[i::3]) for i in range(3)]
        accessors.append(accessor)
        return len(accessors) - 1

    position = add(mesh.positions, 5126, 'VEC3', 34962, bounds=True)
    normal = add(mesh.normals, 5126, 'VEC3', 34962)
    indices = add(mesh.indices, 5123, 'SCALAR', 34963)
    document = {
        'asset': {'version': '2.0', 'generator': 'FLEXS illustrative clamp generator'},
        'scene': 0, 'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0, 'name': 'Abrazadera curva de referencia'}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': position, 'NORMAL': normal},
                                    'indices': indices, 'material': 0}]}],
        'materials': [{'name': 'Acero satinado', 'pbrMetallicRoughness': {
            'baseColorFactor': [0.56, 0.60, 0.65, 1], 'metallicFactor': 0.85,
            'roughnessFactor': 0.3}}],
        'buffers': [{'byteLength': len(binary)}], 'bufferViews': views, 'accessors': accessors,
    }
    data = json.dumps(document, separators=(',', ':'), ensure_ascii=True).encode()
    data += b' ' * (-len(data) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    glb = struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(data) + 8 + len(binary))
    glb += struct.pack('<I4s', len(data), b'JSON') + data
    glb += struct.pack('<I4s', len(binary), b'BIN\0') + binary
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(glb)
    print(f'{destination.name}: {len(glb):,} bytes; {len(mesh.indices) // 3:,} triangles')


if __name__ == '__main__':
    write_glb(make_bolt(), Path(__file__).resolve().parents[1] /
              'core/static/core/models/abrazadera-curva.glb')
