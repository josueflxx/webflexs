import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire('C:/Users/Brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {chromium}=require('playwright');
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--enable-unsafe-swiftshader']});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
 await context.route('**/*',r=>r.request().url().startsWith('http://127.0.0.1:8775')?r.continue():r.abort());
 for(const [name,path] of [['home','/'],['catalog','/catalogo/abrazaderas-a-medida/']]){
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8775'+path,{waitUntil:'domcontentloaded'});
  if(name==='catalog')await page.locator('#clampShow3d').click();
  await page.getByRole('button',{name:'Ver medidas',exact:true}).waitFor({state:'visible',timeout:45000});
  await page.getByRole('button',{name:'Ver medidas',exact:true}).click();
  await page.waitForTimeout(900);
  const overlay=page.locator('.clamp-dimensions-overlay');
  assert.equal(await overlay.isVisible(),true);
  assert.equal(await page.locator('.clamp-dimension-label:visible').count(),4);
  const c=page.locator('.clamp-dimensions-lines > line').nth(2);
  const before=await c.getAttribute('x1');assert.notEqual(before,null);
  const model=page.locator('model-viewer');const box=await model.boundingBox();
  await page.mouse.move(box.x+box.width*.25,box.y+box.height*.3);await page.mouse.down();
  await page.mouse.move(box.x+box.width*.4,box.y+box.height*.45,{steps:15});await page.mouse.up();
  await page.waitForTimeout(600);
  assert.notEqual(await c.getAttribute('x1'),before,'dimensions follow rotation');
  await model.evaluate(e=>e.cameraOrbit='0deg 90deg 140%');await page.waitForTimeout(600);
  const card=page.locator(name==='home'?'#homeClamp':'.clamp-simulator-card');
  await card.screenshot({path:`output/clamp-dimensions-review/${name}-desktop.png`});
  await page.setViewportSize({width:390,height:844});await card.scrollIntoViewIfNeeded();await page.waitForTimeout(700);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'mobile overflow');
  await card.screenshot({path:`output/clamp-dimensions-review/${name}-mobile.png`});
  await page.getByRole('button',{name:'Ocultar medidas',exact:true}).click();
  assert.equal(await overlay.isVisible(),false);
  if(name==='catalog'){
   await page.locator('#clampShowDrawing').click();await page.locator('#width_mm').fill('155');
   assert.match(await page.locator('#clampCalculatedDesc').innerText(),/155/);
   await page.locator('#clampShow3d').click();
   assert.equal(await page.locator('.clamp-dimensions-toggle').count(),1);
  }
  assert.deepEqual(errors,[]);console.log(name+' dimensions: PASS');await page.close();
 }
}finally{await browser.close();}
