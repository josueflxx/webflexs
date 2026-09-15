import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire('C:/Users/Brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {chromium}=require('playwright');
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--enable-unsafe-swiftshader']});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1100},reducedMotion:'reduce'});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const response=await page.goto('https://flexsrepuestos.shop/',{waitUntil:'domcontentloaded'});
 await page.getByRole('button',{name:'Ver medidas',exact:true}).waitFor({state:'visible',timeout:60000});
 assert.ok((await page.locator('model-viewer').evaluate(e=>e.src)).includes('abrazadera-plaqueta-tuercas.glb'));
 await page.getByRole('button',{name:'Ver medidas',exact:true}).click();await page.waitForTimeout(1000);
 assert.equal(await page.locator('.clamp-dimension-label:visible').count(),4);
 const line=page.locator('.clamp-dimensions-lines > line').nth(2);const x=await line.getAttribute('x1');assert.notEqual(x,null);
 const model=page.locator('model-viewer');const b=await model.boundingBox();
 await page.mouse.move(b.x+b.width*.2,b.y+b.height*.3);await page.mouse.down();await page.mouse.move(b.x+b.width*.4,b.y+b.height*.4,{steps:15});await page.mouse.up();await page.waitForTimeout(600);
 assert.notEqual(await line.getAttribute('x1'),x);
 await model.evaluate(e=>e.cameraOrbit='0deg 90deg 140%');await page.waitForTimeout(600);
 await page.locator('#homeClamp').screenshot({path:'output/clamp-dimensions-review/production.png'});
 await page.getByRole('button',{name:'Ocultar medidas',exact:true}).click();assert.equal(await page.locator('.clamp-dimensions-overlay').isVisible(),false);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({publicStatus:response.status(),assemblyLoaded:true,dimensions:4,rotationAndToggle:true,errors}));
}finally{await browser.close();}
