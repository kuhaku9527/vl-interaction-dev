#!/usr/bin/env node
/**
 * 验证 iDesign 镜像（design 下 session 目录里的 index.html，内联后的单文件）里
 * 两个修复是否真实生效 —— 直接渲染镜像本身，不渲染源目录。
 *
 * 这填补一个盲区：此前所有验收都跑在「源 static/ 目录」上，
 * 而 Studio 实际渲染的是**镜像**。两者不同步时，用户看到的仍是旧版。
 * （2026-09-19 实测教训：用户反馈"改了但看不到"，根因正是漏了同步这一步。）
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10070, PORT=8130, OUT='scripts/.fs-inspect';
const DIR = fs.readdirSync('design').filter(d=>d.startsWith('session-'))[0];
if (!DIR) { console.error('❌ 未找到 design/session-* 镜像'); process.exit(2); }
const MIRROR = path.join('design', DIR);
console.log(`镜像: ${MIRROR}`);
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'mirror-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT,{recursive:true});
const srv=spawn('services/.venv/Scripts/python.exe',['-m','http.server',String(PORT),'--directory',MIRROR,'--bind','127.0.0.1'],{stdio:'ignore'});
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{srv.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
let pass=0,fail=0;
const check=(c,l,d='')=>{console.log(`  ${c?'✅':'❌'} ${l}${d?'  '+d:''}`);c?pass++:fail++;};

const TOOL = `(()=>{const g=s=>{const e=document.querySelector(s);if(!e)return null;const c=getComputedStyle(e);
  return {bg:c.backgroundColor,op:c.opacity,bd:c.borderColor};};
  return JSON.stringify({theme:document.body.classList.contains('light-theme')?'light':'dark',
    mode:document.getElementById('videoCard').classList.contains('fullscreen')?'fs':'normal',
    fs:g('.fullscreen-btn'), cam:g('#quickCameraBtn')});})()`;
const BAR = `(()=>{const t=document.getElementById('promptText'); if(!t) return JSON.stringify({txt:null});
  return JSON.stringify({txt:Math.round(t.getBoundingClientRect().width),
    bar:Math.round(document.getElementById('promptEditor').getBoundingClientRect().width)});})()`;

const main=async()=>{
  await new Promise(r=>setTimeout(r,1200));
  for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
  await new Promise(r=>setTimeout(r,6000));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
    return r.exceptionDetails?'EXC '+(r.exceptionDetails.exception?.description||'').split('\n')[0]:r.result.value;};
  await ev('window.alert=function(){}');
  const loaded = await ev(`!!document.getElementById('videoCard')`);
  check(loaded, '镜像页面可渲染（关键 DOM 就位）');

  console.log('\n【(a) 全屏按钮 vs 后置 —— 镜像实测】');
  for (const th of ['dark','light']) {
    await ev(`document.body.classList.toggle('light-theme', ${th==='light'})`);
    await new Promise(r=>setTimeout(r,250));
    for (const fs_ of [false,true]) {
      const isFs = await ev(`document.getElementById('videoCard').classList.contains('fullscreen')`);
      if (isFs!==fs_) { await ev(`document.querySelector('.fullscreen-btn').click()`); await new Promise(r=>setTimeout(r,600)); }
      const d=JSON.parse(await ev(TOOL));
      const same = d.fs.bg===d.cam.bg && d.fs.op===d.cam.op;
      check(same, `[${d.theme}/${d.mode}] 全屏与后置配色一致`,
        `fs bg=${d.fs.bg} op=${d.fs.op} | cam bg=${d.cam.bg} op=${d.cam.op}`);
      if (fs_) { await ev(`document.querySelector('.fullscreen-btn').click()`); await new Promise(r=>setTimeout(r,600)); }
    }
  }
  await ev(`document.body.classList.remove('light-theme')`);

  console.log('\n【(b) 输入栏 —— 镜像实测（多宽度）】');
  const row=[];
  for (const w of [1920,1440,1280,1200,1128,1024,980,900,768,600,420,360]) {
    await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:w,height:900,deviceScaleFactor:1,mobile:w<=768});
    await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
    await new Promise(r=>setTimeout(r,3200));
    await ev('window.alert=function(){}');
    const d=JSON.parse(await ev(BAR));
    row.push(`${w}:${d.txt}`);
    if (d.txt===null) check(false, `${w}px 输入框存在`);
  }
  console.log('    ' + row.join('  '));
  const bad = row.filter(s=>{const n=parseInt(s.split(':')[1]); return isNaN(n)||n<100;});
  check(bad.length===0, '所有宽度输入框 ≥100px（可用）', bad.length? '不足: '+bad.join(', ') : '全部达标');

  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1128,height:800,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
  await new Promise(r=>setTimeout(r,3200));
  const r=await cdp(ws,'Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(path.join(OUT,'mirror-1128.png'), Buffer.from(r.data,'base64'));
  console.log(`    截图: scripts/.fs-inspect/mirror-1128.png`);

  ws.close(); cleanup();
  console.log(`\n${'='.repeat(52)}\n通过 ${pass} / 失败 ${fail}`);
  process.exit(fail?1:0);
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
