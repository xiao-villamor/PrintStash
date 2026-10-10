import hashlib, json, platform, time
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
import numpy as np
import trimesh
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE
from printstash_core.mesh.render_geometry import prepare_mesh_render
from printstash_core.mesh.rasterizer import render_prepared_pixels, encode_rendered_pixels
from scripts.wgpu_render_backend import GpuContext, GpuFrame

root = Path('physical-controls')
root.mkdir(exist_ok=True)
controls = {
 'cube': trimesh.creation.box(extents=[4, 2, 6]),
 'hole-torus': trimesh.creation.torus(major_radius=3, minor_radius=1, major_sections=32, minor_sections=16),
}
manifest = {
 'tested_commit': '969b296685bb6da41b5649d30c2cf3f2bb8bfeb4',
 'scope': 'Windows development controls: reused preparation, cold GPU contexts; not supervised Job qualification',
 'width': 640, 'height': 480, 'observations_per_control': 30,
 'foreground_alpha': 128, 'maximum_channel_difference': 8, 'mask_difference': 0,
 'platform': platform.platform(), 'python': platform.python_version(),
 'versions': {name: version(name) for name in ['wgpu','numpy','scipy','pillow','trimesh']},
 'sources': {name: hashlib.sha256(mesh.export(file_type='stl')).hexdigest() for name,mesh in controls.items()},
}
with (root/'manifest.json').open('x') as f: json.dump(manifest,f,indent=2)
rows = []
with (root/'samples.jsonl').open('x') as out:
 for name, mesh in controls.items():
  prepared = prepare_mesh_render(mesh, face_chunk_size=64000)
  radius = float(np.linalg.norm(np.ptp(prepared.vertices,axis=0))) + 1
  previous_hash = None
  for trial in range(30):
   row = {'case':name,'trial':trial,'status':'failed'}
   context = frame = None
   try:
    samples = {}
    for method in (['cpu','gpu'] if trial % 2 == 0 else ['gpu','cpu']):
     start = time.perf_counter()
     if method == 'gpu':
      context = GpuContext.create()
      row['adapter'] = context.info
      row['initialization_ms'] = (time.perf_counter()-start)*1000
      ss = PREVIEW_PROFILE.supersample_for(640)
      frame = GpuFrame(context,640*ss,480*ss,64000,radius,allocation_limit_bytes=256*1024**2)
     pixels = render_prepared_pixels(prepared,name,640,480,rasterise_triangles=frame if method=='gpu' else None)
     if pixels is None: raise RuntimeError(str(frame.failure) if frame is not None else 'cpu_render_failed')
     encoded = encode_rendered_pixels(pixels,output_format='WEBP')
     row[method+'_render_encode_ms'] = (time.perf_counter()-start)*1000
     samples[method] = np.frombuffer(pixels.rgba,dtype=np.uint8).reshape(-1,4)
     if method == 'gpu':
      row['stats'] = asdict(frame.stats)
      frame.close()
      frame = None
      context.close()
      context = None
    a,b = samples['cpu'],samples['gpu']
    row['mask_different_pixels'] = int(np.count_nonzero((a[:,3]>=128)!=(b[:,3]>=128)))
    row['maximum_channel_difference'] = int(np.abs(a.astype(np.int16)-b.astype(np.int16)).max())
    digest = hashlib.sha256(b.tobytes()).hexdigest()
    row['stable'] = previous_hash is None or digest == previous_hash
    previous_hash = digest
    row['quality_passed'] = row['mask_different_pixels']==0 and row['maximum_channel_difference']<=8 and row['stable']
    row['status'] = 'completed'
   except Exception as exc:
    row['error'] = repr(exc)
   finally:
    if frame is not None: frame.close()
    if context is not None: context.close()
   rows.append(row)
   out.write(json.dumps(row)+'\n')
   out.flush()
 report = {'manifest':manifest,'samples':rows,'production_qualified':False,
           'quality_passed':all(row.get('quality_passed',False) for row in rows)}
 (root/'report.json').write_text(json.dumps(report,indent=2))
 print(json.dumps({'observations':len(rows),'quality_passed':report['quality_passed'],'production_qualified':False}))
