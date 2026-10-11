import gc,json,time
from pathlib import Path
import wgpu
from scripts.wgpu_render_backend import _Device,is_hardware
root=Path('private-server-device-phases-db41b45d');root.mkdir()
for trial in range(30):
 for method in (['enumerate','request'] if trial%2==0 else ['request','enumerate']):
  row={'trial':trial,'method':method,'status':'failed'};owner=device=None
  try:
   t=time.perf_counter()
   if method=='enumerate':
    adapters=wgpu.gpu.enumerate_adapters_sync()
    adapter=next(a for a in adapters if is_hardware(a.info) and a.limits['max-color-attachment-bytes-per-sample']>=16 and a.limits['max-vertex-attributes']>=2)
   else:
    adapter=wgpu.gpu.request_adapter_sync(power_preference='high-performance')
    if not is_hardware(adapter.info):raise RuntimeError('not hardware')
   row['adapter_ms']=(time.perf_counter()-t)*1000;row['adapter']=dict(adapter.info)
   t=time.perf_counter();device=adapter.request_device_sync(required_limits={'max-color-attachment-bytes-per-sample':16})
   row['device_ms']=(time.perf_counter()-t)*1000
   t=time.perf_counter();owner=_Device(device,dict(adapter.info))
   row['pipeline_ms']=(time.perf_counter()-t)*1000;row['status']='completed'
  except Exception as exc:row['error']=repr(exc)
  finally:
   t=time.perf_counter()
   if owner:owner.close()
   elif device:device.destroy()
   row['cleanup_ms']=(time.perf_counter()-t)*1000
   device=owner=None;gc.collect()
  with (root/'samples.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
 print(trial,flush=True)
