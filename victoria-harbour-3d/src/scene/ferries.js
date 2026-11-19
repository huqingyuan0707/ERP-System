import * as THREE from 'three';
import { createWakeTexture } from './textures.js';

/** 天星小轮：经典绿白船身、暖光客舱与尾迹，沿维港往返。 */
export function createFerries(scene) {
  const ferries = [];

  function makeStarFerry() {
    const g = new THREE.Group();
    const hull = new THREE.Mesh(
      new THREE.BoxGeometry(26, 3.4, 9),
      new THREE.MeshStandardMaterial({ color: 0x006a5e, roughness: 0.52, metalness: 0.22 })
    );
    hull.position.y = 2.3;
    const cabin = new THREE.Mesh(
      new THREE.BoxGeometry(20, 3.2, 8),
      new THREE.MeshStandardMaterial({ color: 0xe8f1f3, roughness: 0.68 })
    );
    cabin.position.y = 5.5;
    const windows = new THREE.Mesh(
      new THREE.BoxGeometry(20.2, 1.15, 8.2),
      new THREE.MeshBasicMaterial({ color: 0xffd98a })
    );
    windows.position.y = 5.7;
    const roof = new THREE.Mesh(
      new THREE.BoxGeometry(20.6, 0.5, 8.4),
      new THREE.MeshStandardMaterial({ color: 0x00574e, roughness: 0.6 })
    );
    roof.position.y = 7.4;
    const bridge = new THREE.Mesh(
      new THREE.BoxGeometry(5, 2.2, 7),
      new THREE.MeshStandardMaterial({ color: 0xf2f7f8, roughness: 0.65 })
    );
    bridge.position.set(7, 8.7, 0);
    const red = new THREE.Mesh(
      new THREE.SphereGeometry(0.34, 8, 8),
      new THREE.MeshBasicMaterial({ color: 0xff3838 })
    );
    red.position.set(-13, 4.2, 3.2);
    const green = new THREE.Mesh(
      new THREE.SphereGeometry(0.34, 8, 8),
      new THREE.MeshBasicMaterial({ color: 0x49ea7a })
    );
    green.position.set(-13, 4.2, -3.2);
    g.add(hull, cabin, windows, roof, bridge, red, green);
    g.scale.setScalar(1.7);
    return g;
  }

  const first = makeStarFerry();
  first.position.set(-210, 0, -12);
  scene.add(first);
  ferries.push({ mesh: first, speed: 11, dir: 1, phase: 0 });

  const second = makeStarFerry();
  second.position.set(190, 0, 14);
  second.rotation.y = Math.PI;
  scene.add(second);
  ferries.push({ mesh: second, speed: 9, dir: -1, phase: 1.8 });

  const wakeTexture = createWakeTexture();
  ferries.forEach((ferry) => {
    const wake = new THREE.Mesh(
      new THREE.PlaneGeometry(58, 9),
      new THREE.MeshBasicMaterial({
        map: wakeTexture,
        transparent: true,
        opacity: 0.5,
        depthWrite: false,
        side: THREE.DoubleSide,
      })
    );
    wake.rotation.x = -Math.PI / 2;
    wake.position.set(ferry.mesh.position.x - ferry.dir * 30, 0.14, ferry.mesh.position.z);
    scene.add(wake);
    ferry.wake = wake;
  });

  return {
    update(dt, elapsed) {
      ferries.forEach((ferry) => {
        ferry.mesh.position.x += ferry.speed * ferry.dir * dt;
        if (ferry.mesh.position.x > 560) ferry.dir = -1;
        if (ferry.mesh.position.x < -560) ferry.dir = 1;
        ferry.mesh.rotation.y = ferry.dir === 1 ? 0 : Math.PI;
        ferry.mesh.position.y = Math.sin(elapsed * 1.6 + ferry.phase) * 0.26;
        ferry.mesh.rotation.z = Math.sin(elapsed * 1.1 + ferry.phase) * 0.012;
        ferry.wake.position.x = ferry.mesh.position.x - ferry.dir * 31;
        ferry.wake.position.z = ferry.mesh.position.z;
        ferry.wake.material.opacity = 0.42 + Math.sin(elapsed * 2 + ferry.phase) * 0.08;
      });
    },
  };
}
