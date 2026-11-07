"""Small, bounded display meshes. STEP remains the editable source of truth."""

from __future__ import annotations

import math

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.TDF import TDF_Label
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

from .cad import StepAssembly, _entry

MAX_TRIANGLES = 200_000
MAX_EDGE_SEGMENTS = 200_000
MAX_OCCURRENCES = 500
_face = getattr(TopoDS, "Face_s", None) or TopoDS.Face
_edge = getattr(TopoDS, "Edge_s", None) or TopoDS.Edge


def display_meshes(assembly: StepAssembly) -> dict:
    """Mesh each shared definition once in its own coordinate frame.

    Face triangulation is approximate (0.2 mm / 0.35 rad tolerance). Edge lines
    use actual topology, not triangle diagonals. These bounds limit the display
    payload, not OCCT's native import/meshing memory use.
    """
    assembly._check_source()
    if len(assembly._occurrences) > MAX_OCCURRENCES:
        raise ValueError(f"Viewer supports at most {MAX_OCCURRENCES} direct components")
    result = {}
    references = {}
    triangles = segments = 0
    for label in assembly._occurrences.values():
        definition = TDF_Label()
        if not assembly.shape_tool.GetReferredShape_s(label, definition):
            raise ValueError("Component has no shared shape definition")
        key = _entry(definition)
        references[key] = references.get(key, 0) + 1
        if key in result:
            continue
        shape = assembly.shape_tool.GetShape_s(definition)
        mesher = BRepMesh_IncrementalMesh(shape, 0.2, False, 0.35, False)
        if not mesher.IsDone():
            raise ValueError("Could not create a display mesh for this component")
        positions, normals, edges = [], [], []
        display_edges = []
        faces = TopExp_Explorer(shape, TopAbs_FACE)
        while faces.More():
            face = _face(faces.Current())
            face_edges = TopExp_Explorer(face, TopAbs_EDGE)
            while face_edges.More():
                edge = _edge(face_edges.Current())
                # Periodic-surface seams are parameterization boundaries, not
                # physical creases. Their sampled chords can flicker through
                # the surface and look dashed. Keep real face boundaries.
                if not BRep_Tool.IsClosed_s(edge, face):
                    display_edges.append(edge)
                face_edges.Next()
            location = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation_s(face, location)
            if triangulation is not None:
                triangles += triangulation.NbTriangles()
                if triangles > MAX_TRIANGLES:
                    raise ValueError(f"Display exceeds {MAX_TRIANGLES:,} triangles; use a smaller assembly")
                # Smooth within each CAD face, never across sharp face boundaries.
                # Weld coincident seam/pole nodes only for normal accumulation.
                points = [triangulation.Node(i).Transformed(location.Transformation())
                          for i in range(1, triangulation.NbNodes() + 1)]
                keys = [tuple(round(v, 9) for v in (p.X(), p.Y(), p.Z())) for p in points]
                sums = {key: [0.0, 0.0, 0.0] for key in keys}
                face_triangles = []
                for index in range(1, triangulation.NbTriangles() + 1):
                    indices = list(triangulation.Triangle(index).Get())
                    if face.Orientation() == TopAbs_REVERSED:
                        indices.reverse()
                    face_triangles.append(indices)
                    a, b, c = [points[i - 1] for i in indices]
                    u = (b.X()-a.X(), b.Y()-a.Y(), b.Z()-a.Z())
                    v = (c.X()-a.X(), c.Y()-a.Y(), c.Z()-a.Z())
                    normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
                    for node in indices:
                        total = sums[keys[node - 1]]
                        for axis in range(3):
                            total[axis] += normal[axis]
                for indices in face_triangles:
                    for node in indices:
                        point = points[node - 1]
                        positions.extend((point.X(), point.Y(), point.Z()))
                        normal = sums[keys[node - 1]]
                        length = math.hypot(*normal)
                        normals.extend(value / length if length else 0.0 for value in normal)
            faces.Next()
        for edge in display_edges:
            if not BRep_Tool.Degenerated_s(edge):
                curve = BRepAdaptor_Curve(edge)
                sample = GCPnts_QuasiUniformDeflection(curve, 0.2)
                if sample.IsDone():
                    count = sample.NbPoints()
                    segments += max(0, count - 1)
                    if segments > MAX_EDGE_SEGMENTS:
                        raise ValueError("Display has too many edge segments; use a smaller assembly")
                    for index in range(1, count):
                        for point in (sample.Value(index), sample.Value(index + 1)):
                            edges.extend((point.X(), point.Y(), point.Z()))
        if not positions:
            raise ValueError("Component has no renderable faces; surface/solid assemblies are required")
        if not all(math.isfinite(value) for value in positions + normals + edges):
            raise ValueError("Display geometry contains non-finite coordinates")
        result[key] = {"positions": positions, "normals": normals, "edges": edges}
    # Shared buffers save memory, but each occurrence still adds draw work.
    # Bound both unique geometry and the complete instanced scene.
    if sum(len(result[key]["positions"]) // 9 * count for key, count in references.items()) > MAX_TRIANGLES:
        raise ValueError(f"Instanced display exceeds {MAX_TRIANGLES:,} triangles; use a smaller assembly")
    if sum(len(result[key]["edges"]) // 6 * count for key, count in references.items()) > MAX_EDGE_SEGMENTS:
        raise ValueError("Instanced display has too many edge segments; use a smaller assembly")
    return result
