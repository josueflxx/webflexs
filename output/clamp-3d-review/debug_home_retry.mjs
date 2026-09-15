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
 // Download failure: preserve the image, allow a successful explicit retry.
 const failure=await context.newPage();
 failure.on('console',m=>console.log(m.type(),m.text()));
 failure.on('pageerror',m=>console.log('PAGEERROR',m.message));
 await failure.route('**/abrazadera-curva.glb*',route=>route.abort());
 await failure.goto(root,{waitUntil:'domcontentloaded'});
 await failure.getByRole('button',{name:'Reintentar vista 3D'}).waitFor({state:'visible',timeout:45000});
 assert.equal(await failure.locator('.home-clamp-poster').isVisible(),true);
 await failure.unroute('**/abrazadera-curva.glb*');
 await failure.getByRole('button',{name:'Reintentar vista 3D'}).click();
 try {await failure.waitForFunction(()=>document.getElementById('homeClamp').classList.contains('is-ready'));} catch(e) {console.log(await failure.locator('#homeClamp').evaluate(el=>el.outerHTML));throw e;}
 console.log('RETRY PASSED');
} finally {await browser.close();}
