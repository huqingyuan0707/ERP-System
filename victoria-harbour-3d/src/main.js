import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { createSky } from './scene/sky.js';
import { createWater } from './scene/water.js';
import { createCity } from './scene/city.js';
import { createFerries } from './scene/ferries.js';
import { createLandmarks } from './scene/landmarks.js';

const canvas = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({
  canvas,
  antialias: true,
  powerPreference: 'high-performance',
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.42;

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x081426, 0.0013);

const camera = new THREE.PerspectiveCamera(
  48,
  window.innerWidth / window.innerHeight,
  1,
  4000
);
camera.position.set(350, 150, 440);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(-40, 72, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.055;
controls.maxPolarAngle = Math.PI * 0.495;
controls.minDistance = 50;
controls.maxDistance = 1200;
controls.autoRotateSpeed = 0.35;

createSky(scene);
scene.add(new THREE.HemisphereLight(0x28466e, 0x050912, 0.78));
const moonLight = new THREE.DirectionalLight(0x9dc0ff, 0.58);
moonLight.position.set(-500, 400, -600);
scene.add(moonLight);

const water = createWater(scene);
const { beacons } = createCity(scene);
const landmarks = createLandmarks(scene);
const ferries = createFerries(scene);

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(
  new THREE.Vector2(window.innerWidth, window.innerHeight),
  1.15,
  0.6,
  0.24
);
composer.addPass(bloom);
composer.addPass(new OutputPass());

const presets = {
  panorama: { pos: [350, 150, 440], target: [-40, 72, 0] },
  central: { pos: [-340, 165, 315], target: [-150, 150, 120] },
  tsimshatsui: { pos: [190, 98, -330], target: [-25, 140, -95] },
  ferry: { pos: [0, 16, 26], target: [0, 85, -140] },
};
let tween = null;
function goToPreset(name) {
  const preset = presets[name];
  tween = {
    fromPos: camera.position.clone(),
    toPos: new THREE.Vector3(...preset.pos),
    fromTarget: controls.target.clone(),
    toTarget: new THREE.Vector3(...preset.target),
    t: 0,
  };
}
document.querySelectorAll('.preset').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.preset').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    goToPreset(button.dataset.preset);
  });
});
document.getElementById('bloomToggle').addEventListener('change', (event) => {
  bloom.enabled = event.target.checked;
});
document.getElementById('autoRotate').addEventListener('change', (event) => {
  controls.autoRotate = event.target.checked;
});

window.addEventListener('resize', () => {
  const w = window.innerWidth;
  const h = window.innerHeight;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
  composer.setSize(w, h);
});

const timer = new THREE.Timer();
function animate() {
  requestAnimationFrame(animate);
  timer.update();
  const dt = Math.min(timer.getDelta(), 0.05);
  const elapsed = timer.getElapsed();
  if (tween) {
    tween.t = Math.min(1, tween.t + dt * 0.65);
    const eased = 1 - Math.pow(1 - tween.t, 3);
    camera.position.lerpVectors(tween.fromPos, tween.toPos, eased);
    controls.target.lerpVectors(tween.fromTarget, tween.toTarget, eased);
    if (tween.t >= 1) tween = null;
  }
  water.update(elapsed);
  landmarks.update(dt);
  ferries.update(dt, elapsed);
  beacons.forEach(({ mesh, phase }) => {
    mesh.material.opacity =
      0.3 + 0.7 * Math.pow(Math.max(0, Math.sin(elapsed * 2.1 + phase)), 2);
  });
  controls.update();
  composer.render();
}
animate();

setTimeout(() => document.getElementById('loading').classList.add('hidden'), 1000);
