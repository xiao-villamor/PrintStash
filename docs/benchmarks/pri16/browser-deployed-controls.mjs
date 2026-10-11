// Replay the archived development comparison; set CHROME_PATH and PLAYWRIGHT_MODULE.

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright-core');
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import http from 'node:http';
const output='private-browser-performance-72c66550';
fs.mkdirSync(output);
const root=path.resolve('private-deployed-corpus');
const workloads=['real-stl-small','real-stl-medium','real-stl-large','real-3mf-small','real-3mf-medium','real-3mf-large'];
const hashes=Object.fromEntries(workloads.map(name=>[name,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,name+'.stl'))).digest('hex')]));
const token=crypto.randomBytes(24).toString('hex');
const server=http.createServer((req,res)=>{
 const matched=workloads.find(name=>req.url==='/'+token+'/'+name);
 const origin=req.headers.origin;
 if(!matched||!['http://localhost:3291','http://localhost:3292'].includes(origin)){res.writeHead(404);res.end();return;}
 res.writeHead(200,{'Content-Type':'model/stl','Access-Control-Allow-Origin':origin});
 fs.createReadStream(path.join(root,matched+'.stl')).pipe(res);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const filePort=server.address().port;
const manifest={baseline_commit:'8d70c556',candidate_commit:'72c66550602fc36ef2fa206bf1bfca8b30f9ca1b',observations_per_comparison:30,viewport:{width:900,height:700},canvas:{width:640,height:480},device_scale_factor:1,interaction_frames:120,workloads,source_hashes:hashes,baseline_three:'0.182.0',candidate_three:'0.186.1',scope:'Development comparison using private deployed model bytes: fresh browser contexts, warm Vite servers, readiness plus two animation frames is a proxy, not measured first visible paint. 3MF sources use offline application viewer-STL conversion. No production qualification.',required_p95_improvement:0.20,maximum_ready_regression:0.10};
fs.writeFileSync(output+'/manifest.json',JSON.stringify(manifest,null,2),{flag:'wx'});
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
const rows=[];
try {
 for(const workload of workloads) {
  for(let trial=-1;trial<30;trial++) {
   for(const variant of trial%2===0?['baseline','candidate']:['candidate','baseline']) {
    const context=await browser.newContext({viewport:manifest.viewport,deviceScaleFactor:1});
    const row={workload,trial,warmup:trial<0,variant,status:'failed'};
    try {
     const page=await context.newPage();
     await page.route(/\/tests\/browser-fixtures\/mesh-benchmark\.tsx(?:\?|$)/,async route=>{
      const response=await route.fetch();
      const text=await response.text();
      const marker='const source = new Blob([bytes]);';
      if(!text.includes(marker)) throw new Error('fixture_source_injection_not_found');
      row.fixture_injected=true;
      const url='http://127.0.0.1:'+filePort+'/'+token+'/'+workload;
      await route.fulfill({response,body:text.replace(marker,'const downloadStarted = performance.now(); const source = await (await fetch('+JSON.stringify(url)+')).blob(); window.sourceDownloadMs = performance.now()-downloadStarted;')});
     });
     await page.addInitScript(()=>{
      new MutationObserver((_,observer)=>{
       if(document.body?.dataset.ready==='true') {
        observer.disconnect();
        requestAnimationFrame(()=>requestAnimationFrame(()=>{window.benchmarkReadyAt=performance.now()}));
       }
      }).observe(document,{subtree:true,attributes:true,attributeFilter:['data-ready']});
     });
     const port=variant==='baseline'?3291:3292;
     await page.goto('http://localhost:'+port+'/tests/browser-fixtures/mesh-benchmark.html?backend=webgpu');
     await page.waitForFunction(()=>Number.isFinite(window.benchmarkReadyAt),{},{timeout:120000});
     Object.assign(row,await page.evaluate(()=>({ready_after_two_frames_ms:window.benchmarkReadyAt-Number(document.body.dataset.startedAt),source_download_ms:window.sourceDownloadMs,source_sha256:document.body.dataset.sourceHash,backend:document.querySelector('[data-mesh-backend]')?.getAttribute('data-mesh-backend')??'legacy-webgl'})));
     if(!row.fixture_injected||row.source_sha256!==hashes[workload])throw new Error('wrong_source');
     if(variant==='candidate' && row.backend!=='webgpu') throw new Error('candidate_not_webgpu');
     row.frame_intervals_ms=await page.evaluate(()=>new Promise(resolve=>{
      const intervals=[];let previous=null;let frame=0;
      const tick=now=>{
       if(previous!==null) intervals.push(now-previous);
       previous=now;
       if(frame++===120){resolve(intervals);return;}
       if(frame%2===0)window.meshViewerCheck.fit();else window.meshViewerCheck.zoom();
       requestAnimationFrame(tick);
      };requestAnimationFrame(tick);
     }));
     const download=page.waitForEvent('download',{timeout:60000});
     row.screenshot_ms=await page.evaluate(async()=>{const start=performance.now();await window.meshViewerCheck.screenshot();return performance.now()-start});
     const bytes=fs.readFileSync(await(await download).path());
     row.screenshot_dimensions=[bytes.readUInt32BE(16),bytes.readUInt32BE(20)];
     if(row.screenshot_dimensions.join('x')!=='1280x960')throw new Error('invalid_screenshot_dimensions');
     row.status='completed';
     await page.evaluate(()=>window.meshViewerCheck.close());
    } catch(error){row.error=String(error)}
    finally {await context.close()}
    rows.push(row);fs.appendFileSync(output+'/samples.jsonl',JSON.stringify(row)+'\n');
    console.log(workload,trial,variant,row.status,row.error??'');
   }
  }
 }
 fs.writeFileSync(output+'/report.json',JSON.stringify({manifest,browser:browser.version(),samples:rows,production_qualified:false},null,2));
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}

