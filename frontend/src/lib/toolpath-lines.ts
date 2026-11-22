import {
  InstancedInterleavedBuffer,
  InterleavedBufferAttribute,
  Vector2,
  type WebGLProgramParametersWithUniforms,
} from "three";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { featureIndex, type ToolpathFeature } from "@/lib/gcode-features";
import type { ToolpathData } from "@/lib/gcode";

const FALLBACK_WIDTH_MM = 0.4;

/**
 * Printed lines are about twice as wide as a layer is tall, so neighbouring
 * layers' tubes touch and a wall reads as a surface with grooves between layers.
 */
export function toolpathLineWidth(layers: ToolpathData["layerRanges"]): number {
  const steps: number[] = [];
  for (let index = 1; index < layers.length; index++) {
    const step = layers[index]!.z - layers[index - 1]!.z;
    if (step > 0) steps.push(step);
  }
  if (steps.length === 0) return FALLBACK_WIDTH_MM;
  steps.sort((a, b) => a - b);
  const layerHeight = steps[Math.floor(steps.length / 2)]!;
  return Math.min(1, Math.max(0.25, layerHeight * 2));
}

/** Bit mask of hidden features, as the shader tests them. */
export function hiddenFeatureMask(hidden: Iterable<ToolpathFeature>): number {
  let mask = 0;
  for (const feature of hidden) mask |= 1 << featureIndex(feature);
  return mask;
}

const VERTEX_PARS = /* glsl */ `
attribute float instanceFeature;
uniform int hiddenFeatures;
uniform vec2 segmentRange;
`;

// A hidden or out-of-range segment collapses outside the clip volume.
const VERTEX_CULL = /* glsl */ `
  int feature = int( instanceFeature + 0.5 );
  float segment = float( gl_InstanceID );
  if ( ( ( hiddenFeatures >> feature ) & 1 ) != 0 || segment < segmentRange.x || segment >= segmentRange.y ) {
    gl_Position = vec4( 2.0, 2.0, 2.0, 1.0 );
    return;
  }
`;

// World-unit lines already find how far the view ray passes from the segment's
// axis; that offset gives a round tube's surface normal, lit by a key and a fill
// light fixed to the camera so the print reads the same from every side. A tube
// looks alike from any side, so walls turning away from the camera (their tubes
// running towards it) darken to show the print's shape.
const FRAGMENT_SHADE = /* glsl */ `
  vec3 towardCamera = - normalize( p2 );
  vec3 axis = normalize( lineDir );
  vec3 across = towardCamera - axis * dot( towardCamera, axis );
  float facing = length( across );
  float radius = linewidth * 0.5;
  vec3 tubeNormal = normalize( - delta + across / max( facing, 1e-4 ) * sqrt( max( radius * radius - len * len, 0.0 ) ) );
  vec3 keyLight = normalize( vec3( -0.45, 0.75, 0.5 ) );
  vec3 fillLight = normalize( vec3( 0.6, -0.2, 0.75 ) );
  float shade = 0.2
    + 0.75 * max( dot( tubeNormal, keyLight ), 0.0 )
    + 0.25 * max( dot( tubeNormal, fillLight ), 0.0 );
  shade *= mix( 0.45, 1.0, facing * facing );
  float highlight = pow( max( dot( tubeNormal, normalize( keyLight + towardCamera ) ), 0.0 ), 24.0 );
  gl_FragColor = vec4( diffuseColor.rgb * shade + 0.12 * highlight * facing, alpha );
`;

export type ToolpathLines = {
  lines: LineSegments2;
  /** Draw only segments `[start, end)`, as the layer controls choose. */
  showSegments(start: number, end: number): void;
  hideFeatures(hidden: Iterable<ToolpathFeature>): void;
  dispose(): void;
};

/**
 * Extrusions drawn as shaded tubes as wide as the printed line. WebGL draws
 * plain lines one pixel wide, which leaves a print hard to make out.
 */
export function createToolpathLines(data: ToolpathData): ToolpathLines {
  const segments = data.extrudePositions.length / 6;
  const geometry = new LineSegmentsGeometry();
  geometry.setPositions(data.extrudePositions);
  // Colours and features are stored per vertex: two vertices per segment.
  const colours = new InstancedInterleavedBuffer(data.extrudeColors, 8, 1);
  geometry.setAttribute("instanceColorStart", new InterleavedBufferAttribute(colours, 3, 0, true));
  geometry.setAttribute("instanceColorEnd", new InterleavedBufferAttribute(colours, 3, 4, true));
  const features = new InstancedInterleavedBuffer(data.extrudeFeatures, 2, 1);
  geometry.setAttribute("instanceFeature", new InterleavedBufferAttribute(features, 1, 0));

  const uniforms = {
    hiddenFeatures: { value: 0 },
    segmentRange: { value: new Vector2(0, segments) },
  };
  const material = new LineMaterial({
    vertexColors: true,
    worldUnits: true,
    linewidth: toolpathLineWidth(data.layerRanges),
  });
  material.onBeforeCompile = (shader: WebGLProgramParametersWithUniforms) => {
    shader.uniforms.hiddenFeatures = uniforms.hiddenFeatures;
    shader.uniforms.segmentRange = uniforms.segmentRange;
    shader.vertexShader = shader.vertexShader.replace(
      "void main() {",
      `${VERTEX_PARS}\nvoid main() {\n${VERTEX_CULL}`,
    );
    shader.fragmentShader = shader.fragmentShader.replace(
      "gl_FragColor = vec4( diffuseColor.rgb, alpha );",
      FRAGMENT_SHADE,
    );
  };

  const lines = new LineSegments2(geometry, material);
  return {
    lines,
    showSegments(start, end) {
      uniforms.segmentRange.value.set(start, end);
    },
    hideFeatures(hidden) {
      uniforms.hiddenFeatures.value = hiddenFeatureMask(hidden);
    },
    dispose() {
      geometry.dispose();
      material.dispose();
    },
  };
}
