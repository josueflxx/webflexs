import { createRequire } from 'node:module';
const require = createRequire('C:/Users/Brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {chromium}=require('playwright');
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--enable-unsafe-swiftshader']});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const response=await page.goto('https://flexsrepuestos.shop/',{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.querySelector('#homeClamp')?.classList.contains('is-ready'),null,{timeout:60000});
 await page.screenshot({path:'output/home-clamp-production.png'});
 console.log(JSON.stringify({status:response.status(),model:await page.locator('model-viewer').evaluate(el=>({loaded:el.loaded,src:el.src,environment:el.environmentImage})),errors}));
}finally{await browser.close();}
