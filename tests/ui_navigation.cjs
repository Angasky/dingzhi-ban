// Isolated browser fixture: no production credentials or server requests.
const puppeteer=require('puppeteer-core'),fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
(async()=>{
 const browser=await puppeteer.launch({executablePath:process.env.MXIOC_CHROMIUM_BIN||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 const html=fs.readFileSync(path.resolve(__dirname,'..','index.html'),'utf8').replace('void bootstrap();','');
 const server=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try {
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.setViewport({width:1360,height:900});await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.evaluate(()=>{
   document.getElementById('boot').classList.add('hidden');document.getElementById('login').classList.add('hidden');document.getElementById('app').classList.remove('hidden');
   S.profiles=[{id:'clash',name:'Test'}];S.data={nodes:[],groups:[],rules:[],profile:{name:'Test'}};
   S.servers=[{id:'s1',name:'Cached server',host:'198.51.100.1',sshUser:'root',sshPort:22,installType:'xray'}];
   api=async(url)=>{if(url==='/servers'){await new Promise(r=>setTimeout(r,1000));return {servers:S.servers};}throw Error(url);};
  });
  assert.equal(await page.$$eval('.nav',items=>items.length),5);
  await page.click('.nav[data-page="servers"]');
  await page.waitForFunction(()=>document.getElementById('content').textContent.includes('Cached server'),{timeout:600});
  assert.equal(await page.$eval('.nav[aria-current]',el=>el.dataset.page),'servers');
  await page.click('.nav[data-page="nodes"]');
  assert.equal(await page.$$eval('#sectionTabs button',items=>items.length),6);
  await page.evaluate(()=>go('dns'));
  assert.equal(await page.$eval('.nav[aria-current]',el=>el.dataset.page),'nodes');
  await page.evaluate(()=>{S.data=null;return go('settings');});
  assert.equal(await page.$$eval('#sectionTabs button',items=>items.length),3);
  assert(await page.$('button[onclick*="system"]'));
  for(const width of [1360,390]){await page.setViewport({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
  await page.screenshot({path:path.resolve(__dirname,'..','work','navigation-simplified.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: five main entries, nested tabs, active parent, settings without snapshot, cached servers visible before 1s API reply, mobile layout');
 } finally {await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
