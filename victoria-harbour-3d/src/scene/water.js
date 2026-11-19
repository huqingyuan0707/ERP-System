import * as THREE from 'three';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { createWaveNormalTexture } from './textures.js';

/** 带真实场景反射的海面：在原 Reflector shader 上叠加双层滚动法线，制造波纹 distortion。 */
export function createWater(scene) {
  // 只覆盖两岸之间的海面，避免与陆地顶面共面产生 z-fighting 条纹
  const water = new Reflector(new THREE.PlaneGeometry(1480, 108), {
    clipBias: 0.003,
    textureWidth: 2048,
    textureHeight: 2048,
    color: 0x2e4a63,
  });
  water.rotateX(-Math.PI / 2);
  water.position.y = 0.02;

  const mat = water.material;
  mat.uniforms.time = { value: 0 };
  mat.uniforms.waveNormal = { value: createWaveNormalTexture(256) };
  mat.fragmentShader = mat.fragmentShader
    .replace(
      'uniform sampler2D tDiffuse;',
      'uniform sampler2D tDiffuse;\nuniform sampler2D waveNormal;\nuniform float time;'
    )
    .replace(
      'vec4 base = texture2DProj( tDiffuse, vUv );',
      /* glsl */ `
        vec2 screenUv = vUv.xy / max( vUv.w, 0.0001 );
        vec3 n1 = texture2D( waveNormal, screenUv * 0.6 + vec2( time * 0.010, time * 0.006 ) ).rgb * 2.0 - 1.0;
        vec3 n2 = texture2D( waveNormal, screenUv * 1.1 - vec2( time * 0.007, time * 0.013 ) ).rgb * 2.0 - 1.0;
        vec4 projUv = vUv;
        projUv.xy += ( n1.xz + n2.xz ) * 0.0028 * vUv.w;
        vec4 base = texture2DProj( tDiffuse, projUv );
        float shimmer = pow( clamp( dot( normalize( n1 + n2 ), vec3( 0.0, 0.0, 1.0 ) ), 0.0, 1.0 ), 8.0 );
        base.rgb += vec3( 0.04, 0.08, 0.12 ) * shimmer;
      `
    );
  mat.needsUpdate = true;
  scene.add(water);

  return {
    mesh: water,
    update(elapsed) {
      mat.uniforms.time.value = elapsed;
    },
  };
}
