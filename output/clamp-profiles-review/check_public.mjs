import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire('C:/Users/Brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {chromium}=require('playwright');
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--enable-unsafe-swiftshader']});
const files={plana:'abrazadera-plaqueta-tuercas.glb',curva:'abrazadera-curva-plaqueta-tuercas.glb',semicurva:'abrazadera-semicurva-plaqueta-tuercas.glb'};
try{
 const context=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
 await context.route('**/*',r=>r.request().url().startsWith('https://flexsrepuestos.shop')?r.continue():r.abort());
 for(const [name,path] of [['home','/']]){
  const page=await context.newPage();const errors=[],downloads=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('.glb'))downloads.push(r.url());});
  await page.goto('https://flexsrepuestos.shop'+path,{waitUntil:'domcontentloaded',timeout:90000});
  if(name==='catalog') {assert.equal(downloads.length,0);await page.locator('#clampShow3d').click();}
  await page.locator('.clamp-dimensions-toggle').waitFor({state:'visible',timeout:60000});
  assert.equal(downloads.length,1,'only selected model loads');
  await page.locator('.clamp-dimensions-toggle').click();
  async function select(profile){
   if(name==='home')await page.locator(`[data-profile="${profile}"]`).click();
   else await page.locator('#profile_type').selectOption(profile.toUpperCase(),{force:true});
   await page.waitForFunction(file=>document.querySelector('model-viewer')?.loaded && document.querySelector('model-viewer').src.endsWith(file) && document.querySelector('.clamp-dimensions-toggle'),files[profile],{timeout:45000});
  }
  for(const profile of ['plana','curva','semicurva']){
   await select(profile);await page.waitForTimeout(700);
   assert.equal(await page.locator('.clamp-dimensions-toggle').count(),1);
   assert.equal(await page.locator('.clamp-dimension-label:visible').count(),4);
   const model=page.locator('model-viewer');
   const anchors=await model.evaluate(e=>['c0','c1'].map(k=>e.querySelector(`[slot="hotspot-dimension-${k}"]`).dataset.position));
   assert.equal(anchors[0],profile==='plana'?'-0.08m 0m 0.008m':'-0.03865m 0m 0.008m');
   assert.equal(anchors[1],profile==='plana'?'-0.08m 0.25365m 0.008m':'0m 0.25365m 0.008m');
   const c=page.locator('line[data-measure="c"]');const before=await c.getAttribute('x1');assert.notEqual(before,null);
   const box=await model.boundingBox();await page.mouse.move(box.x+box.width*.25,box.y+box.height*.3);await page.mouse.down();
   await page.mouse.move(box.x+box.width*.4,box.y+box.height*.45,{steps:15});await page.mouse.up();await page.waitForTimeout(500);
   assert.notEqual(await c.getAttribute('x1'),before,'annotations follow rotation');
   await model.evaluate(e=>e.cameraOrbit='0deg 90deg 140%');await page.waitForTimeout(700);
   await page.locator(name==='home'?'#homeClamp':'.clamp-simulator-card').screenshot({path:`output/clamp-profiles-review/production-${name}-${profile}.png`});
  }
  if(name==='home') await page.evaluate(()=>{for(const p of ['plana','curva','semicurva','curva'])document.querySelector(`[data-profile="${p}"]`).click();});
  else await page.evaluate(()=>{for(const p of ['PLANA','CURVA','SEMICURVA','CURVA']){const el=document.querySelector('#profile_type');el.value=p;el.dispatchEvent(new Event('change',{bubbles:true}));}});
  await page.waitForFunction(()=>document.querySelector('model-viewer')?.loaded && document.querySelector('model-viewer').src.endsWith('abrazadera-curva-plaqueta-tuercas.glb') && document.querySelector('.clamp-dimensions-toggle'));
  assert.equal(await page.locator('model-viewer').count(),1);assert.equal(await page.locator('.clamp-dimensions-overlay').count(),1);
  assert.equal(downloads.length,3,'previously loaded profiles reuse cache');
  await page.setViewportSize({width:390,height:844});await page.locator('model-viewer').scrollIntoViewIfNeeded();await page.waitForTimeout(700);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'mobile overflow');
  await page.locator(name==='home'?'#homeClamp':'.clamp-simulator-card').screenshot({path:`output/clamp-profiles-review/production-${name}-mobile.png`});
  assert.deepEqual(errors,[]);console.log(name+': three profiles, C endpoints, rotation, caching, rapid switching and mobile PASS');await page.close();
 }
}finally{await browser.close();}
