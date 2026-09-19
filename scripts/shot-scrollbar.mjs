#!/usr/bin/env node
/** 截图：输入框滚动条在深/浅主题下的实际观感 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10153, OUT='scripts/.fs-inspect';
const P=fs.mkdtempSync(path.join(os.tmpdir(),'sbs3-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT,{recursive:true});
const ch=spawn(CHROME,['--headless=new','--disable-gpu',`--remote-debugging-port=${CDP}`,`--user-data-dir=${P}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;
const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
const t=(await get('/json/list')).find(x=>x.type==='page');
const ws=new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:3,mobile:false});
const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
  return r.exceptionDetails?'EXC '+((r.exceptionDetails.exception||{}).description||'').split('\n')[0]:r.result.value;};

// 用数组 join 构造多行文本，避免转义问题
const FILL = `(()=>{const t=document.getElementById('promptText');
  t.value=['第一行测试内容','第二行测试内容','第三行测试内容','第四行测试内容','第五行测试内容','第六行'].join(String.fromCharCode(10));
  t.style.setProperty('max-height','30px','important');
  t.style.setProperty('height','30px','important');
  t.style.setProperty('overflow-y','scroll','important');
  const cs=getComputedStyle(t);
  return JSON.stringify({sh:t.scrollHeight, ch:t.clientHeight, ov:cs.overflowY,
    track:getComputedStyle(t,'::-webkit-scrollbar-track').backgroundColor,
    thumb:getComputedStyle(t,'::-webkit-scrollbar-thumb').backgroundColor});})()`;

for (const th of ['dark','light']) {
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,5000));
  await ev('window.alert=function(){}');
  if(th==='light'){ await ev(`document.body.classList.add('light-theme')`); await new Promise(r=>setTimeout(r,400)); }
  console.log(`  ${th}: ${await ev(FILL)}`);
  await new Promise(r=>setTimeout(r,700));
  const box=JSON.parse(await ev(`(()=>{const b=document.getElementById('promptEditor').getBoundingClientRect();
    return JSON.stringify({x:Math.max(0,b.x-4),y:Math.max(0,b.y-4),width:Math.min(b.width+8,1100),height:b.height+8})})()`));
  const r=await cdp(ws,'Page.captureScreenshot',{format:'png',clip:{...box,scale:2}});
  fs.writeFileSync(path.join(OUT,`sb-${th}.png`), Buffer.from(r.data,'base64'));
  console.log(`     → ${OUT}/sb-${th}.png`);
}
ws.close();try{ch.kill()}catch{};try{fs.rmSync(P,{recursive:true,force:true})}catch{}
