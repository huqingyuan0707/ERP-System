import * as THREE from 'three';
import { createGlowTexture, seededRandom } from './textures.js';

/** 夜色渐变天空 + 星点 + 月亮光晕，全部程序化生成，无外部贴图。 */
export function createSky(scene) {
  const skyMat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: {
      topColor: { value: new THREE.Color(0x02060f) },
      horizonColor: { value: new THREE.Color(0x11283c) },
      glowColor: { value: new THREE.Color(0x6b3d47) },
    },
    vertexShader: /* glsl */ `
      varying vec3 vWorldPosition;
      void main() {
        vec4 worldPosition = modelMatrix * vec4(position, 1.0);
        vWorldPosition = worldPosition.xyz;
        gl_Position = projectionMatrix * viewMatrix * worldPosition;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 topColor;
      uniform vec3 horizonColor;
      uniform vec3 glowColor;
      varying vec3 vWorldPosition;
      void main() {
        vec3 dir = normalize(vWorldPosition);
        float h = dir.y;
        vec3 col = mix(horizonColor, topColor, pow(clamp(h, 0.0, 1.0), 0.55));
        float glow = pow(clamp(1.0 - abs(h) * 3.6, 0.0, 1.0), 2.0);
        col += glowColor * glow * 0.38;
        col = mix(col, vec3(0.008, 0.012, 0.026), clamp(-h * 3.0, 0.0, 1.0));
        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(1900, 40, 20), skyMat));

  const rand = seededRandom(2026);
  const positions = [];
  for (let i = 0; i < 1500; i++) {
    const theta = rand() * Math.PI * 2;
    const phi = Math.acos(rand() * 0.88 + 0.04);
    const r = 1600;
    positions.push(
      r * Math.sin(phi) * Math.cos(theta),
      r * Math.cos(phi),
      r * Math.sin(phi) * Math.sin(theta)
    );
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  scene.add(
    new THREE.Points(
      starGeo,
      new THREE.PointsMaterial({
        color: 0xc4d8f2,
        size: 2.2,
        sizeAttenuation: false,
        transparent: true,
        opacity: 0.72,
        depthWrite: false,
      })
    )
  );

  const glowTexture = createGlowTexture('rgba(255,244,214,0.95)', 'rgba(255,206,150,0)');
  const glow = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: glowTexture,
      color: 0xfff2d8,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      opacity: 0.75,
    })
  );
  glow.position.set(-620, 520, -980);
  glow.scale.set(300, 300, 1);
  scene.add(glow);
  const moon = new THREE.Mesh(
    new THREE.SphereGeometry(19, 24, 24),
    new THREE.MeshBasicMaterial({ color: 0xfff7e6 })
  );
  moon.position.copy(glow.position);
  scene.add(moon);
}
