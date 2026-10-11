import os
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
 os.environ[name]='1'

import ast,hashlib,inspect,json,platform,textwrap,time,traceback
from dataclasses import asdict
from pathlib import Path
from importlib.metadata import version
import numpy as np
import trimesh
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE
from printstash_core.mesh.render_geometry import prepare_mesh_render
from printstash_core.mesh.rasterizer import render_prepared_pixels,encode_rendered_pixels
from scripts import wgpu_render_backend as gpu

root=Path('private-server-pipeline-review-bounded-db41b45d')
root.mkdir()
original=gpu._Frame.draw
tree=ast.parse(textwrap.dedent(inspect.getsource(original)))
tree.body[0].body=[node for node in tree.body[0].body if not (
 isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute)
 and node.value.func.attr in {'copy_buffer_to_buffer','map_sync','unmap'}
)]
namespace={}
exec(compile(tree,'<diagnostic-queued-draw>','exec'),gpu.__dict__,namespace)
queued=namespace['draw']
names=['real-stl-medium','real-3mf-large']
sources={name:Path('private-deployed-corpus')/(name+'.stl') for name in names}
manifest={'commit':'db41b45d61662ee22441de267da123d12f3b040e','observations':30,'native_threads':{n:os.environ[n] for n in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')},'dimensions':[640,480],'python':platform.python_version(),'platform':platform.platform(),'versions':{n:version(n) for n in ['wgpu','numpy','trimesh','pillow']},'sources':{n:hashlib.sha256(p.read_bytes()).hexdigest() for n,p in sources.items()},'variant':'queued draw removes intermediate copy/map/unmap fences only; final readback still waits','scope':'Native Windows diagnostic, retained preparation, cold GPU devices, all render/encode/cleanup costs; no production Job or upload qualification','probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(root/'manifest.json').write_text(json.dumps(manifest,indent=2))
rows=[]
for name,path in sources.items():
 prepared=prepare_mesh_render(trimesh.load_mesh(path,process=False),face_chunk_size=64000)
 radius=float(np.linalg.norm(np.ptp(prepared.vertices,axis=0)))+1
 for trial in range(30):
  pixels_by_method={}
  methods=['cpu','gpu_current','gpu_queued']
  methods=methods[trial%3:]+methods[:trial%3]
  for method in methods:
   row={'case':name,'trial':trial,'method':method,'status':'failed'}
   context=frame=None
   started=time.perf_counter()
   try:
    if method!='cpu':
     gpu._Frame.draw=queued if method=='gpu_queued' else original
     t=time.perf_counter();context=gpu.GpuContext.create()
     row['context_ms']=(time.perf_counter()-t)*1000
     row['adapter']=context.info
     ss=PREVIEW_PROFILE.supersample_for(640)
     t=time.perf_counter();frame=gpu.GpuFrame(context,640*ss,480*ss,64000,radius,allocation_limit_bytes=256*1024**2)
     row['frame_setup_ms']=(time.perf_counter()-t)*1000
    t=time.perf_counter()
    pixels=render_prepared_pixels(prepared,name,640,480,rasterise_triangles=frame)
    row['render_postprocess_ms']=(time.perf_counter()-t)*1000
    if pixels is None:raise RuntimeError(str(frame.failure) if frame else 'CPU render failed')
    pixels_by_method[method]=np.frombuffer(pixels.rgba,dtype=np.uint8)
    t=time.perf_counter();encoded=encode_rendered_pixels(pixels,output_format='WEBP')
    row['encode_ms']=(time.perf_counter()-t)*1000
    row['rgba_sha256']=hashlib.sha256(pixels.rgba).hexdigest()
    row['encoded_bytes']=len(encoded)
    if frame:row['gpu_phases']=asdict(frame.stats)
    row['status']='completed'
   except Exception as exc:
    row['error']=repr(exc);row['traceback']=traceback.format_exc()
   finally:
    t=time.perf_counter()
    if frame:frame.close()
    if context:context.close()
    row['cleanup_ms']=(time.perf_counter()-t)*1000
    row['total_ms']=(time.perf_counter()-started)*1000
   rows.append(row)
   with (root/'samples.jsonl').open('a') as output:output.write(json.dumps(row)+'\n')
  quality={'case':name,'trial':trial}
  if len(pixels_by_method)==3:
   a,b=pixels_by_method['gpu_current'],pixels_by_method['gpu_queued']
   quality['queued_matches_current']=bool(np.array_equal(a,b))
   c=pixels_by_method['cpu'].reshape(-1,4);b=b.reshape(-1,4)
   quality['cpu_mask_differences']=int(np.count_nonzero((c[:,3]>=128)!=(b[:,3]>=128)))
   quality['cpu_max_channel_difference']=int(np.abs(c.astype(np.int16)-b.astype(np.int16)).max())
  with (root/'quality.jsonl').open('a') as output:output.write(json.dumps(quality)+'\n')
  print(name,trial,[r['status'] for r in rows[-3:]],flush=True)
gpu._Frame.draw=original
(root/'report.json').write_text(json.dumps({'manifest':manifest,'samples':rows,'production_qualified':False},indent=2))
