(function () {
  'use strict';

  function createFallbackProxy(THREE, reason) {
    const group = new THREE.Group();
    const boxGeom = new THREE.BoxGeometry(1.1, 1.4, 0.8);
    const mat = new THREE.MeshStandardMaterial({
      color: 0x3b82f6,
      roughness: 0.4,
      metalness: 0.5,
      transparent: true,
      opacity: 0.35,
    });
    const mesh = new THREE.Mesh(boxGeom, mat);
    const edges = new THREE.EdgesGeometry(boxGeom);
    const wire = new THREE.LineSegments(
      edges,
      new THREE.LineBasicMaterial({ color: 0x60a5fa })
    );
    group.add(mesh);
    group.add(wire);
    return {
      root: group,
      stats: {
        vertices: 24,
        faces: 12,
        meshes: 1,
        materials: ['FallbackProxy'],
        animations: [],
        hasTextures: false,
        format: 'PROXY',
        isFallback: true,
        fallbackReason: reason || 'Unsupported format',
      },
    };
  }

  function centerAndScaleObject(THREE, root, targetSize) {
    const sizeTarget = targetSize || 1.6;
    const box = new THREE.Box3().setFromObject(root);
    if (box.isEmpty()) return;
    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    box.getCenter(center);
    box.getSize(size);
    const maxDim = Math.max(size.x, size.y, size.z);
    const scale = maxDim > 1e-6 ? sizeTarget / maxDim : 1.0;
    root.scale.multiplyScalar(scale);
    root.position.sub(center.multiplyScalar(scale));
  }

  function numComponentsForType(typeStr) {
    if (typeStr === 'SCALAR') return 1;
    if (typeStr === 'VEC2') return 2;
    if (typeStr === 'VEC3') return 3;
    if (typeStr === 'VEC4') return 4;
    if (typeStr === 'MAT4') return 16;
    return 1;
  }

  function componentByteSize(componentType) {
    if (componentType === 5120 || componentType === 5121) return 1;
    if (componentType === 5122 || componentType === 5123) return 2;
    if (componentType === 5125 || componentType === 5126) return 4;
    return 4;
  }

  function readComponent(view, byteOffset, componentType, normalized) {
    let val = 0;
    if (componentType === 5126) {
      return view.getFloat32(byteOffset, true);
    }
    if (componentType === 5120) {
      val = view.getInt8(byteOffset);
      return normalized ? Math.max(val / 127, -1) : val;
    }
    if (componentType === 5121) {
      val = view.getUint8(byteOffset);
      return normalized ? val / 255 : val;
    }
    if (componentType === 5122) {
      val = view.getInt16(byteOffset, true);
      return normalized ? Math.max(val / 32767, -1) : val;
    }
    if (componentType === 5123) {
      val = view.getUint16(byteOffset, true);
      return normalized ? val / 65535 : val;
    }
    if (componentType === 5125) {
      return view.getUint32(byteOffset, true);
    }
    return 0;
  }

  function readAccessor(gltf, buffers, accessorIndex, asFloat) {
    const acc = gltf.accessors && gltf.accessors[accessorIndex];
    if (!acc || acc.bufferView === undefined) return null;
    const bv = gltf.bufferViews && gltf.bufferViews[acc.bufferView];
    if (!bv) return null;
    const buf = buffers[bv.buffer || 0];
    if (!buf) return null;

    const comps = numComponentsForType(acc.type);
    const compSize = componentByteSize(acc.componentType);
    const packedStride = comps * compSize;
    const stride = bv.byteStride && bv.byteStride > 0 ? bv.byteStride : packedStride;
    const baseOffset = (bv.byteOffset || 0) + (acc.byteOffset || 0);
    const count = acc.count || 0;
    const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);

    if (asFloat) {
      const out = new Float32Array(count * comps);
      for (let i = 0; i < count; i++) {
        const elemOffset = baseOffset + i * stride;
        for (let c = 0; c < comps; c++) {
          out[i * comps + c] = readComponent(
            view,
            elemOffset + c * compSize,
            acc.componentType,
            Boolean(acc.normalized)
          );
        }
      }
      return out;
    }

    const useUint32 = acc.componentType === 5125 || count > 65535;
    const out = useUint32 ? new Uint32Array(count * comps) : new Uint16Array(count * comps);
    for (let i = 0; i < count; i++) {
      const elemOffset = baseOffset + i * stride;
      for (let c = 0; c < comps; c++) {
        out[i * comps + c] = readComponent(view, elemOffset + c * compSize, acc.componentType, false);
      }
    }
    return out;
  }

  function decodeBase64Bytes(base64Str) {
    const binStr = atob(base64Str);
    const len = binStr.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binStr.charCodeAt(i);
    }
    return bytes;
  }

  function loadGltfImageTexture(THREE, gltf, buffers, texIndex, baseUrl) {
    if (typeof Image === 'undefined' || !gltf.textures || !gltf.images) return null;
    const texDef = gltf.textures[texIndex];
    if (!texDef || texDef.source === undefined) return null;
    const imgDef = gltf.images[texDef.source];
    if (!imgDef) return null;

    let srcUrl = null;
    let revokeAfterLoad = false;

    if (imgDef.bufferView !== undefined) {
      const bv = gltf.bufferViews && gltf.bufferViews[imgDef.bufferView];
      if (!bv) return null;
      const buf = buffers[bv.buffer || 0];
      if (!buf) return null;
      const slice = buf.subarray(bv.byteOffset || 0, (bv.byteOffset || 0) + (bv.byteLength || 0));
      if (typeof Blob !== 'undefined' && typeof URL !== 'undefined' && URL.createObjectURL) {
        const blob = new Blob([slice], { type: imgDef.mimeType || 'image/png' });
        srcUrl = URL.createObjectURL(blob);
        revokeAfterLoad = true;
      }
    } else if (typeof imgDef.uri === 'string' && imgDef.uri) {
      if (imgDef.uri.startsWith('data:') || !baseUrl) {
        srcUrl = imgDef.uri;
      } else {
        try {
          srcUrl = new URL(imgDef.uri, baseUrl).href;
        } catch (_) {
          srcUrl = imgDef.uri;
        }
      }
    }

    if (!srcUrl) return null;
    const texture = new THREE.Texture();
    texture.flipY = false;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = function () {
      texture.image = img;
      texture.needsUpdate = true;
      if (revokeAfterLoad && typeof URL !== 'undefined' && URL.revokeObjectURL) {
        URL.revokeObjectURL(srcUrl);
      }
    };
    img.onerror = function () {
      if (revokeAfterLoad && typeof URL !== 'undefined' && URL.revokeObjectURL) {
        URL.revokeObjectURL(srcUrl);
      }
    };
    img.src = srcUrl;
    return texture;
  }

  function buildGltfMaterial(THREE, gltf, buffers, matIdx, baseUrl, materialNames, textureFlag) {
    if (matIdx === undefined || !gltf.materials || !gltf.materials[matIdx]) {
      return new THREE.MeshStandardMaterial({
        color: 0xd4d4d8,
        roughness: 0.6,
        metalness: 0.2,
      });
    }
    const matDef = gltf.materials[matIdx];
    if (matDef.name && materialNames.indexOf(matDef.name) === -1) {
      materialNames.push(matDef.name);
    }
    const pbr = matDef.pbrMetallicRoughness || {};
    const factor = Array.isArray(pbr.baseColorFactor) ? pbr.baseColorFactor : [1, 1, 1, 1];
    const params = {
      color: new THREE.Color(factor[0], factor[1], factor[2]),
      roughness: pbr.roughnessFactor !== undefined ? pbr.roughnessFactor : 0.65,
      metalness: pbr.metallicFactor !== undefined ? pbr.metallicFactor : 0.15,
    };
    if (factor[3] < 1.0 || matDef.alphaMode === 'BLEND') {
      params.transparent = true;
      params.opacity = factor[3];
    }
    if (matDef.doubleSided) {
      params.side = THREE.DoubleSide;
    }
    if (pbr.baseColorTexture && pbr.baseColorTexture.index !== undefined) {
      const tex = loadGltfImageTexture(THREE, gltf, buffers, pbr.baseColorTexture.index, baseUrl);
      if (tex) {
        params.map = tex;
        textureFlag.hasTextures = true;
      }
    }
    return new THREE.MeshStandardMaterial(params);
  }

  function buildThreeGroupFromGltf(THREE, gltf, buffers, formatLabel, baseUrl) {
    const root = new THREE.Group();
    let totalVertices = 0;
    let totalFaces = 0;
    let meshCount = 0;
    const materialNames = [];
    const textureFlag = { hasTextures: false };

    function buildMeshObject(meshIdx) {
      const meshDef = gltf.meshes && gltf.meshes[meshIdx];
      if (!meshDef || !Array.isArray(meshDef.primitives)) return null;
      const meshGroup = new THREE.Group();
      for (const prim of meshDef.primitives) {
        if (!prim.attributes || prim.attributes.POSITION === undefined) continue;
        const pos = readAccessor(gltf, buffers, prim.attributes.POSITION, true);
        if (!pos || pos.length < 3) continue;

        const geom = new THREE.BufferGeometry();
        geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        const vertCount = Math.floor(pos.length / 3);
        totalVertices += vertCount;

        if (prim.attributes.NORMAL !== undefined) {
          const norm = readAccessor(gltf, buffers, prim.attributes.NORMAL, true);
          if (norm) geom.setAttribute('normal', new THREE.BufferAttribute(norm, 3));
        }
        if (prim.attributes.TEXCOORD_0 !== undefined) {
          const uv = readAccessor(gltf, buffers, prim.attributes.TEXCOORD_0, true);
          if (uv) geom.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
        }
        if (prim.indices !== undefined) {
          const indices = readAccessor(gltf, buffers, prim.indices, false);
          if (indices) {
            geom.setIndex(new THREE.BufferAttribute(indices, 1));
            totalFaces += Math.floor(indices.length / 3);
          } else {
            totalFaces += Math.floor(vertCount / 3);
          }
        } else {
          totalFaces += Math.floor(vertCount / 3);
        }
        if (!geom.getAttribute('normal')) {
          geom.computeVertexNormals();
        }
        const material = buildGltfMaterial(THREE, gltf, buffers, prim.material, baseUrl, materialNames, textureFlag);
        meshGroup.add(new THREE.Mesh(geom, material));
        meshCount += 1;
      }
      return meshGroup;
    }

    function buildNode(nodeIdx) {
      const nodeDef = gltf.nodes && gltf.nodes[nodeIdx];
      if (!nodeDef) return null;
      const nodeObj = new THREE.Group();
      if (nodeDef.name) nodeObj.name = nodeDef.name;
      if (Array.isArray(nodeDef.matrix) && nodeDef.matrix.length === 16) {
        const mat = new THREE.Matrix4().fromArray(nodeDef.matrix);
        nodeObj.applyMatrix4(mat);
      } else {
        if (Array.isArray(nodeDef.translation)) {
          nodeObj.position.set(nodeDef.translation[0], nodeDef.translation[1], nodeDef.translation[2]);
        }
        if (Array.isArray(nodeDef.rotation)) {
          nodeObj.quaternion.set(nodeDef.rotation[0], nodeDef.rotation[1], nodeDef.rotation[2], nodeDef.rotation[3]);
        }
        if (Array.isArray(nodeDef.scale)) {
          nodeObj.scale.set(nodeDef.scale[0], nodeDef.scale[1], nodeDef.scale[2]);
        }
      }
      if (nodeDef.mesh !== undefined) {
        const m = buildMeshObject(nodeDef.mesh);
        if (m) nodeObj.add(m);
      }
      if (Array.isArray(nodeDef.children)) {
        for (const childIdx of nodeDef.children) {
          const child = buildNode(childIdx);
          if (child) nodeObj.add(child);
        }
      }
      return nodeObj;
    }

    const sceneIdx = typeof gltf.scene === 'number' ? gltf.scene : 0;
    const sceneDef = gltf.scenes && gltf.scenes[sceneIdx];
    if (sceneDef && Array.isArray(sceneDef.nodes) && Array.isArray(gltf.nodes)) {
      for (const nIdx of sceneDef.nodes) {
        const nodeObj = buildNode(nIdx);
        if (nodeObj) root.add(nodeObj);
      }
    } else if (Array.isArray(gltf.meshes)) {
      for (let i = 0; i < gltf.meshes.length; i++) {
        const m = buildMeshObject(i);
        if (m) root.add(m);
      }
    }

    if (meshCount === 0 || totalVertices === 0) {
      throw new Error('glTF contains no renderable mesh primitives');
    }

    centerAndScaleObject(THREE, root, 1.6);
    const animNames = Array.isArray(gltf.animations)
      ? gltf.animations.map((a, idx) => (a && a.name) || `clip_${idx + 1}`)
      : [];

    return {
      root,
      stats: {
        vertices: totalVertices,
        faces: totalFaces,
        meshes: meshCount,
        materials: materialNames,
        animations: animNames,
        hasTextures: textureFlag.hasTextures,
        format: formatLabel,
        isFallback: false,
      },
    };
  }

  function parseGlbBuffer(THREE, arrayBuffer, baseUrl) {
    if (!arrayBuffer || arrayBuffer.byteLength < 12) {
      throw new Error('GLB buffer too small');
    }
    const headerView = new DataView(arrayBuffer, 0, 12);
    const magic = headerView.getUint32(0, true);
    const version = headerView.getUint32(4, true);
    if (magic !== 0x46546C67 || version !== 2) {
      throw new Error('Invalid GLB v2 header');
    }

    let offset = 12;
    let jsonChunk = null;
    let binChunk = null;
    while (offset + 8 <= arrayBuffer.byteLength) {
      const chunkView = new DataView(arrayBuffer, offset, 8);
      const chunkLen = chunkView.getUint32(0, true);
      const chunkType = chunkView.getUint32(4, true);
      const chunkStart = offset + 8;
      const chunkEnd = Math.min(chunkStart + chunkLen, arrayBuffer.byteLength);
      if (chunkType === 0x4E4F534A) {
        const jsonBytes = new Uint8Array(arrayBuffer, chunkStart, chunkEnd - chunkStart);
        jsonChunk = JSON.parse(new TextDecoder('utf-8').decode(jsonBytes));
      } else if (chunkType === 0x004E4942) {
        binChunk = new Uint8Array(arrayBuffer, chunkStart, chunkEnd - chunkStart);
      }
      offset = chunkStart + chunkLen;
    }

    if (!jsonChunk) {
      throw new Error('Missing JSON chunk in GLB');
    }
    const buffers = binChunk ? [binChunk] : [];
    return buildThreeGroupFromGltf(THREE, jsonChunk, buffers, 'GLB', baseUrl);
  }

  async function parseGltfJson(THREE, text, baseUrl, fetchOpts) {
    const gltf = JSON.parse(text);
    const buffers = [];
    const bufDefs = Array.isArray(gltf.buffers) ? gltf.buffers : [];
    for (const bDef of bufDefs) {
      if (!bDef || !bDef.uri) {
        buffers.push(new Uint8Array(0));
        continue;
      }
      if (bDef.uri.startsWith('data:')) {
        const commaIdx = bDef.uri.indexOf(',');
        const b64 = commaIdx >= 0 ? bDef.uri.slice(commaIdx + 1) : '';
        buffers.push(decodeBase64Bytes(b64));
      } else if (typeof fetch === 'function') {
        const bufUrl = baseUrl ? new URL(bDef.uri, baseUrl).href : bDef.uri;
        const resp = await fetch(bufUrl, fetchOpts || {});
        if (!resp.ok) throw new Error(`Failed to fetch glTF buffer (${resp.status})`);
        buffers.push(new Uint8Array(await resp.arrayBuffer()));
      } else {
        buffers.push(new Uint8Array(0));
      }
    }
    return buildThreeGroupFromGltf(THREE, gltf, buffers, 'GLTF', baseUrl);
  }

  function parseObjText(THREE, text) {
    const verts = [];
    const normals = [];
    const uvs = [];
    const outPos = [];
    const outNorm = [];
    const outUv = [];
    let hasNormals = false;
    let hasUvs = false;
    let faceCount = 0;

    function resolveIndex(rawIdx, count) {
      const idx = parseInt(rawIdx, 10);
      if (Number.isNaN(idx) || idx === 0) return -1;
      return idx > 0 ? idx - 1 : count + idx;
    }

    const lines = text.split(/\r?\n/);
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || line.startsWith('#')) continue;
      const parts = line.split(/\s+/);
      const tag = parts[0];
      if (tag === 'v' && parts.length >= 4) {
        verts.push(parseFloat(parts[1]) || 0, parseFloat(parts[2]) || 0, parseFloat(parts[3]) || 0);
      } else if (tag === 'vn' && parts.length >= 4) {
        normals.push(parseFloat(parts[1]) || 0, parseFloat(parts[2]) || 0, parseFloat(parts[3]) || 0);
      } else if (tag === 'vt' && parts.length >= 3) {
        uvs.push(parseFloat(parts[1]) || 0, parseFloat(parts[2]) || 0);
      } else if (tag === 'f' && parts.length >= 4) {
        const tokens = parts.slice(1);
        for (let i = 1; i < tokens.length - 1; i++) {
          const tri = [tokens[0], tokens[i], tokens[i + 1]];
          for (const tok of tri) {
            const segs = tok.split('/');
            const vIdx = resolveIndex(segs[0], verts.length / 3);
            if (vIdx >= 0 && vIdx * 3 + 2 < verts.length) {
              outPos.push(verts[vIdx * 3], verts[vIdx * 3 + 1], verts[vIdx * 3 + 2]);
            } else {
              outPos.push(0, 0, 0);
            }
            const tIdx = segs.length > 1 && segs[1] ? resolveIndex(segs[1], uvs.length / 2) : -1;
            if (tIdx >= 0 && tIdx * 2 + 1 < uvs.length) {
              hasUvs = true;
              outUv.push(uvs[tIdx * 2], uvs[tIdx * 2 + 1]);
            } else {
              outUv.push(0, 0);
            }
            const nIdx = segs.length > 2 && segs[2] ? resolveIndex(segs[2], normals.length / 3) : -1;
            if (nIdx >= 0 && nIdx * 3 + 2 < normals.length) {
              hasNormals = true;
              outNorm.push(normals[nIdx * 3], normals[nIdx * 3 + 1], normals[nIdx * 3 + 2]);
            } else {
              outNorm.push(0, 0, 0);
            }
          }
          faceCount += 1;
        }
      }
    }

    if (outPos.length === 0) {
      throw new Error('OBJ contains no face geometry');
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(outPos), 3));
    if (hasNormals) {
      geom.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(outNorm), 3));
    } else {
      geom.computeVertexNormals();
    }
    if (hasUvs) {
      geom.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(outUv), 2));
    }

    const mat = new THREE.MeshStandardMaterial({
      color: 0xd4d4d8,
      roughness: 0.55,
      metalness: 0.2,
    });
    const root = new THREE.Group();
    root.add(new THREE.Mesh(geom, mat));
    centerAndScaleObject(THREE, root, 1.6);

    return {
      root,
      stats: {
        vertices: Math.floor(verts.length / 3),
        faces: faceCount,
        meshes: 1,
        materials: ['OBJDefault'],
        animations: [],
        hasTextures: false,
        format: 'OBJ',
        isFallback: false,
      },
    };
  }

  async function loadModelFromUrl(THREE, url, filenameOrExt, fetchOpts) {
    const extRaw = String(filenameOrExt || url || '').split('?')[0].split('#')[0];
    const ext = extRaw.includes('.') ? '.' + extRaw.split('.').pop().toLowerCase() : extRaw.toLowerCase();

    if (!url || ext === '.fbx' || ext === '.usdz') {
      const reason = !url ? 'No model URL provided' : `${ext.toUpperCase()} uses bounding-box preview proxy`;
      return createFallbackProxy(THREE, reason);
    }

    try {
      const res = await fetch(url, fetchOpts || {});
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      if (ext === '.obj') {
        const text = await res.text();
        return parseObjText(THREE, text);
      }
      if (ext === '.gltf') {
        const text = await res.text();
        return await parseGltfJson(THREE, text, new URL(url, window.location.href).href, fetchOpts);
      }
      // Default to .glb
      const buf = await res.arrayBuffer();
      const absUrl = typeof window !== 'undefined' ? new URL(url, window.location.href).href : url;
      return parseGlbBuffer(THREE, buf, absUrl);
    } catch (err) {
      return createFallbackProxy(THREE, err && err.message ? err.message : 'Model load failed');
    }
  }

  function disposeObject3D(root) {
    if (!root || typeof root.traverse !== 'function') return;
    root.traverse((obj) => {
      if (obj.geometry && typeof obj.geometry.dispose === 'function') {
        obj.geometry.dispose();
      }
      const mats = Array.isArray(obj.material) ? obj.material : (obj.material ? [obj.material] : []);
      for (const m of mats) {
        if (m.map && typeof m.map.dispose === 'function') m.map.dispose();
        if (typeof m.dispose === 'function') m.dispose();
      }
    });
  }

  const api = {
    parseGlbBuffer,
    parseGltfJson,
    parseObjText,
    loadModelFromUrl,
    createFallbackProxy,
    disposeObject3D,
  };

  if (typeof window !== 'undefined') {
    window.AsxModel3DLoader = api;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})();
