({ vertex, stages, size }) => {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const gl = canvas.getContext("webgl2", { alpha: true, premultipliedAlpha: false, antialias: false });
  if (!gl || !gl.getExtension("EXT_color_buffer_float")) {
    throw new Error("WebGL 2 with floating-point color buffers is required");
  }

  function compile(type, code) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, code);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader));
    }
    return shader;
  }

  function texture(width, height, floatingPoint) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texStorage2D(gl.TEXTURE_2D, 1, floatingPoint ? gl.RGBA16F : gl.RGBA8, width, height);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return tex;
  }

  function framebuffer(tex) {
    const buffer = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, buffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
      throw new Error("Could not attach float render target");
    }
    return buffer;
  }

  const art = texture(size, size, false);
  const outputs = new Map([["art", art]]);
  const images = new Map();
  const vertexShader = compile(gl.VERTEX_SHADER, vertex);
  const passes = stages.map(([name, code], index) => {
    const program = gl.createProgram();
    try {
      gl.attachShader(program, vertexShader);
      gl.attachShader(program, compile(gl.FRAGMENT_SHADER, code));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error(gl.getProgramInfoLog(program));
      }
    } catch (error) {
      throw new Error(`Could not compile pass "${name}": ${error.message}`);
    }
    const members = [];
    for (let i = 0; i < gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS); i++) {
      const member = gl.getActiveUniform(program, i);
      members.push([member.name.replace(/\[0\]$/, ""), member.type, member.size]);
    }
    let target = null;
    if (index < stages.length - 1) {
      const output = texture(size, size, true);
      outputs.set(name, output);
      target = framebuffer(output);
    }
    return { name, program, target, members };
  });

  function upload(tex, width, height, base64) {
    const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, bytes);
  }

  window.loadTexture = ([name, width, height, encoded]) => {
    gl.activeTexture(gl.TEXTURE0);
    const tex = texture(width, height, false);
    upload(tex, width, height, encoded);
    images.set(name, tex);
  };

  const types = { f: "f", i: "i", I: "ui" };
  window.renderCel = ([encoded, bound]) => {
    gl.activeTexture(gl.TEXTURE0);
    upload(art, size, size, encoded);
    gl.viewport(0, 0, size, size);
    for (const [index, { name, program, target }] of passes.entries()) {
      gl.useProgram(program);
      gl.bindFramebuffer(gl.FRAMEBUFFER, target);
      let unit = 0;
      for (const [uniform, family, dimensions, values] of bound[index]) {
        const location = gl.getUniformLocation(program, uniform);
        if (family === "texture") {
          const units = values.map(([kind, key]) => {
            gl.activeTexture(gl.TEXTURE0 + unit);
            gl.bindTexture(gl.TEXTURE_2D, (kind === "image" ? images : outputs).get(key));
            return unit++;
          });
          gl.uniform1iv(location, units);
        } else if (family === "m") {
          const [columns, rows] = dimensions;
          gl[`uniformMatrix${columns === rows ? columns : `${columns}x${rows}`}fv`](location, false, values);
        } else {
          gl[`uniform${dimensions}${types[family]}v`](location, values);
        }
      }
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      const error = gl.getError();
      if (error !== gl.NO_ERROR) throw new Error(`WebGL pass "${name}" failed: ${error}`);
    }
    return canvas.toDataURL("image/png").split(",")[1];
  };
  return passes.map(({ members }) => members);
}
