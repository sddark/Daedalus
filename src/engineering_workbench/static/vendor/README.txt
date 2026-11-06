Three.js 0.160.1 (r160), MIT license.
Source: https://registry.npmjs.org/three/-/three-0.160.1.tgz

three.module.js is upstream build/three.module.min.js, renamed only.
OrbitControls.js is upstream examples/jsm/controls/OrbitControls.js with its
single `from 'three'` import changed to `from './three.module.js'` so the app
works entirely offline without a bundler or runtime CDN.
THREE-LICENSE.txt is the original upstream MIT notice.

This version supports both WebGL 1 and WebGL 2. The viewer caps device pixel
ratio at 1.5, shares tessellations between occurrences, does not enable shadows
or damping, and renders only on changes to reduce idle GPU work on a Pi.
No hardware-specific performance claims are made.
