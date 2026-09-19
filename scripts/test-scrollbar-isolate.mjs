#!/usr/bin/env node
/**
 * 隔离测试：那些上下三角箭头到底是什么、能否消除。
 * 用一个最小页面（只有一个 textarea + 各种滚动条 CSS 变体）隔离，排除项目自身的干扰。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10164, OUT='scripts/.fs-inspect', PORT=8150;
const P=fs.mkdtempSync(path.join(os.tmpdir(),'iso-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT,{recursive:true});
fs.mkdirSync(P,{recursive:true});

const VARIANTS = [
  ['0-无任何样式', ''],
  ['1-button:none', `textarea::-webkit-scrollbar{width:8px}
    textarea::-webkit-scrollbar-button{display:none;width:0;height:0}`],
  ['2-button:none+track同色', `textarea::-webkit-scrollbar{width:8px}
    textarea::-webkit-scrollbar-button{display:none;width:0;height:0}
    textarea::-webkit-scrollbar-track{background:#161618}
    textarea::-webkit-scrollbar-thumb{background:rgba(255,255,255,.14);border-radius:3px}`],
  ['3-仅现代属性', `textarea{scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.14) #161618}`],
  ['4-两个按钮伪类都禁', `textarea::-webkit-scrollbar{width:8px}
    textarea::-webkit-scrollbar-button,
    textarea::-webkit-scrollbar-button:vertical:start:decrement,
    textarea::-webkit-scrollbar-button:vertical:end:increment,
    textarea::-webkit-scrollbar-button:start:decrement,
    textarea::-webkit-scrollbar-button:end:increment{display:none!important;width:0!important;height:0!important;background:transparent!important}`],
];

const html = VARIANTS.map(([name,css])=>`
<div style="margin:6px;font:12px sans-serif;color:#ccc">${name}</div>
<textarea style="width:260px;height:44px;background:#161618;color:#eee;border:1px solid #333;border-radius:8px;padding:6px">行1
行2
行3
行4
行5
行6
行7</textarea>
<style>${css}</style>`).join('\n');
fs.writeFileSync(path.join(P,'index.html'), `<!doctype html><meta charset=utf-8>
<body style="background:#0a0a0b">${html}</body>`);
const srv=spawn('services/.venv/Scripts/python.exe',['-m','http.server',String(PORT),'--directory',P,'--bind','127.0.0.1'],{stdio:'ignore'});
const ch=spawn(CHROME,['--headless=new','--disable-gpu',`--remote-debugging-port=${CDP}`,`--user-data-dir=${P}/cp`,'--window-size=800,700','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
await new Promise(r=>setTimeout(r,2200));
for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
const t=(await get('/json/list')).find(x=>x.type==='page');
const ws=new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:800,height:700,deviceScaleFactor:3,mobile:false});
await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
await new Promise(r=>setTimeout(r,3000));
const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});return r.exceptionDetails?'EXC':r.result.value};
console.log('=== 各变体的 ::-webkit-scrollbar-button 计算样式 ===');
console.log(await ev(`JSON.stringify([...document.querySelectorAll('textarea')].map((t,i)=>{
  const cs=getComputedStyle(t,'::-webkit-scrollbar-button');
  return {i, disp:cs.display, w:cs.width, h:cs.height, sh:t.scrollHeight, ch:t.clientHeight};
}),null,1)`));
const r=await cdp(ws,'Page.captureScreenshot',{format:'png'});
fs.writeFileSync(path.join(OUT,'sb-isolate.png'), Buffer.from(r.data,'base64'));
console.log(`截图: ${OUT}/sb-isolate.png`);
ws.close();try{ch.kill()}catch{};try{srv.kill()}catch{};try{fs.rmSync(P,{recursive:true,force:true})}catch{}
