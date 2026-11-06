import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from '../../src/engineering_workbench/static/vendor/three.module.js';
import { AssemblyViewer, matrixFromRows } from '../../src/engineering_workbench/static/viewer.js';
const rows = (x = 0, y = 0, z = 0) => [[1,0,0,x], [0,1,0,y], [0,0,1,z]];
function fixture(offset = 0) {
  return { source_sha256: 'one', meshes: { part: { positions: [0,0,0, 10,0,0, 0,20,30], edges: [0,0,0, 10,0,0, 10,0,0, 0,20,30] } }, occurrences: [
    { id: 'fixed', definition_id: 'part', name: 'Fixed', matrix_3x4: rows(), baseline_matrix_3x4: rows(), offset_mm: [0,0,0] },
    { id: 'moving', definition_id: 'part', name: 'Moving', matrix_3x4: rows(30+offset), baseline_matrix_3x4: rows(30), offset_mm: [offset,0,0] },
  ] };
}
function viewer() {
  const v = Object.create(AssemblyViewer.prototype);
  Object.assign(v, { scene: new THREE.Scene(), parts: new Map(), geometries: new Map(), sourceKey: null, render() {}, camera: new THREE.PerspectiveCamera(36, 1.6, .01, 10000) });
  v.camera.up.set(0,0,1);
  v.controls = { target: new THREE.Vector3(), update() {} };
  v.materials = { normal: new THREE.MeshBasicMaterial(), selected: new THREE.MeshBasicMaterial(), edge: new THREE.LineBasicMaterial(), selectedEdge: new THREE.LineBasicMaterial(), original: new THREE.LineDashedMaterial() };
  return v;
}

test('row-major nested and flat matrices preserve translation and rotation', () => {
  const nested = [[0,-1,0,30], [1,0,0,5], [0,0,1,7]];
  const point = new THREE.Vector3(3,4,8);
  assert.deepEqual(point.clone().applyMatrix4(matrixFromRows(nested)).toArray(), [26,8,15]);
  assert.deepEqual(matrixFromRows(nested).elements, matrixFromRows(nested.flat()).elements);
});

test('occurrences share actual tessellation buffers, not bounding-box stand-ins', () => {
  const v = viewer(); const model = fixture(); v.setModel(model, 'fixed');
  assert.equal(v.parts.get('fixed').mesh.geometry, v.parts.get('moving').mesh.geometry);
  assert.deepEqual([...v.parts.get('fixed').mesh.geometry.attributes.position.array], model.meshes.part.positions);
  assert.equal(v.parts.get('fixed').mesh.geometry.attributes.normal.count, 3);
  assert.equal(v.parts.get('moving').mesh.matrix.elements[12], 30);
  assert.equal(v.parts.get('fixed').mesh.matrix.elements[12], 0);
});

test('old-position outlines appear only for changed occurrences and retain baseline transforms', () => {
  const v = viewer(); v.setModel(fixture(), 'moving');
  assert.equal(v.parts.get('moving').original.visible, false);
  v.setModel(fixture(12), 'moving');
  assert.equal(v.parts.get('moving').original.visible, true);
  assert.equal(v.parts.get('fixed').original.visible, false);
  assert.equal(v.parts.get('moving').original.matrix.elements[12], 30);
  assert.equal(v.parts.get('moving').mesh.matrix.elements[12], 42);
  assert.equal(v.parts.get('moving').original.geometry, v.parts.get('moving').edges.geometry);
});

test('reset removes original outline while reusing definition geometry', () => {
  const v = viewer(); v.setModel(fixture(10), 'moving'); const geometry = v.parts.get('moving').mesh.geometry;
  v.setModel(fixture(0), 'moving');
  assert.equal(v.parts.get('moving').original.visible, false);
  assert.equal(v.parts.get('moving').mesh.matrix.elements[12], 30);
  assert.equal(v.parts.get('moving').mesh.geometry, geometry);
});

test('fit bounds include original and current geometry; camera has valid clipping and target', () => {
  const v = viewer(); v.setModel(fixture(-80), 'moving');
  const bounds = v.modelBounds();
  assert.deepEqual(bounds.min.toArray(), [-50,0,0]); assert.deepEqual(bounds.max.toArray(), [40,20,30]);
  v.fit(); assert.deepEqual(v.controls.target.toArray(), [-5,10,15]);
  assert.ok(v.camera.far > v.camera.position.length()); assert.ok(v.camera.near > 0);
});

test('selection changes shade without mutating geometry or occurrence transforms', () => {
  const v = viewer(); v.setModel(fixture(7), 'fixed');
  assert.equal(v.parts.get('fixed').mesh.material, v.materials.selected);
  v.select('moving'); assert.equal(v.parts.get('moving').mesh.material, v.materials.selected);
  assert.equal(v.parts.get('fixed').mesh.material, v.materials.normal);
  assert.equal(v.parts.get('moving').mesh.matrix.elements[12], 37);
});

test('replacement disposes prior geometry and clears old occurrence groups', () => {
  const v = viewer(); v.setModel(fixture(), 'fixed');
  let disposed = false; const original = v.parts.get('fixed').mesh.geometry;
  original.addEventListener('dispose', () => { disposed = true; });
  const replacement = fixture(); replacement.source_sha256 = 'two'; v.setModel(replacement, 'moving');
  assert.equal(disposed, true); assert.notEqual(v.parts.get('fixed').mesh.geometry, original);
  v.setModel(null, null); assert.equal(v.parts.size, 0); assert.equal(v.geometries.size, 0);
});
