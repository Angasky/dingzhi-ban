// Real Chromium checks with mock data only; no production servers or account access.
const puppeteer=require('puppeteer-core');
const fs=require('fs'),assert=require('assert'),path=require('path'),os=require('os'),http=require('http');
const {spawnSync}=require('child_process');
const captureDir=process.env.MXIOC_SCREENSHOT_DIR || path.join(os.tmpdir(),'mxioc-theme-previews');
fs.mkdirSync(captureDir,{recursive:true});
const html=fs.readFileSync(path.resolve(__dirname,'..','index.html'),'utf8').replace('void bootstrap();','');
const metrics={serverId:'demo',name:'Singapore Edge',managementIp:'198.51.100.24',hostname:'sg-edge-01',os:'Debian GNU/Linux 12',kernel:'6.1.0',cpuModel:'AMD EPYC Virtual Processor',cores:4,load:[.54,.38,.31],cpuPercent:26.8,perCpu:[0,1,2,3].map((x)=>({name:'cpu'+x,percent:20+x*5})),memory:{total:8589934592,used:3027959808,available:5561974784,percent:35.2,swapTotal:2147483648,swapUsed:0},network:{rxBytes:82678120448,txBytes:146884400128,rxBytesPerSecond:1954856,txBytesPerSecond:3484425},interfaces:[{name:'eth0',rxBytes:82678120448,txBytes:146884400128,rxBytesPerSecond:1954856,txBytesPerSecond:3484425}],summaryInterfaces:['eth0'],addresses:[{interface:'eth0',address:'10.0.0.24',family:'inet'},{interface:'eth0',address:'2001:db8::24',family:'inet6'}],disks:[{mount:'/',filesystem:'ext4',total:85899345920,used:28346784153,available:57552561767,percent:33}],sampledAt:1792684800,uptime:1284285,stale:false,warmingUp:false,error:''};
(async()=>{
const browser=await puppeteer.launch({executablePath:process.env.MXIOC_CHROMIUM_BIN || '/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
let fixtureServer;
try{
 const qrCache=new Map();
 fixtureServer=http.createServer((req,res)=>{
  const route=new URL(req.url,'http://127.0.0.1');
  if(route.pathname==='/admin/api/qrcode'){
   const value=route.searchParams.get('url') || '';
   if(!qrCache.has(value)){
    const root=path.resolve(__dirname,'..'),local=path.join(root,'.venv','bin','python');
    const result=spawnSync(process.env.MXIOC_PYTHON_BIN || (fs.existsSync(local)?local:'python3'),['-c','import server,sys;sys.stdout.buffer.write(server.subscription_qr_svg(sys.argv[1]))',value],{cwd:root});
    if(result.status!==0){res.writeHead(500);res.end();return;}
    qrCache.set(value,result.stdout);
   }
   res.writeHead(200,{'Content-Type':'image/svg+xml'});res.end(qrCache.get(value));return;
  }
  res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(html);
 });
 await new Promise((resolve,reject)=>{fixtureServer.once('error',reject);fixtureServer.listen(0,'127.0.0.1',resolve);});
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.setViewport({width:1440,height:1100,deviceScaleFactor:1});await page.goto('http://127.0.0.1:'+fixtureServer.address().port+'/admin/',{waitUntil:'domcontentloaded'});
 await page.evaluate((metrics)=>{
  S.profiles=[{id:'clash',name:'示例订阅',url:'/sub/demo'}];S.profile='clash';
  S.data={nodes:[{index:0,name:'Singapore Reality',type:'vless',server:'198.51.100.24',port:443,config:{name:'Singapore Reality',type:'vless',server:'198.51.100.24',port:443,uuid:'22453845-ff1a-471d-8214-25952a9348a6',tls:true,servername:'www.microsoft.com','reality-opts':{'public-key':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA','short-id':'abcdef12'},'client-fingerprint':'chrome'}}],groups:[],rules:[],dnsPolicies:[],updated:'2026-10-22',profile:{name:'示例订阅',traffic:{configured:false,bindings:[]}}};
  api=async(path,opt)=>{if(path==='/servers')return {servers:[{id:'demo',name:'Singapore Edge',host:'198.51.100.24',installType:'sing-box',sshPort:22,sshUser:'root',fingerprint:'SHA256:'+'a'.repeat(43)}]};if(path.startsWith('/servers/metrics'))return metrics;if(path==='/snapshot')return S.data;if(path==='/failovers')return {failovers:[]};if(path==='/server-chains')return {chains:[]};if(path==='/conversions')return {formats:[{name:'电脑 v2rayN · Xray 主备配置',supported:1,unit:'主备组',available:true,note:'独立 JSON 订阅，按优先顺序切换并恢复切回；本地 SOCKS 10808。',url:'/convert/demo/v2rayn-ha'},{name:'安卓 v2rayNG 节点订阅',supported:3,unit:'节点',available:true,note:'流量与有效期通过订阅响应头提供；普通节点列表不含主备策略。',url:'/convert/demo/v2rayng'},{name:'小火箭主备 · 第一步：节点订阅',supported:2,unit:'节点',available:true,note:'先添加节点，再启用第二步策略配置。',url:'/convert/demo/shadowrocket-ha'},{name:'小火箭主备 · 第二步：策略配置',supported:1,unit:'主备组',available:true,note:'在配置页添加并启用此配置，不启用全局回退。',url:'/convert/demo/shadowrocket-config'}]};if(path==='/account')return {username:'admin'};if(path==='/backups')return {backups:[]};if(path==='/updates')return {available:false,currentSha:'abc123',checkedAt:metrics.sampledAt};if(path==='/raw')return {yaml:'proxies: []\nproxy-groups: []\nrules: []'};if(path.startsWith('/servers/inbounds'))return {inbounds:[],server:{id:'demo',name:'Singapore Edge',host:'198.51.100.24',installType:'sing-box'}};if(path==='/nodes/validate')return {config:JSON.parse(opt.body).config,uri:'vless://example'};throw Error('Unmocked '+path);};
  document.getElementById('boot').classList.add('hidden');document.getElementById('login').classList.add('hidden');document.getElementById('app').classList.remove('hidden');document.getElementById('currentProfileName').textContent='示例订阅';
  S.monitorId='demo';S.monitorHistory=new Map([['demo/auto',Array.from({length:30},(_,i)=>({time:metrics.sampledAt-150+i*5,rx:1200000+Math.sin(i/3)*700000+i*17000,tx:1800000+Math.cos(i/4)*900000+i*23000}))]]);
 },metrics);
 for(const theme of ['high','low','pink']){
  await page.evaluate(async theme=>{setTheme(theme);await go('monitoring');},theme);
  await page.waitForSelector('.monitor-grid');await page.evaluate(()=>stopMonitoring());await new Promise(r=>setTimeout(r,180));await page.screenshot({path:`${captureDir}/${theme}-monitoring.png`,fullPage:true});
  const style=await page.evaluate(()=>({body:getComputedStyle(document.body).backgroundColor,nav:getComputedStyle(document.querySelector('.nav .icon')).width,panel:getComputedStyle(document.querySelector('.panel')).backgroundColor}));
  console.log(theme,JSON.stringify(style));assert.equal(style.nav,'22px');
  if(theme==='pink')assert.equal(style.body,'rgb(251, 247, 244)');
  await page.evaluate(()=>nodeModal(0));
  await page.screenshot({path:`${captureDir}/${theme}-node.png`,fullPage:true});
  assert(await page.$('#nodePreview'));await page.evaluate(async()=>{await document.getElementById('nodePreview').onclick();});assert(await page.$('#nodePreviewResult:not(.hidden)'));await page.evaluate(()=>closeModal());
 }
 for (const theme of ['high','low','pink']) {
  for (const route of ['overview','profiles','servers','nodes','chains','groups','rules','dns','subscriptions','traffic','raw','system','settings','maintenance']) {
   await page.evaluate(async({theme,route})=>{setTheme(theme);await go(route);},{theme,route});
   await new Promise(resolve=>setTimeout(resolve,35));
   const content=await page.$eval('#content',n=>n.textContent);
   assert(!content.includes('页面加载失败'),theme+'/'+route+': '+content);
   if (theme==='pink') {
    const dark=await page.evaluate(()=>[...document.querySelectorAll('#content .btn,#content input:not([type=checkbox]),#content select,#content textarea,#content th')].filter(n=>{
     const color=getComputedStyle(n).backgroundColor.match(/rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)/);
     return color && (!color[4] || +color[4]>.5) && Math.max(+color[1],+color[2],+color[3])<70;
    }).map(n=>n.outerHTML.slice(0,100)));
    assert.equal(dark.length,0,'Dark component leaked into pink '+route+': '+dark.join(','));
   }
  }
 }
 console.log('PASS: 42 page/theme combinations; no dark component leaked into pink controls');
 await page.setViewport({width:1440,height:1100,deviceScaleFactor:1});
 await page.evaluate(()=>{
  const base=S.data.nodes[0];
  S.data.nodes=[base,{...base,index:1,name:'备用入口',config:{...base.config,name:'备用入口',server:'198.51.100.25'}},{...base,index:2,name:'固定出口',config:{...base.config,name:'固定出口',server:'198.51.100.26'}}];
 });
 for (const theme of ['high','low','pink']) {
  await page.evaluate(async theme=>{setTheme(theme);await go('chains');await chains();await failoverModal();},theme);
  assert.equal(await page.$$eval('.foEntry',x=>x.length),2);
  assert.equal(await page.$eval('#foExit',x=>x.value),'固定出口');
  await page.click('#foAdd');assert.equal(await page.$$eval('.foEntry',x=>x.length),3);
  await page.screenshot({path:path.join(captureDir,theme+'-failover.png'),fullPage:true});
  await page.evaluate(()=>closeModal());
 }
 console.log('PASS: prioritized multi-entry form in all three themes');
 for (const theme of ['high','low','pink']) {
  await page.evaluate(async theme=>{setTheme(theme);await profileClientLinks(encodeURIComponent('clash'));},theme);
  await page.waitForSelector('#clientConversionRows .subscription-row');
  await page.waitForFunction(()=>[...document.querySelectorAll('#clientConversionRows .subscription-qr')].every(img=>img.complete && img.naturalWidth>0));
  assert(!await page.$eval('#clientConversionRows',n=>n.textContent.includes('null/convert')));
  assert.equal(await page.$$eval('#clientConversionRows .subscription-row',x=>x.length),4);
  await page.screenshot({path:path.join(captureDir,theme+'-client-subscriptions.png'),fullPage:true});
  await page.evaluate(()=>closeModal());
 }
 console.log('PASS: desktop, Android and Shadowrocket client links in all three themes');
 await page.setViewport({width:390,height:844,deviceScaleFactor:1});
 for (const theme of ['high','low','pink']) {
  await page.evaluate(async theme=>{setTheme(theme);await go('monitoring');},theme);
  await page.waitForSelector('.monitor-grid');await page.evaluate(()=>stopMonitoring());
  await page.screenshot({path:path.join(captureDir,theme+'-mobile.png'),fullPage:true});
  const overflow=await page.evaluate(()=>({width:window.innerWidth,scroll:document.documentElement.scrollWidth}));
  assert(overflow.scroll<=overflow.width,'Unexpected mobile page overflow: '+theme);
  await page.evaluate(()=>nodeModal(0));
  const bounds=await page.$eval('.modalbox',n=>({width:n.getBoundingClientRect().width,screen:window.innerWidth}));
  assert(bounds.width<bounds.screen,'Mobile dialog too wide: '+theme);
  await page.evaluate(()=>closeModal());
 }
 console.log('PASS: all three mobile themes and node dialogs fit 390px viewport');
 await page.evaluate(async()=>{await go('settings');});
 assert.equal(await page.$$eval('[data-theme-choice]',x=>x.length),3);
 console.log('page errors',errors);assert.equal(errors.length,0);
}finally{await browser.close();if(fixtureServer)await new Promise(resolve=>fixtureServer.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
