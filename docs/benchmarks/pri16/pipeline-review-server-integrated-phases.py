import os
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[name]='1'
import gc,json,time
from pathlib import Path
import numpy as np
import trimesh,wgpu
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE
from printstash_core.mesh.render_geometry import prepare_mesh_render
from printstash_core.mesh.rasterizer import render_prepared_pixels
from scripts import wgpu_render_backend as gpu
prepared=prepare_mesh_render(trimesh.load_mesh('private-deployed-corpus/real-stl-medium.stl',process=False))
radius=float(np.linalg.norm(np.ptp(prepared.vertices,axis=0)))+1
rows=[]
for trial in range(3):
 row={'trial':trial}
 def create(selector,software):
  t=time.perf_counter();adapters=wgpu.gpu.enumerate_adapters_sync();row['enumerate_ms']=(time.perf_counter()-t)*1000
  t=time.perf_counter()
  adapter=next(a for a in adapters if gpu.is_hardware(a.info) and a.limits['max-color-attachment-bytes-per-sample']>=16 and a.limits['max-vertex-attributes']>=2)
  row['select_ms']=(time.perf_counter()-t)*1000
  t=time.perf_counter();device=adapter.request_device_sync(required_limits={'max-color-attachment-bytes-per-sample':16});row['device_ms']=(time.perf_counter()-t)*1000
  t=time.perf_counter();identity=dict(adapter.info);row['identity_ms']=(time.perf_counter()-t)*1000
  t=time.perf_counter();owner=gpu._Device(device,identity);row['wrapper_pipeline_ms']=(time.perf_counter()-t)*1000
  return owner
 render_prepared_pixels(prepared,'diagnostic',640,480)
 t=time.perf_counter();context=gpu.GpuContext.create(context_factory=create);row['context_ms']=(time.perf_counter()-t)*1000
 ss=PREVIEW_PROFILE.supersample_for(640);frame=gpu.GpuFrame(context,640*ss,480*ss,64000,radius)
 render_prepared_pixels(prepared,'diagnostic',640,480,rasterise_triangles=frame)
 frame.close();context.close()
 rows.append(row);print(json.dumps(row),flush=True)
Path('private-server-integrated-phases-d9511ac9.json').write_text(json.dumps(rows,indent=2))
