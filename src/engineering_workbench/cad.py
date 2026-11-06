"""STEP assembly import, occurrence translation, and verified export primitives.

This first slice deliberately supports one top-level assembly and its immediate
component occurrences. Nested assemblies are not flattened. Their children
cannot be edited independently by this API.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import tempfile
from typing import Sequence
from uuid import NAMESPACE_URL, uuid5

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_Tool
try:
    from OCP.collections import Sequence_TDF_Label as TDF_LabelSequence
except ImportError:  # Compatibility with the original OCCT 7.8 proof environment.
    from OCP.TDF import TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Compound
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_Location, XCAFDoc_ShapeTool


class UnsupportedAssemblyError(ValueError):
    """The file is outside this intentionally small proof's assembly scope."""


def file_sha256(path: Path) -> str:
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _document() -> TDocStd_Document:
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    XCAFDoc_DocumentTool.SetLengthUnit_s(document, 0.001)
    return document


def _entry(label: TDF_Label) -> str:
    result = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, result)
    return result.ToCString()


def _name(label: TDF_Label) -> str:
    attribute = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return attribute.Get().ToExtString()
    return _entry(label)


def _set_name(label: TDF_Label, name: str) -> None:
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))


def _translation(values: Sequence[float]) -> tuple[float, float, float]:
    try:
        if isinstance(values, (str, bytes)) or len(values) != 3:
            raise ValueError
        if any(isinstance(value, bool) for value in values):
            raise ValueError
        result = tuple(float(value) for value in values)
        if not all(math.isfinite(value) for value in result):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Translation must contain three finite numbers in millimetres") from error
    return result


def _write_document(document: TDocStd_Document, path: Path) -> None:
    """Write a complete STEP to a temporary sibling and publish it atomically.

    Callers must serialize kernel work: these OCCT STEP options are process-global.
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.SetColorMode(True)
    # Export explicitly in mm. Never infer units from a filename or coordinates.
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetIVal_s("write.step.assembly", 1)
    if not writer.Transfer(document, STEPControl_AsIs):
        raise RuntimeError("OCCT could not transfer the assembly for STEP export")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".step-export-", suffix=".step", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        if writer.Write(str(temporary_path)) != IFSelect_RetDone:
            raise RuntimeError("OCCT could not write the STEP export")
        # Atomic create-if-absent: a concurrent file appearing at the destination
        # must not be overwritten between the caller's check and publication.
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def create_two_instance_fixture(path: Path) -> None:
    """Write a named assembly with two placements of one 10 x 20 x 30 mm box."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite fixture: {path}")
    document = _document()
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    compound = TopoDS_Compound()
    BRep_Builder().MakeCompound(compound)
    root = shape_tool.AddShape(compound, True)
    _set_name(root, "TwoInstanceAssembly")
    definition = shape_tool.AddShape(BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), False)
    _set_name(definition, "SharedBox")
    for name, position in (("Fixed", (0.0, 0.0, 0.0)), ("Movable", (30.0, 0.0, 0.0))):
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(*position))
        occurrence = shape_tool.AddComponent(root, definition, TopLoc_Location(transform))
        _set_name(occurrence, name)
    shape_tool.UpdateAssemblies()
    _write_document(document, path)


