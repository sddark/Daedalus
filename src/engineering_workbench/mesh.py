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
        positions, edges = [], []
        faces = TopExp_Explorer(shape, TopAbs_FACE)
        while faces.More():
            face = _face(faces.Current())
            location = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation_s(face, location)
            if triangulation is not None:
                triangles += triangulation.NbTriangles()
                if triangles > MAX_TRIANGLES:
                    raise ValueError(f"Display exceeds {MAX_TRIANGLES:,} triangles; use a smaller assembly")
                for index in range(1, triangulation.NbTriangles() + 1):
                    indices = list(triangulation.Triangle(index).Get())
                    if face.Orientation() == TopAbs_REVERSED:
                        indices.reverse()
                    for node in indices:
                        point = triangulation.Node(node).Transformed(location.Transformation())
                        positions.extend((point.X(), point.Y(), point.Z()))
            faces.Next()
        explorer = TopExp_Explorer(shape, TopAbs_EDGE)
        while explorer.More():
            edge = _edge(explorer.Current())
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
            explorer.Next()
        if not positions:
            raise ValueError("Component has no renderable faces; surface/solid assemblies are required")
        if not all(math.isfinite(value) for value in positions + edges):
            raise ValueError("Display geometry contains non-finite coordinates")
        result[key] = {"positions": positions, "edges": edges}
    # Shared buffers save memory, but each occurrence still adds draw work.
    # Bound both unique geometry and the complete instanced scene.
    if sum(len(result[key]["positions"]) // 9 * count for key, count in references.items()) > MAX_TRIANGLES:
        raise ValueError(f"Instanced display exceeds {MAX_TRIANGLES:,} triangles; use a smaller assembly")
    if sum(len(result[key]["edges"]) // 6 * count for key, count in references.items()) > MAX_EDGE_SEGMENTS:
        raise ValueError("Instanced display has too many edge segments; use a smaller assembly")
    return result
