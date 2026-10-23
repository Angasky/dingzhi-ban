// Real frontend + real local HTTP API. Remote server transport is simulated by Python fixture.
const puppeteer=require('puppeteer-core'),assert=require('assert'),path=require('path'),fs=require('fs');
(async()=>{
 const browser=await puppeteer.launch({executablePath:process.env.MXIOC_CHROMIUM_BIN||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  page.on('dialog',async dialog=>{errors.push(dialog.message());await dialog.dismiss();});
  await page.setViewport({width:1360,height:1000});
  await page.goto(process.env.MXIOC_TEST_URL+'/admin/',{waitUntil:'networkidle0'});
  await page.waitForSelector('#login:not(.hidden)');await page.type('#user','admin');await page.type('#pass','workflow-test-password');
  await page.click('#loginForm button');await page.waitForSelector('#app:not(.hidden)');
  const dir=process.env.MXIOC_SCREENSHOT_DIR||'/tmp/mxioc-e2e';fs.mkdirSync(dir,{recursive:true});
  for(const theme of ['high','low','pink']){
   await page.evaluate(async theme=>{setTheme(theme);await go('traffic');},theme);
   await page.$eval('#trafficMultiplier',n=>{n.value='2.5';});await page.click('#trafficEnforce');
   // Keep enforcement enabled when returning from the previous theme.
   await page.$eval('#trafficEnforce',n=>{n.checked=true;});
   const saved=page.waitForResponse(r=>r.url().endsWith('/admin/api/traffic')&&r.request().method()==='PUT');
   await page.click('#trafficSave');assert.equal((await saved).status(),200);
   await page.waitForFunction(()=>document.getElementById('trafficSave')&&!document.getElementById('trafficSave').disabled);
   assert.equal(await page.$eval('#trafficMultiplier',n=>n.value),'2.5');
   const button=await page.$('button[onclick^="nodeBandwidthModal"]');await button.click();
   await page.$eval('#bandwidthUpload',n=>{n.value='1.25';});await page.$eval('#bandwidthDownload',n=>{n.value='2.5';});
   const applied=page.waitForResponse(r=>r.url().endsWith('/admin/api/servers/bandwidth')&&r.request().method()==='PUT');
   await page.click('#bandwidthSave');assert.equal((await applied).status(),200);
   await page.waitForSelector('#modal.hidden');
   await page.waitForFunction(()=>document.getElementById('content').textContent.includes('1.25 Mbps'));
   await page.screenshot({path:path.join(dir,theme+'-live-traffic.png'),fullPage:true});
   await page.click('button[onclick^="nodeBandwidthModal"]');
   const cleared=page.waitForResponse(r=>r.url().endsWith('/admin/api/servers/bandwidth')&&r.request().method()==='PUT');
   await page.click('#bandwidthClear');assert.equal((await cleared).status(),200);await page.waitForSelector('#modal.hidden');
   await page.waitForFunction(()=>document.getElementById('content').textContent.includes('不限速'));
  }
  await page.reload({waitUntil:'networkidle0'});await page.waitForSelector('#app:not(.hidden)');
  await page.click('#logout');await page.waitForSelector('#login:not(.hidden)');
  assert.deepEqual(errors,[]);console.log('PASS real browser/API: cookie login, 3 theme quota edits, bandwidth apply/clear, reload session, logout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
