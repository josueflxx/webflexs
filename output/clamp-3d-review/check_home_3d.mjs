import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
const require = createRequire('C:/Users/Brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const { chromium } = require('playwright');
const root = 'http://127.0.0.1:8773';
for(let n=0;n<90;n++){try{await fetch(root);break;}catch{await new Promise(r=>setTimeout(r,1000));}}
const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--enable-unsafe-swiftshader']});
try {
 const context=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
 await context.route('**/*', route=>route.request().url().startsWith(root)?route.continue():route.abort());
 const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(root,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.getElementById('homeClamp')?.classList.contains('is-ready'),null,{timeout:45000});
 const model=page.locator('#homeClamp model-viewer');
 const initial=await model.evaluate(el=>({...el.getCameraOrbit()}));
 const box=await model.boundingBox();
 await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);
 await page.mouse.down();await page.mouse.move(box.x+box.width*.7,box.y+box.height*.6,{steps:12});await page.mouse.up();
 await page.waitForTimeout(600);
 const rotated=await model.evaluate(el=>({...el.getCameraOrbit()}));
 assert.ok(Math.abs(rotated.theta-initial.theta)>.1);
 await page.getByRole('button',{name:'Acercar',exact:true}).click();await page.waitForTimeout(600);
 assert.ok((await model.evaluate(el=>el.getCameraOrbit().radius))<initial.radius);
 await page.getByRole('button',{name:'Restablecer vista',exact:true}).click();await page.waitForTimeout(800);
 const materials=await model.evaluate(el=>el.model.materials.map(m=>({metal:m.pbrMetallicRoughness.metallicFactor,roughness:m.pbrMetallicRoughness.roughnessFactor})));
 assert.equal(materials[0].metal,1);assert.equal(materials[0].roughness,.24);
 await page.screenshot({path:'output/home-clamp-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await page.locator('#homeClamp').scrollIntoViewIfNeeded();await page.waitForTimeout(400);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.locator('#homeClamp').screenshot({path:'output/home-clamp-mobile.png'});
 await page.evaluate(()=>document.documentElement.dataset.theme='light');
 await page.locator('#homeClamp').screenshot({path:'output/home-clamp-light.png'});
 assert.deepEqual(errors,[]);
 // Download failure: preserve the image, allow a successful explicit retry.
 const failure=await context.newPage();
 await failure.route('**/abrazadera-curva.glb*',route=>route.abort());
 await failure.goto(root,{waitUntil:'domcontentloaded'});
 await failure.getByRole('button',{name:'Reintentar vista 3D'}).waitFor({state:'visible',timeout:45000});
 assert.equal(await failure.locator('.home-clamp-poster').isVisible(),true);
 await failure.unroute('**/abrazadera-curva.glb*');
 await failure.getByRole('button',{name:'Reintentar vista 3D'}).click();
 await failure.waitForFunction(()=>document.getElementById('homeClamp').classList.contains('is-ready'));
 console.log(JSON.stringify({passed:true,materials,rotation:rotated.theta-initial.theta,errors}));
} finally {await browser.close();}
