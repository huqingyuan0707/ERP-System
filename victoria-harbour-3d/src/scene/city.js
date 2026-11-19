import * as THREE from 'three';
import { createFacadeTexture, createGlowTexture, seededRandom } from './textures.js';

const FACADE_VARIANTS = 10;

/** 两岸地形、密集楼宇、地标与航空障碍灯。 */
export function createCity(scene) {
  const group = new THREE.Group();
  const beacons = [];
  const rand = seededRandom(1119);
  const facades = Array.from({ length: FACADE_VARIANTS }, (_, i) =>
    createFacadeTexture(300 + i, {
      cols: 10 + (i % 5),
      rows: 28 + ((i * 3) % 12),
      litRatio: 0.30 + (i % 5) * 0.05,
    })
  );

  const landMat = new THREE.MeshStandardMaterial({
    color: 0x0d1622,
    roughness: 0.96,
    metalness: 0.04,
  });
  const kowloon = new THREE.Mesh(new THREE.BoxGeometry(1500, 4, 320), landMat);
  kowloon.position.set(0, -2, -215);
  group.add(kowloon);
  const island = new THREE.Mesh(new THREE.BoxGeometry(1500, 4, 320), landMat);
  island.position.set(0, -2, 215);
  group.add(island);

  // 海堤与路灯
  const quayMat = new THREE.MeshBasicMaterial({ color: 0x2f5680 });
  for (const z of [-55, 55]) {
    const quay = new THREE.Mesh(new THREE.BoxGeometry(1500, 0.5, 1.2), quayMat);
    quay.position.set(0, 0.25, z);
    group.add(quay);
  }
  const lampCount = 220;
  const lamps = new THREE.InstancedMesh(
    new THREE.SphereGeometry(0.42, 6, 6),
    new THREE.MeshBasicMaterial({ color: 0xffd79a }),
    lampCount
  );
  const dummy = new THREE.Object3D();
  for (let i = 0; i < lampCount; i++) {
    dummy.position.set(-700 + (i / (lampCount - 1)) * 1400, 1.4, i % 2 === 0 ? -56 : 56);
    dummy.updateMatrix();
    lamps.setMatrixAt(i, dummy.matrix);
  }
  lamps.instanceMatrix.needsUpdate = true;
  group.add(lamps);

  // 街道灯火：散布在楼宇之间的地面亮点，填充城市基底
  const streetPositions = [];
  for (let i = 0; i < 1400; i++) {
    const x = -700 + rand() * 1400;
    const z = rand() < 0.5 ? -70 - rand() * 138 : 70 + rand() * 138;
    streetPositions.push(x, 0.7, z);
  }
  const streetGeo = new THREE.BufferGeometry();
  streetGeo.setAttribute('position', new THREE.Float32BufferAttribute(streetPositions, 3));
  group.add(
    new THREE.Points(
      streetGeo,
      new THREE.PointsMaterial({ color: 0xffca82, size: 1.6, sizeAttenuation: false })
    )
  );

  const roofMat = new THREE.MeshStandardMaterial({ color: 0x101926, roughness: 0.85 });
  const poleMat = new THREE.MeshStandardMaterial({ color: 0x465366, metalness: 0.8 });

  function addBuilding(x, z, w, d, h, variant, glow = 0.72) {
    // 高楼做2~3段退台，打破方盒子轮廓
    const ratios = h > 105 ? [0.56, 0.29, 0.15] : [1];
    let baseY = 0;
    ratios.forEach((ratio, index) => {
      const sh = h * ratio;
      const factor = 1 - index * 0.17;
      const sw = w * factor;
      const sd = d * factor;
      const tex = facades[variant % FACADE_VARIANTS].clone();
      tex.repeat.set(Math.max(1, Math.round(sw / 9)), Math.max(1, Math.round(sh / 16)));
      tex.needsUpdate = true;
      const mat = new THREE.MeshStandardMaterial({
        map: tex,
        emissiveMap: tex,
        emissive: 0xffffff,
        emissiveIntensity: glow,
        metalness: 0.48,
        roughness: 0.42,
      });
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(sw, sh, sd), mat);
      mesh.position.set(x, baseY + sh / 2, z);
      group.add(mesh);
      baseY += sh;
    });

    if (h > 45) {
      const roof = new THREE.Mesh(new THREE.BoxGeometry(w * 0.34, 3, d * 0.34), roofMat);
      roof.position.set(x, h + 1.5, z);
      group.add(roof);
    }
    if (h > 95) {
      const poleH = Math.min(18, h * 0.07);
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, poleH, 6), poleMat);
      pole.position.set(x, h + poleH / 2, z);
      group.add(pole);
      const beacon = new THREE.Mesh(
        new THREE.SphereGeometry(0.72, 8, 8),
        new THREE.MeshBasicMaterial({ color: 0xff3b30, transparent: true, opacity: 1 })
      );
      beacon.position.set(x, h + poleH + 1.2, z);
      group.add(beacon);
      beacons.push({ mesh: beacon, phase: rand() * Math.PI * 2 });
    }
  }

  // 楼宇高度随离市中心距离衰减，形成自然天际线
  const heightFor = (x, center, base) => {
    const distance = Math.abs(x - center);
    const falloff = Math.exp(-(distance * distance) / (2 * 170 * 170));
    return Math.max(14, base * (0.22 + 0.78 * falloff) * (0.55 + rand() * 0.85));
  };
  for (let i = 0; i < 125; i++) {
    const x = -620 + rand() * 1240;
    const z = -68 - rand() * 138;
    addBuilding(x, z, 8 + rand() * 17, 8 + rand() * 17, heightFor(x, -35, 138), i);
  }
  for (let i = 0; i < 140; i++) {
    const x = -620 + rand() * 1240;
    const z = 68 + rand() * 138;
    addBuilding(x, z, 8 + rand() * 17, 8 + rand() * 17, heightFor(x, -145, 145), i + 3);
  }

  const landmarkMat = (seed, color, glow) => {
    const tex = createFacadeTexture(seed, { cols: 12, rows: 46, litRatio: 0.42 });
    return new THREE.MeshStandardMaterial({
      map: tex,
      emissiveMap: tex,
      emissive: 0xffffff,
      emissiveIntensity: glow * 0.92,
      color,
      metalness: 0.62,
      roughness: 0.3,
    });
  };

  function addTower(x, z, height, topRadius, bottomRadius, material, sides = 4) {
    const tower = new THREE.Mesh(
      new THREE.CylinderGeometry(topRadius, bottomRadius, height, sides, 1),
      material
    );
    tower.rotation.y = sides === 4 ? Math.PI / 4 : 0;
    tower.position.set(x, height / 2, z);
    group.add(tower);
    const poleH = Math.min(24, height * 0.07);
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, poleH, 6), poleMat);
    pole.position.set(x, height + poleH / 2, z);
    group.add(pole);
    const beacon = new THREE.Mesh(
      new THREE.SphereGeometry(0.95, 10, 10),
      new THREE.MeshBasicMaterial({ color: 0xff2d2d, transparent: true, opacity: 1 })
    );
    beacon.position.set(x, height + poleH + 1, z);
    group.add(beacon);
    beacons.push({ mesh: beacon, phase: rand() * Math.PI * 2 });
    return tower;
  }

  // 环球贸易广场 ICC（九龙，全港最高）
  addTower(-25, -112, 425, 9, 16, landmarkMat(901, 0xffd2a0, 1.05));
  // 国际金融中心二期 IFC Two（中环）
  addTower(-152, 116, 392, 7, 14, landmarkMat(902, 0xbfd8ff, 1.0));
  // 中银大厦：三座错落三角塔
  const bocMat = landmarkMat(903, 0xb8ccff, 0.92);
  addTower(-192, 132, 305, 2.5, 11, bocMat, 3);
  addTower(-206, 142, 232, 2, 8, bocMat, 3);
  addTower(-178, 143, 184, 1.8, 7, bocMat, 3);
  // 汇丰总行：阶梯式塔群
  const hsbcMat = landmarkMat(904, 0xc8e0ff, 0.95);
  [-172, -158, -144, -130].forEach((x, i) => {
    const h = 168 - i * 18;
    const box = new THREE.Mesh(new THREE.BoxGeometry(11, h, 15), hsbcMat);
    box.position.set(x, h / 2, 150);
    group.add(box);
  });
  // 中环广场：三角塔 + 三根尖顶
  addTower(-92, 126, 374, 4, 13, landmarkMat(905, 0xffd8b0, 0.98), 3);
  [-1, 1].forEach((side) => {
    const spire = new THREE.Mesh(new THREE.CylinderGeometry(0.4, 1.1, 44, 6), poleMat);
    spire.position.set(-92 + side * 6, 396, 126);
    group.add(spire);
  });

  // 太平山：暗色山体 + 零星山灯
  const peak = new THREE.Mesh(
    new THREE.ConeGeometry(220, 118, 32, 1),
    new THREE.MeshStandardMaterial({ color: 0x0b1a16, roughness: 1, metalness: 0 })
  );
  peak.position.set(-330, -4, 330);
  group.add(peak);
  const peakLights = [];
  for (let i = 0; i < 90; i++) {
    const a = rand() * Math.PI * 2;
    const r = rand() * 150;
    peakLights.push(-330 + Math.cos(a) * r, 8 + rand() * 70, 330 + Math.sin(a) * r);
  }
  const peakGeo = new THREE.BufferGeometry();
  peakGeo.setAttribute('position', new THREE.Float32BufferAttribute(peakLights, 3));
  group.add(
    new THREE.Points(
      peakGeo,
      new THREE.PointsMaterial({ color: 0xffd9a0, size: 1.8, sizeAttenuation: false })
    )
  );

  // 市中心地面暖色光晕，模拟密集灯火在海面上方的散射
  const glowTexture = createGlowTexture('rgba(255,186,104,0.55)', 'rgba(255,140,70,0)');
  const glowMat = new THREE.SpriteMaterial({
    map: glowTexture,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    opacity: 0.3,
  });
  for (const [cx, cz] of [
    [-145, 120],
    [-35, -115],
    [105, -120],
    [-255, 135],
  ]) {
    for (let i = 0; i < 7; i++) {
      const sprite = new THREE.Sprite(glowMat.clone());
      sprite.position.set(cx + (rand() - 0.5) * 190, 4, cz + (rand() - 0.5) * 95);
      sprite.scale.set(55 + rand() * 55, 22 + rand() * 16, 1);
      group.add(sprite);
    }
  }

  scene.add(group);
  return { group, beacons };
}
