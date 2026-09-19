#!/usr/bin/env node
/** 量测顶栏两处问题：mode-chip 的常驻红框、health-menu 溢出。 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10020, OUT='scripts/.fs-inspect';
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'topbar-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT,{recursive:true});
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0;const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
const t=(await get('/json/list')).find(x=>x.type==='page');
const ws=new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
await new Promise(r=>setTimeout(r,5500));
const ev=async e=>(await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true})).result.value;
await ev('window.alert=function(){}');

console.log('=== A. mode-chip（live/kws）当前样式 ===');
console.log(await ev(`(()=>{
  return JSON.stringify([...document.querySelectorAll('.mode-chip')].map(b=>{const cs=getComputedStyle(b);const r=b.getBoundingClientRect();
    return {mode:b.dataset.mode, cls:b.className, active:b.classList.contains('active'),
      border:cs.borderColor, bg:cs.backgroundColor, dataState:b.getAttribute('data-state'),
      rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};}),null,1)})()`));

console.log('\\n=== B. 是否有 JS 会给 mode-chip 加/去 .active ===');
console.log(await ev(`(()=>{const s=[...document.querySelectorAll('script')].map(x=>x.textContent||'').join('\\n');
  const hits=s.split('\\n').filter(l=>/mode-chip/.test(l)&&/active/.test(l));
  return JSON.stringify({inlineHits:hits.length, sample:hits.slice(0,3)},null,1)})()`));

console.log('\\n=== C. health-pill / health-menu 几何与溢出 ===');
await ev(`document.getElementById('healthPill').click()`);
await new Promise(r=>setTimeout(r,600));
console.log(await ev(`(()=>{
  const pill=document.getElementById('healthPill'), wrap=document.querySelector('.health-wrap'), menu=document.getElementById('healthMenu');
  const pr=pill.getBoundingClientRect(), wr=wrap.getBoundingClientRect(), mr=menu.getBoundingClientRect();
  const cs=getComputedStyle(menu);
  return JSON.stringify({
    viewport:[innerWidth,innerHeight],
    wrap:[Math.round(wr.x),Math.round(wr.y),Math.round(wr.width),Math.round(wr.height)],
    pill:[Math.round(pr.x),Math.round(pr.y),Math.round(pr.width),Math.round(pr.height)],
    menu:[Math.round(mr.x),Math.round(mr.y),Math.round(mr.width),Math.round(mr.height)],
    menuCSS:{position:cs.position,right:cs.right,left:cs.left,top:cs.top,width:cs.width},
    overflow:{left: mr.x<0, leftBy: mr.x<0?Math.round(-mr.x):0,
              right: mr.right>innerWidth, rightBy: mr.right>innerWidth?Math.round(mr.right-innerWidth):0},
    open: menu.classList.contains('open')
  },null,1)})()`));
const r=await cdp(ws,'Page.captureScreenshot',{format:'png',clip:{x:0,y:0,width:900,height:600,scale:1}});
fs.writeFileSync(path.join(OUT,'J-topbar.png'),Buffer.from(r.data,'base64'));
console.log('  截图: scripts/.fs-inspect/J-topbar.png');
ws.close();try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}