class StepAssembly:
    """One imported source and independently movable direct occurrences.

    The source is read-only. IDs are scoped to the source hash plus the imported
    occurrence path. They are not claimed to survive replacing/re-exporting STEP.
    """

    def __init__(self, path: Path, document: TDocStd_Document):
        self.source_path = path.resolve()
        self.source_sha256 = file_sha256(self.source_path)
        self.document = document
        self.shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        roots = TDF_LabelSequence()
        self.shape_tool.GetFreeShapes(roots)
        self.root_count = roots.Length()
        if self.root_count != 1:
            raise UnsupportedAssemblyError("This proof requires exactly one top-level assembly")
        self.root = roots.Value(1)
        if not self.shape_tool.IsAssembly_s(self.root):
            raise UnsupportedAssemblyError("This proof requires an assembly with component occurrences")
        labels = TDF_LabelSequence()
        self.shape_tool.GetComponents_s(self.root, labels, False)
        self._occurrences: dict[str, TDF_Label] = {}
        self._paths: dict[str, str] = {}
        for index in range(1, labels.Length() + 1):
            label = labels.Value(index)
            path_key = f"{_entry(self.root)}/{_entry(label)}"
            occurrence_id = str(uuid5(NAMESPACE_URL, f"step:{self.source_sha256}:{path_key}"))
            self._occurrences[occurrence_id] = label
            self._paths[occurrence_id] = path_key

    @classmethod
    def load(cls, path: Path) -> "StepAssembly":
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("STEP input must be a regular file")
        source_hash = file_sha256(path)
        document = _document()
        reader = STEPCAFControl_Reader()
        reader.SetNameMode(True)
        reader.SetColorMode(True)
        # OCCT converts source file units to this internal length unit.
        Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            raise ValueError(f"OCCT could not read STEP input: {path.name}")
        if not reader.Transfer(document):
            raise ValueError("OCCT could not transfer STEP input into an XCAF document")
        assembly = cls(path, document)
        if assembly.source_sha256 != source_hash:
            raise RuntimeError("Source changed during import")
        return assembly

    def _check_source(self) -> None:
        if file_sha256(self.source_path) != self.source_sha256:
            raise RuntimeError("Source STEP changed since import; import it as a new source")

    def snapshot(self) -> dict:
        self._check_source()
        occurrences = []
        for occurrence_id, label in self._occurrences.items():
            definition = TDF_Label()
            if not self.shape_tool.GetReferredShape_s(label, definition):
                raise RuntimeError("Expected an assembly occurrence reference")
            transform = self.shape_tool.GetLocation_s(label).Transformation()
            translation = transform.TranslationPart()
            shape = self.shape_tool.GetShape_s(label)
            bounds = Bnd_Box()
            BRepBndLib.AddOptimal_s(shape, bounds, False, False)
            box = [*bounds.CornerMin().Coord(), *bounds.CornerMax().Coord()]
            properties = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, properties)
            occurrences.append({
                "id": occurrence_id,
                "name": _name(label),
                "path": self._paths[occurrence_id],
                "definition_id": _entry(definition),
                "translation_mm": [translation.X(), translation.Y(), translation.Z()],
                "bounds_mm": box,
                "dimensions_mm": [box[index + 3] - box[index] for index in range(3)],
                "volume_mm3": properties.Mass(),
                "matrix_3x4": [[transform.Value(row, column) for column in range(1, 5)] for row in range(1, 4)],
                "is_subassembly": self.shape_tool.IsAssembly_s(definition),
            })
        return {
            "source_sha256": self.source_sha256,
            "units": "mm",
            "root_count": self.root_count,
            "root_name": _name(self.root),
            "occurrences": occurrences,
        }

    def translate_occurrence(self, occurrence_id: str, delta_mm: Sequence[float]) -> None:
        """Translate a direct occurrence in its parent's coordinate frame."""
        self._check_source()
        if occurrence_id not in self._occurrences:
            raise KeyError(f"Unknown or unsupported occurrence: {occurrence_id}")
        delta = _translation(delta_mm)
        label = self._occurrences[occurrence_id]
        transform = self.shape_tool.GetLocation_s(label).Transformation()
        current = transform.TranslationPart()
        position = _translation([current.X() + delta[0], current.Y() + delta[1], current.Z() + delta[2]])
        transform.SetTranslationPart(gp_Vec(*position))
        # Replace this reference's placement, never transform its shared definition.
        XCAFDoc_Location.Set_s(label, TopLoc_Location(transform))
        self.shape_tool.UpdateAssemblies()

    def export_step(self, path: Path) -> None:
        self._check_source()
        path = Path(path).resolve()
        if path == self.source_path or (path.exists() and os.path.samefile(path, self.source_path)):
            raise ValueError("Refusing to overwrite the original STEP source")
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing export: {path}")
        _write_document(self.document, path)
        self._check_source()
