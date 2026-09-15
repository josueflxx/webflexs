import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire('C:/Users/Brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {chromium}=require('playwright');
const origin=process.env.GUIDE_ORIGIN || 'http://127.0.0.1:8778';
const suffix=origin.includes('127.0.0.1')?'local':'public';
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce'});
 await context.route('**/*',r=>r.request().url().startsWith(origin)?r.continue():r.abort());
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const response=await page.goto(origin+'/catalogo/como-medir/',{waitUntil:'domcontentloaded',timeout:90000});assert.equal(response.status(),200);
 await page.locator('.mg-profiles').waitFor({state:'visible'});
 const drawing=page.locator('#lengthLine');const paths=new Set();
 for(const shape of ['curva','semicurva','plana','curva']){
  await page.locator(`button[data-shape="${shape}"]`).click();
  assert.equal(await page.locator('button[data-shape][aria-pressed="true"]').count(),1);
  const coords=await drawing.evaluate(e=>['x1','y1','x2','y2'].map(a=>Number(e.getAttribute(a))));
  assert.deepEqual(coords,shape==='plana'?[100,450,100,93]:[185,450,260,93]);
  const body=await page.locator('#uBoltBody').getAttribute('d');paths.add(body);
  if(shape==='semicurva')assert.ok(body.includes('A86 57'),'elliptical shallow crown');
  assert.equal(await page.locator('#lengthWitnesses').getAttribute('visibility'),shape==='plana'?'visible':'hidden');
  assert.match(await page.locator('#mgLengthInstruction').innerText(),shape==='plana'?/en vertical/:/línea inclinada/);
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({fullPage:true,path:`output/measurement-guide-review/${suffix}-${shape}.png`});
 }
 assert.equal(paths.size,3,'each profile has a distinct drawing');
 for(const measure of ['a','b','c','d']){
  await page.locator(`.mg-step[data-focus="${measure}"]`).click();
  assert.equal(await page.locator('.mg-dimension:not(.is-muted)').count(),1);
  assert.equal(await page.locator('.mg-dimension:not(.is-muted)').getAttribute('data-dimension'),measure);
  assert.equal(await page.locator('#mgTipLetter').innerText(),measure.toUpperCase());
 }
 await page.locator('.mg-measures [data-focus="all"]').click();
 assert.equal(await page.locator('.mg-dimension.is-muted').count(),0);
 // Native buttons remain operable by keyboard; selecting a profile preserves focus mode.
 await page.locator('.mg-measures [data-focus="c"]').focus();await page.keyboard.press('Enter');
 await page.locator('[data-shape="semicurva"]').filter({has:page.locator('svg')}).focus();await page.keyboard.press('Space');
 assert.equal(await page.locator('#measurementGuide').getAttribute('data-shape'),'semicurva');
 assert.equal(await page.locator('#measurementGuide').getAttribute('data-measure'),'c');
 assert.match(await page.locator('#mgTipTitle').innerText(),/semicurva/);
 for(const width of [768,390,320]){
  await page.setViewportSize({width,height:900});await page.locator('.mg-board').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${width}px overflow`);
  const boxes=await page.locator('.mg-profiles button,.mg-measures button').evaluateAll(elements=>elements.map(e=>({w:e.getBoundingClientRect().width,h:e.getBoundingClientRect().height})));
  assert.ok(boxes.every(b=>b.w>=40&&b.h>=40),'touch controls');
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({fullPage:true,path:`output/measurement-guide-review/${suffix}-${width}.png`});
 }
 await page.setViewportSize({width:1440,height:1100});await page.evaluate(()=>document.documentElement.dataset.theme='light');
 await page.locator('.mg-measures [data-focus="all"]').click();
 await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({fullPage:true,path:`output/measurement-guide-review/${suffix}-light.png`});
 assert.equal(await page.locator('.mg-equivalence-grid > div').count(),7);
 assert.ok(!(await page.locator('#measurementGuide').innerText()).includes('**'),'no raw markdown');
 assert.ok((await page.locator('.mg-cta a').getAttribute('href')).includes('abrazaderas-a-medida'));
 assert.deepEqual(errors,[]);await context.close();
 const fallback=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:844}});
 await fallback.route('**/*',r=>r.request().url().startsWith(origin)?r.continue():r.abort());
 const staticPage=await fallback.newPage();await staticPage.goto(origin+'/catalogo/como-medir/',{waitUntil:'domcontentloaded',timeout:90000});
 assert.ok((await staticPage.locator('#uBoltBody').getAttribute('d')).length>10);
 assert.equal(await staticPage.locator('.mg-dimension').count(),4);
 assert.equal(await staticPage.locator('.mg-profiles').isVisible(),false);
 await fallback.close();
 console.log(JSON.stringify({status:200,profiles:3,correctCEndpoints:true,measurementFocus:true,keyboard:true,mobile:[768,390,320],lightTheme:true,noJsFallback:true,errors}));
}finally{await browser.close();}
