import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';

// Tessellation is local to each definition. Occurrence matrices are row-major
// 3 × 4 transforms from the server; shared definitions are never translated.
export function matrixFromRows(rows) {
  const m = Array.isArray(rows[0]) ? rows.flat() : rows;
  return new THREE.Matrix4().set(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11], 0, 0, 0, 1);
}

export class AssemblyViewer {
  constructor(container, onSelect, onFailure) {
    this.container = container;
    this.onSelect = onSelect;
    this.frame = 0;
    this.sourceKey = null;
    this.diffMode = false;
    this.parts = new Map();
    this.geometries = new Map();
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#fafbf8');
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.01, 10000);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(100, -140, 100);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'low-power' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor('#fafbf8');
    this.renderer.domElement.setAttribute('aria-hidden', 'true');
    this.renderer.domElement.setAttribute('data-viewer', 'assembly');
    container.prepend(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false; // Render only on interaction, not a permanent Pi GPU loop.
    this.controls.screenSpacePanning = true;
    this.controls.zoomSpeed = 0.8;
    this.controls.rotateSpeed = 0.75;
    this.controls.addEventListener('change', () => this.render());
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x9cae9a, 2.7));
    const key = new THREE.DirectionalLight(0xffffff, 2.6);
    key.position.set(70, -100, 180);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xe4ece7, 0.8);
    fill.position.set(-80, 100, 30);
    this.scene.add(fill);
    this.materials = {
      normal: new THREE.MeshStandardMaterial({ color: '#ccd5c9', roughness: 0.8, metalness: 0.08, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
      selected: new THREE.MeshStandardMaterial({ color: '#80a68c', roughness: 0.75, metalness: 0.08, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
      edge: new THREE.LineBasicMaterial({ color: '#6b7e6e', transparent: true, opacity: 0.48 }),
      selectedEdge: new THREE.LineBasicMaterial({ color: '#416950', transparent: true, opacity: 0.8 }),
      diff: new THREE.MeshStandardMaterial({ color: '#548d72', roughness: 0.8, transparent: true, opacity: 0.45, depthWrite: false, side: THREE.DoubleSide }),
      diffSelected: new THREE.MeshStandardMaterial({ color: '#287755', roughness: 0.8, transparent: true, opacity: 0.55, depthWrite: false, side: THREE.DoubleSide }),
      original: new THREE.MeshStandardMaterial({ color: '#d39452', roughness: 0.8, transparent: true, opacity: 0.4, depthWrite: false, side: THREE.DoubleSide }),
    };
    this.raycaster = new THREE.Raycaster();
    let press = null;
    container.addEventListener('pointerdown', (event) => {
      if (event.button === 0 && event.target === this.renderer.domElement) press = { x: event.clientX, y: event.clientY, id: event.pointerId };
      else press = null;
    });
    container.addEventListener('pointercancel', () => { press = null; });
    container.addEventListener('pointerup', (event) => {
      const start = press;
      press = null;
      if (!start || event.pointerId !== start.id || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) return;
      const bounds = this.renderer.domElement.getBoundingClientRect();
      this.raycaster.setFromCamera(new THREE.Vector2((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1), this.camera);
      const hits = this.raycaster.intersectObjects([...this.parts.values()].map((part) => part.mesh), false);
      if (hits.length) this.onSelect(hits[0].object.userData.occurrenceId);
    });
    container.addEventListener('keydown', (event) => this.onKey(event));
    this.renderer.domElement.addEventListener('webglcontextlost', (event) => {
      event.preventDefault();
      onFailure('The 3D graphics context was interrupted. Reload the page to restore the view. Component controls remain available.');
    });
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
  }

  resize() {
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    if (width === 0 || height === 0) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.render();
  }

  render() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.renderer.render(this.scene, this.camera);
    });
  }

  clearModel() {
    for (const part of this.parts.values()) this.scene.remove(part.group);
    this.parts.clear();
    for (const geometry of this.geometries.values()) { geometry.mesh.dispose(); geometry.edges.dispose(); }
    this.geometries.clear();
    if (this.grid) { this.scene.remove(this.grid); this.grid.geometry.dispose(); this.grid.material.dispose(); this.grid = null; }
  }

  setModel(model, selectedId) {
    if (!model) {
      this.clearModel();
      this.model = null;
      this.comparisonMatrices = null;
      this.reviewMatrices = null;
      this.sourceKey = null;
      this.render();
      return;
    }
    const sourceKey = `${model.source_sha256}:${model.occurrences.map((part) => part.id).join(',')}`;
    const replacement = sourceKey !== this.sourceKey;
    if (replacement) {
      this.clearModel();
      this.sourceKey = sourceKey;
      for (const [id, data] of Object.entries(model.meshes)) {
        const mesh = new THREE.BufferGeometry();
        mesh.setAttribute('position', new THREE.Float32BufferAttribute(data.positions, 3));
        if (data.normals?.length === data.positions.length) mesh.setAttribute('normal', new THREE.Float32BufferAttribute(data.normals, 3));
        else mesh.computeVertexNormals();
        mesh.computeBoundingBox();
        mesh.computeBoundingSphere();
        const edges = new THREE.BufferGeometry();
        edges.setAttribute('position', new THREE.Float32BufferAttribute(data.edges, 3));
        this.geometries.set(id, { mesh, edges });
      }
      for (const occurrence of model.occurrences) {
        const geometry = this.geometries.get(occurrence.definition_id);
        if (!geometry) throw new Error(`Missing display geometry for ${occurrence.name}.`);
        const mesh = new THREE.Mesh(geometry.mesh, this.materials.normal);
        const edges = new THREE.LineSegments(geometry.edges, this.materials.edge);
        const original = new THREE.Mesh(geometry.mesh, this.materials.original);
        const group = new THREE.Group();
        mesh.matrixAutoUpdate = edges.matrixAutoUpdate = original.matrixAutoUpdate = false;
        mesh.userData.occurrenceId = occurrence.id;
        group.add(mesh, edges, original);
        this.scene.add(group);
        this.parts.set(occurrence.id, { group, mesh, edges, original });
      }
    }
    if (replacement) { this.comparisonMatrices = null; this.reviewMatrices = null; }
    this.model = model;
    for (const occurrence of model.occurrences) {
      const part = this.parts.get(occurrence.id);
      part.mesh.matrix.copy(matrixFromRows(this.reviewMatrices?.[occurrence.id] || occurrence.matrix_3x4));
      part.edges.matrix.copy(part.mesh.matrix);
      part.original.matrix.copy(matrixFromRows(this.comparisonMatrices?.[occurrence.id] || occurrence.baseline_matrix_3x4));
      part.changed = part.mesh.matrix.elements.some((value, index) => Math.abs(value - part.original.matrix.elements[index]) > 1e-9);
      part.original.visible = this.diffMode && part.changed;
      part.group.updateMatrixWorld(true);
    }
    this.select(selectedId);
    if (replacement) { this.makeGrid(); this.fit(); }
    this.render();
  }

  setComparison(matrices, afterMatrices = null) {
    this.comparisonMatrices = matrices;
    this.reviewMatrices = afterMatrices;
    if (this.model) this.setModel(this.model, this.selectedId);
    // No fit: comparison is display-only and preserves camera and orbit target.
  }

  modelBounds() {
    const bounds = new THREE.Box3();
    for (const part of this.parts.values()) {
      const local = part.mesh.geometry.boundingBox;
      bounds.union(local.clone().applyMatrix4(part.mesh.matrix));
      if (part.original.visible) bounds.union(local.clone().applyMatrix4(part.original.matrix));
    }
    return bounds;
  }

  makeGrid() {
    const bounds = this.modelBounds();
    if (bounds.isEmpty()) return;
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    const span = Math.max(size.x, size.y, size.z, 1) * 2;
    const step = span / 12;
    const vertices = [];
    const z = bounds.min.z - Math.max(size.length(), 1) * 0.003;
    for (let i = -6; i <= 6; i++) {
      vertices.push(center.x + i * step, center.y - span / 2, z, center.x + i * step, center.y + span / 2, z);
      vertices.push(center.x - span / 2, center.y + i * step, z, center.x + span / 2, center.y + i * step, z);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    this.grid = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: '#dce5d8', transparent: true, opacity: 0.45 }));
    this.scene.add(this.grid);
  }

  setDiffMode(enabled) {
    this.diffMode = Boolean(enabled);
    for (const part of this.parts.values()) part.original.visible = this.diffMode && part.changed;
    this.select(this.selectedId);
    // A display-mode switch must not change the user’s camera or orbit target.
    // Fit remains an explicit action (button / F).
  }

  select(id) {
    this.selectedId = id;
    for (const [occurrenceId, part] of this.parts) {
      part.mesh.material = this.diffMode
        ? (occurrenceId === id ? this.materials.diffSelected : this.materials.diff)
        : (occurrenceId === id ? this.materials.selected : this.materials.normal);
      part.edges.visible = !this.diffMode;
      part.edges.material = occurrenceId === id ? this.materials.selectedEdge : this.materials.edge;
    }
    this.render();
  }

  fit() {
    if (!this.parts.size) return;
    const bounds = this.modelBounds();
    if (bounds.isEmpty()) return;
    const center = bounds.getCenter(new THREE.Vector3());
    const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, 0.1);
    const verticalFov = THREE.MathUtils.degToRad(this.camera.fov);
    const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * this.camera.aspect);
    const distance = radius / Math.sin(Math.min(verticalFov, horizontalFov) / 2) * 1.22;
    this.camera.position.copy(center).add(new THREE.Vector3(1, -1.5, 1.05).normalize().multiplyScalar(distance));
    this.camera.near = Math.max(radius / 1000, 0.001);
    this.camera.far = Math.max(distance * 100, 100);
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(center);
    this.controls.minDistance = Math.max(radius / 50, 0.01);
    this.controls.maxDistance = distance * 20;
    this.controls.update();
    this.render();
  }

  onKey(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key.toLowerCase() === 'f') { event.preventDefault(); this.fit(); return; }
    const arrows = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'];
    if (!arrows.includes(event.key) && !['+', '=', '-', '_'].includes(event.key)) return;
    event.preventDefault();
    const offset = this.camera.position.clone().sub(this.controls.target);
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
    if (arrows.includes(event.key) && event.shiftKey) {
      const movement = event.key === 'ArrowLeft' ? right.negate() : event.key === 'ArrowRight' ? right : event.key === 'ArrowUp' ? up : up.negate();
      movement.multiplyScalar(offset.length() * 0.025);
      this.controls.target.add(movement);
      this.camera.position.add(movement);
    } else if (arrows.includes(event.key)) {
      const angle = Math.PI / 36;
      offset.applyAxisAngle(event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? new THREE.Vector3(0, 0, 1) : right, event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -angle : angle);
      this.camera.position.copy(this.controls.target).add(offset);
    } else {
      offset.multiplyScalar(event.key === '+' || event.key === '=' ? 0.88 : 1.14);
      offset.setLength(THREE.MathUtils.clamp(offset.length(), this.controls.minDistance, this.controls.maxDistance));
      this.camera.position.copy(this.controls.target).add(offset);
    }
    this.controls.update();
    this.render();
  }
}
