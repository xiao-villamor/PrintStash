"use client";

import React, { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useLoader, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import * as THREE from "three";
import { STLLoader } from "three-stdlib";
import { AlertTriangle, Loader2 } from "lucide-react";

import { authHeaders } from "@/lib/api/request";

export type ViewerDisplayMode = "solid" | "xray" | "wireframe";

export interface STLViewerControls {
  zoomIn: () => void;
  zoomOut: () => void;
  resetView: () => void;
  fit: () => void;
  screenshot: () => void;
}

export interface STLViewerProps {
  url: string;
  onControlsReady?: (api: STLViewerControls) => void;
  displayMode?: ViewerDisplayMode;
  showGrid?: boolean;
  screenshotName?: string;
}

const DEFAULT_CAMERA_POSITION = new THREE.Vector3(12, 9, 14);
const ZOOM_FACTOR = 0.75;
// Mesh is normalized so its largest dimension equals this many world units.
const NORMALIZED_SIZE = 10;

const box = new THREE.Box3();
const sizeVec = new THREE.Vector3();
const centerVec = new THREE.Vector3();

function Mesh({
  url,
  displayMode,
  onSized,
}: {
  url: string;
  displayMode: ViewerDisplayMode;
  onSized: (size: THREE.Vector3) => void;
}) {
  const geometry = useLoader(STLLoader, url, (loader) => {
    loader.setRequestHeader(authHeaders());
  });
  const meshRef = useRef<THREE.Mesh>(null);

  useEffect(() => {
    if (!meshRef.current) return;
    const mesh = meshRef.current;
    mesh.scale.setScalar(1);
    mesh.position.set(0, 0, 0);
    mesh.updateMatrixWorld();

    box.setFromObject(mesh);
    box.getSize(sizeVec);

    const maxDim = Math.max(sizeVec.x, sizeVec.y, sizeVec.z);
    const scale = maxDim > 0 ? NORMALIZED_SIZE / maxDim : 1;

    mesh.scale.setScalar(scale);
    box.getCenter(centerVec);
    mesh.position.sub(centerVec.multiplyScalar(scale));

    onSized(sizeVec.clone().multiplyScalar(scale));
  }, [geometry, onSized]);

  useEffect(() => {
    return () => {
      geometry.dispose();
    };
  }, [geometry]);

  return (
    <mesh ref={meshRef} geometry={geometry}>
      <meshStandardMaterial
        color="#8a93a6"
        roughness={0.45}
        metalness={0.1}
        wireframe={displayMode === "wireframe"}
        transparent={displayMode === "xray"}
        opacity={displayMode === "xray" ? 0.3 : 1}
        depthWrite={displayMode !== "xray"}
      />
    </mesh>
  );
}

function Scene({
  url,
  onControlsReady,
  onLoadedChange,
  displayMode,
  showGrid,
  screenshotName,
}: Required<Omit<STLViewerProps, "onControlsReady">> & {
  onControlsReady?: (api: STLViewerControls) => void;
  onLoadedChange?: (loaded: boolean) => void;
}) {
  const orbitRef = useRef<any>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera>(null);
  const { gl, scene, camera } = useThree();
  const [size, setSize] = useState(() => new THREE.Vector3(NORMALIZED_SIZE, NORMALIZED_SIZE, NORMALIZED_SIZE));
  const [loaded, setLoaded] = useState(false);
  const fittedRef = useRef(false);
  // Ref so fit() always reads the latest size without stale closure
  const sizeRef = useRef(size);
  useEffect(() => { sizeRef.current = size; }, [size]);

  const handleSized = (s: THREE.Vector3) => {
    setSize(s);
    setLoaded(true);
    onLoadedChange?.(true);
  };

  const gridSize = Math.max(size.x, size.z) * 2.6 || NORMALIZED_SIZE * 2.6;
  const floorY = -size.y / 2;

  const controlsApi: STLViewerControls = {
    zoomIn: () => {
      if (cameraRef.current) {
        cameraRef.current.position.multiplyScalar(ZOOM_FACTOR);
        orbitRef.current?.update();
      }
    },
    zoomOut: () => {
      if (cameraRef.current) {
        cameraRef.current.position.multiplyScalar(1 / ZOOM_FACTOR);
        orbitRef.current?.update();
      }
    },
    resetView: () => {
      orbitRef.current?.reset();
      if (cameraRef.current) {
        cameraRef.current.position.copy(DEFAULT_CAMERA_POSITION);
        cameraRef.current.lookAt(0, 0, 0);
        orbitRef.current?.update();
      }
    },
    fit: () => {
      const cam = cameraRef.current;
      if (!cam) return;
      const s = sizeRef.current;
      const maxDim = Math.max(s.x, s.y, s.z) || NORMALIZED_SIZE;
      const fov = (cam.fov * Math.PI) / 180;
      const distance = (maxDim / 2 / Math.tan(fov / 2)) * 1.7;
      const dir = cam.position.clone().normalize();
      if (dir.lengthSq() === 0) dir.copy(DEFAULT_CAMERA_POSITION).normalize();
      dir.multiplyScalar(distance);
      orbitRef.current?.target?.set(0, 0, 0);
      cam.position.copy(dir);
      cam.lookAt(0, 0, 0);
      orbitRef.current?.update();
    },
    screenshot: () => {
      gl.render(scene, camera);
      const dataUrl = gl.domElement.toDataURL("image/png");
      const link = document.createElement("a");
      link.href = dataUrl;
      link.download = `${screenshotName || "model"}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    },
  };

  useEffect(() => {
    onControlsReady?.(controlsApi);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onControlsReady, size]);

  // Auto-fit once, after the mesh has actually loaded and reported its size.
  useEffect(() => {
    if (loaded && !fittedRef.current) {
      controlsApi.fit();
      fittedRef.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, size]);

  return (
    <>
      <PerspectiveCamera
        ref={cameraRef}
        makeDefault
        position={DEFAULT_CAMERA_POSITION.toArray() as [number, number, number]}
      />
      <ambientLight intensity={0.5} />
      <hemisphereLight args={["#d4e8ff", "#1a1a2e", 0.4]} />
      <directionalLight position={[8, 12, 6]} intensity={1.2} castShadow />
      <directionalLight position={[-6, -4, -8]} intensity={0.25} />
      <directionalLight position={[0, -8, 0]} intensity={0.15} color="#8899bb" />
      <Suspense fallback={null}>
        <Mesh url={url} displayMode={displayMode} onSized={handleSized} />
      </Suspense>
      {showGrid && (
        <gridHelper
          args={[gridSize, 26, "#94a3b8", "#475569"]}
          position={[0, floorY, 0]}
        />
      )}
      <OrbitControls ref={orbitRef} enablePan enableZoom enableRotate />
    </>
  );
}

interface MeshErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface MeshErrorBoundaryState {
  hasError: boolean;
}

class MeshErrorBoundary extends React.Component<MeshErrorBoundaryProps, MeshErrorBoundaryState> {
  constructor(props: MeshErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): MeshErrorBoundaryState {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-[var(--on-surface-variant)]">
            <AlertTriangle className="h-8 w-8" />
            <span className="font-mono text-xs">Failed to load 3D preview</span>
          </div>
        )
      );
    }
    return this.props.children;
  }
}

export function STLViewer({
  url,
  onControlsReady,
  displayMode = "solid",
  showGrid = true,
  screenshotName = "model",
}: STLViewerProps) {
  const [meshLoaded, setMeshLoaded] = useState(false);

  useEffect(() => {
    setMeshLoaded(false);
  }, [url]);

  return (
    <div className="relative h-full w-full">
      <MeshErrorBoundary>
        <Canvas className="h-full w-full" gl={{ preserveDrawingBuffer: true }}>
          <Scene
            url={url}
            onControlsReady={onControlsReady}
            onLoadedChange={setMeshLoaded}
            displayMode={displayMode}
            showGrid={showGrid}
            screenshotName={screenshotName}
          />
        </Canvas>
        {/* Overlay while the mesh downloads/parses — the canvas mounts
            immediately, so without this the viewer is a blank void. */}
        {!meshLoaded && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-[var(--on-surface-variant)]" />
          </div>
        )}
      </MeshErrorBoundary>
    </div>
  );
}
