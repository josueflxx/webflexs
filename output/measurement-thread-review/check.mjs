import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire('C:/Users/Brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {chromium}=require('playwright');
const origin=process.env.GUIDE_ORIGIN || 'http://127.0.0.1:8779';
const tag=origin.includes('127.0.0.1')?'local':'public';
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce',deviceScaleFactor:2});
 await context.route('**/*',r=>r.request().url().startsWith(origin)?r.continue():r.abort());
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const response=await page.goto(origin+'/catalogo/como-medir/',{waitUntil:'domcontentloaded',timeout:60000});assert.equal(response.status(),200);
 for(const shape of ['curva','semicurva','plana']){
  await page.locator(`button[data-shape="${shape}"]`).click();
  await page.locator('#guideSvg').screenshot({path:`output/measurement-thread-review/${tag}-${shape}.png`});
 }
 // Crop the rendered lower assembly to inspect both threaded legs at high resolution.
 await page.locator('#guideSvg').scrollIntoViewIfNeeded();
 const clip=await page.locator('#guideSvg').evaluate(e=>{const m=e.getScreenCTM();const a=new DOMPoint(125,335).matrixTransform(m);const b=new DOMPoint(495,460).matrixTransform(m);return {x:a.x,y:a.y,width:b.x-a.x,height:b.y-a.y};});
 await page.screenshot({path:`output/measurement-thread-review/${tag}-detail.png`,clip});
 await page.setViewportSize({width:390,height:844});await page.locator('button[data-shape="curva"]').click();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.locator('#guideSvg').screenshot({path:`output/measurement-thread-review/${tag}-mobile.png`});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({status:200,profiles:3,mobile:true,errors}));
}finally{await browser.close();}
