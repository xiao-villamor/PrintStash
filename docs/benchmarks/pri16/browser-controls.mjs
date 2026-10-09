import {chromium} from './playwright-core/index.mjs';
import fs from 'node:fs/promises';
const output='browser-controls-42bb9668';
await fs.mkdir(output);
const manifest={baseline_commit:'8d70c556',candidate_commit:'42bb966836b04d4f35ca1ddeae3eca531b6d5706',fixture_sha256:'f44a886e5386a2ca02413e37d3e1bf5704c033e48153f39b1cda93b3bf7fc65b',observations_per_comparison:30,viewport:{width:900,height:700},canvas:{width:640,height:480},device_scale_factor:1,interaction_frames:120,workloads:['box','large-torus-262144-faces'],baseline_three:'0.182.0',candidate_three:'0.186.1',scope:'Development comparison: fresh browser contexts, warm Vite servers, synthetic STL; readiness plus two animation frames is a proxy, not measured first visible paint. No production qualification.',required_p95_improvement:0.20,maximum_ready_regression:0.10};
await fs.writeFile(output+'/manifest.json',JSON.stringify(manifest,null,2),{flag:'wx'});
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const rows=[];
try {
 for(const workload of manifest.workloads) {
  for(let trial=0;trial<30;trial++) {
   for(const variant of trial%2===0?['baseline','candidate']:['candidate','baseline']) {
    const context=await browser.newContext({viewport:manifest.viewport,deviceScaleFactor:1});
    const row={workload,trial,variant,status:'failed'};
    try {
     const page=await context.newPage();
     await page.addInitScript(()=>{
      new MutationObserver((_,observer)=>{
       if(document.body?.dataset.ready==='true') {
        observer.disconnect();
        requestAnimationFrame(()=>requestAnimationFrame(()=>{window.benchmarkReadyAt=performance.now()}));
       }
      }).observe(document,{subtree:true,attributes:true,attributeFilter:['data-ready']});
     });
     const port=variant==='baseline'?3291:3290;
     await page.goto(`http://localhost:${port}/tests/browser-fixtures/mesh-benchmark.html?backend=webgpu${workload==='box'?'':'&large=1'}`);
     await page.waitForFunction(()=>Number.isFinite(window.benchmarkReadyAt),{},{timeout:60000});
     Object.assign(row,await page.evaluate(()=>({ready_after_two_frames_ms:window.benchmarkReadyAt-Number(document.body.dataset.startedAt),source_sha256:document.body.dataset.sourceHash,backend:document.querySelector('[data-mesh-backend]')?.getAttribute('data-mesh-backend')??'legacy-webgl'})));
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
     const download=page.waitForEvent('download');
     row.screenshot_ms=await page.evaluate(async()=>{const start=performance.now();await window.meshViewerCheck.screenshot();return performance.now()-start});
     const bytes=await fs.readFile(await(await download).path());
     row.screenshot_dimensions=[bytes.readUInt32BE(16),bytes.readUInt32BE(20)];
     if(row.screenshot_dimensions.join('x')!=='1280x960')throw new Error('invalid_screenshot_dimensions');
     row.status='completed';
     await page.evaluate(()=>window.meshViewerCheck.close());
    } catch(error){row.error=String(error)}
    finally {await context.close()}
    rows.push(row);await fs.appendFile(output+'/samples.jsonl',JSON.stringify(row)+'\n');
    console.log(workload,trial,variant,row.status);
   }
  }
 }
 await fs.writeFile(output+'/report.json',JSON.stringify({manifest,browser:browser.version(),samples:rows,production_qualified:false},null,2));
} finally {await browser.close()}
