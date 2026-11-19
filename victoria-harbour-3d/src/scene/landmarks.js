import * as THREE from 'three';
import { createGlowTexture, createNeonSignTexture, seededRandom } from './textures.js';

const NEON_WORDS = [
  '茶餐廳', '大排檔', '金龍酒家', '冰室', '烧臘', '當舖', '夜總會',
  '富豪酒店', '海景餐廳', '珠寶', '時裝', '銀行', '药房', '戏院',
];
const NEON_COLORS = ['#ff3b6b', '#31e0ff', '#ffd23f', '#ff6a00', '#7cff5a', '#ff4fd8'];

/** 中环摩天轮、尖沙咀钟楼与临街霓虹招牌。 */
export function createLandmarks(scene) {
  const group = new THREE.Group();
  const rand = seededRandom(4242);
  const rotating = [];

  // ---------- 中环摩天轮：支架固定，轮圈旋转 ----------
  const frameMat = new THREE.MeshStandardMaterial({ color: 0x6b7c93, metalness: 0.85, roughness: 0.35 });
  const rimMat = new THREE.MeshBasicMaterial({ color: 0x8fe6ff });
  const cabinMat = new THREE.MeshBasicMaterial({ color: 0xffd98a });
  const R = 42;

  const rotor = new THREE.Group();
  rotor.add(new THREE.Mesh(new THREE.TorusGeometry(R, 0.8, 8, 64), rimMat));
  rotor.add(new THREE.Mesh(new THREE.TorusGeometry(R * 0.78, 0.45, 6, 64), rimMat));
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2;
    const spoke = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.24, R, 6), frameMat);
    spoke.rotation.z = -a;
    spoke.position.set((Math.cos(a) * R) / 2, (Math.sin(a) * R) / 2, 0);
    rotor.add(spoke);
    const cabin = new THREE.Mesh(new THREE.BoxGeometry(4.2, 3.4, 4.2), cabinMat);
    cabin.position.set(Math.cos(a) * R, Math.sin(a) * R, 0);
    rotor.add(cabin);
  }

  const support = new THREE.Group();
  for (const z of [-5, 5]) {
    for (const dir of [-1, 1]) {
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.8, 1.3, 54, 8), frameMat);
      leg.position.set(dir * 11, -27, z);
      leg.rotation.z = dir * 0.4;
      support.add(leg);
    }
    const axle = new THREE.Mesh(new THREE.CylinderGeometry(1.6, 1.6, 12, 12), frameMat);
    axle.rotation.x = Math.PI / 2;
    axle.position.set(0, 0, z);
    support.add(axle);
  }

  const ferris = new THREE.Group();
  ferris.add(rotor, support);
  ferris.position.set(-300, 76, 54);
  group.add(ferris);
  rotating.push({ object: rotor, speed: 0.05 });

  const wheelGlow = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: createGlowTexture('rgba(130,220,255,0.6)', 'rgba(80,160,255,0)'),
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      opacity: 0.34,
    })
  );
  wheelGlow.position.copy(ferris.position);
  wheelGlow.scale.set(105, 105, 1);
  group.add(wheelGlow);

  // ---------- 尖沙咀钟楼 ----------
  const clock = new THREE.Group();
  const brickMat = new THREE.MeshStandardMaterial({ color: 0x8d5a45, roughness: 0.9, metalness: 0.05 });
  const tower = new THREE.Mesh(new THREE.BoxGeometry(10, 44, 10), brickMat);
  tower.position.y = 22;
  clock.add(tower);
  const belfry = new THREE.Mesh(new THREE.BoxGeometry(12, 8, 12), brickMat);
  belfry.position.y = 47;
  clock.add(belfry);
  const roof = new THREE.Mesh(
    new THREE.ConeGeometry(9, 13, 4),
    new THREE.MeshStandardMaterial({ color: 0x24303c, roughness: 0.7, metalness: 0.3 })
  );
  roof.position.y = 57;
  roof.rotation.y = Math.PI / 4;
  clock.add(roof);
  for (const z of [5.1, -5.1]) {
    const face = new THREE.Mesh(
      new THREE.CircleGeometry(3.4, 24),
      new THREE.MeshBasicMaterial({ color: 0xfff3d0 })
    );
    face.position.set(0, 47, z);
    if (z < 0) face.rotation.y = Math.PI;
    clock.add(face);
    const hand = new THREE.Mesh(
      new THREE.BoxGeometry(0.32, 2.6, 0.1),
      new THREE.MeshBasicMaterial({ color: 0x1a1a1a })
    );
    hand.position.set(0, 47.9, z + (z > 0 ? 0.12 : -0.12));
    clock.add(hand);
  }
  clock.position.set(60, 0, -60);
  group.add(clock);

  // ---------- 临街霓虹招牌 ----------
  const signTextures = NEON_WORDS.map((word, i) =>
    createNeonSignTexture(word, NEON_COLORS[i % NEON_COLORS.length], { vertical: i % 3 === 0 })
  );
  for (const [side, zFace] of [
    [1, -68],
    [-1, 68],
  ]) {
    for (let i = 0; i < 26; i++) {
      const tex = signTextures[Math.floor(rand() * signTextures.length)];
      const vertical = tex.image.height > tex.image.width;
      const w = vertical ? 7 : 24;
      const h = vertical ? 24 : 7;
      const sign = new THREE.Mesh(
        new THREE.PlaneGeometry(w, h),
        new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide })
      );
      sign.position.set(-580 + rand() * 1160, 14 + rand() * 62, zFace + side * 1.5);
      if (side < 0) sign.rotation.y = Math.PI;
      group.add(sign);
    }
  }

  scene.add(group);
  return {
    group,
    update(dt) {
      rotating.forEach((item) => {
        item.object.rotation.z += item.speed * dt;
      });
    },
  };
}
