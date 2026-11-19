import * as THREE from 'three';

/** 可重复的伪随机数，保证每次刷新城市布局一致。 */
export function seededRandom(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

/** 程序化生成玻璃幕墙贴图：暗色楼体 + 随机亮起的暖/冷色窗户。 */
export function createFacadeTexture(seed, { cols = 12, rows = 34, litRatio = 0.24 } = {}) {
  const width = 192;
  const height = 384;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const rand = seededRandom(seed);

  ctx.fillStyle = '#0a111c';
  ctx.fillRect(0, 0, width, height);

  const cw = width / cols;
  const ch = height / rows;
  const ww = cw * 0.52;
  const wh = ch * 0.46;
  // 整层亮/暗模拟真实办公楼层，避免马赛克式随机噪点
  const floorActive = Array.from({ length: rows }, () => rand() < 0.74);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = c * cw + (cw - ww) / 2;
      const y = r * ch + (ch - wh) / 2;
      if (floorActive[r] && rand() < litRatio) {
        const warm = rand() < 0.58;
        const a = 0.62 + rand() * 0.33;
        ctx.fillStyle = warm
          ? `rgba(255, 224, 178, ${a})`
          : `rgba(198, 224, 255, ${a})`;
      } else {
        ctx.fillStyle = 'rgba(11, 17, 27, 0.94)';
      }
      ctx.fillRect(x, y, ww, wh);
    }
  }
  // 楼板线：横向细分隔，远看形成真实幕墙纹理
  ctx.fillStyle = 'rgba(70, 92, 122, 0.20)';
  for (let r = 1; r < rows; r++) ctx.fillRect(0, r * ch, width, 0.7);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.anisotropy = 4;
  return tex;
}

/** 香港特色霓虹招牌：发光描边文字 + 深色底板，可横排或竖排。 */
export function createNeonSignTexture(text, color, { vertical = false } = {}) {
  const width = vertical ? 96 : 320;
  const height = vertical ? 320 : 96;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = 'rgba(4, 7, 14, 0.88)';
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.shadowColor = color;
  ctx.shadowBlur = 14;
  ctx.strokeRect(5, 5, width - 10, height - 10);

  const fontSize = vertical ? 54 : 54;
  ctx.font = `bold ${fontSize}px "Microsoft YaHei", "SimHei", sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = color;
  ctx.shadowColor = color;
  ctx.shadowBlur = 20;
  const chars = [...text];
  const drawText = () => {
    if (vertical) {
      chars.forEach((ch, i) => ctx.fillText(ch, width / 2, 46 + i * 52));
    } else {
      ctx.fillText(text, width / 2, height / 2 + 2);
    }
  };
  drawText();
  ctx.shadowBlur = 6;
  drawText();

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** 程序化生成水面法线贴图（多组正弦波叠加，解析求导）。 */
export function createWaveNormalTexture(size = 256) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(size, size);
  // 多尺度值噪声：无周期对称，水面不会出现规则菱形网格
  const hash = (x, y, seed) => {
    let h = Math.imul((x & 63) + seed * 17, 374761393) + Math.imul((y & 63) + seed * 31, 668265263);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
  };
  const noise = (x, y, seed) => {
    const xi = Math.floor(x);
    const yi = Math.floor(y);
    const xf = x - xi;
    const yf = y - yi;
    const u = xf * xf * (3 - 2 * xf);
    const v = yf * yf * (3 - 2 * yf);
    const a = hash(xi, yi, seed);
    const b = hash(xi + 1, yi, seed);
    const c = hash(xi, yi + 1, seed);
    const d = hash(xi + 1, yi + 1, seed);
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v;
  };
  const fbm = (x, y) =>
    noise(x, y, 1) * 0.5 +
    noise(x * 2, y * 2, 2) * 0.25 +
    noise(x * 4, y * 4, 3) * 0.125 +
    noise(x * 8, y * 8, 4) * 0.0625;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const u = (x / size) * 8;
      const v = (y / size) * 8;
      const eps = 0.02;
      const nx = -(fbm(u + eps, v) - fbm(u - eps, v)) * 6;
      const ny = -(fbm(u, v + eps) - fbm(u, v - eps)) * 6;
      const len = Math.hypot(nx, ny, 1);
      const i = (y * size + x) * 4;
      img.data[i] = ((nx / len) * 0.5 + 0.5) * 255;
      img.data[i + 1] = ((ny / len) * 0.5 + 0.5) * 255;
      img.data[i + 2] = (1 / len) * 0.5 * 255 + 127.5;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  return tex;
}

/** 径向渐变贴图，用于月亮光晕与灯光晕染。 */
export function createGlowTexture(inner = 'rgba(255,236,190,1)', outer = 'rgba(255,190,120,0)') {
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, inner);
  g.addColorStop(0.25, inner);
  g.addColorStop(1, outer);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

/** 尾迹贴图：横向渐隐的白色水花。 */
export function createWakeTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  const g = ctx.createLinearGradient(0, 0, 256, 0);
  g.addColorStop(0, 'rgba(255,255,255,0)');
  g.addColorStop(0.5, 'rgba(210,235,255,0.35)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 256, 64);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}
