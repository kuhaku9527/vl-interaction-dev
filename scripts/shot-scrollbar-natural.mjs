#!/usr/bin/env node
/**
 * 用**自然溢出**（不强制 overflow-y:scroll / 不改高度）复测滚动条观感。
 * 上一次测试强设 height:30px + overflow-y:scroll，会让 Chromium 走"滚动条常显"分支、
 * 连原生上下箭头一起画出来 —— 那不是用户实际会看到的样子。
 * 真实场景：textarea 由 CSS 的 max-height:120px + rows=1 控制，内容多了自动出现滚动条。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10156, OUT='scripts/.fs-inspect';
const P=fs.mkdtempSync(path.join(os.tmpdir(),'nat-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT,{recursive:true});
const ch=spawn(CHROME,['--headless=new','--disable-gpu',`--remote-debugging-port=${CDP}`,`--user-data-dir=${P}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
const t=(await get('/json/list')).find(x=>x.type==='page');
const ws=new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:3,mobile:false});
const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
  return r.exceptionDetails?'EXC '+((r.exceptionDetails.exception||{}).description||'').split('\n')[0]:r.result.value;};

// 自然溢出：填够行数让 scrollHeight 超过 CSS 的 max-height:120px
const FILL = `(()=>{const t=document.getElementById('promptText');
  t.value=['一','二','三','四','五','六','七','八','九','十','十一','十二'].map((s,i)=>'第'+s+'行内容').join(String.fromCharCode(10));
  t.dispatchEvent(new Event('input',{bubbles:true}));
  if (typeof resizePromptInput==='function') { try{resizePromptInput();}catch(e){} }
  const cs=getComputedStyle(t);
  return JSON.stringify({sh:t.scrollHeight, ch:t.clientHeight, maxH:cs.maxHeight, ovY:cs.overflowY,
    needScroll: t.scrollHeight>t.clientHeight+1,
    track:getComputedStyle(t,'::-webkit-scrollbar-track').backgroundColor,
    thumb:getComputedStyle(t,'::-webkit-scrollbar-thumb').backgroundColor,
    btn:getComputedStyle(t,'::-webkit-scrollbar-button').display});})()`;

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
  fs.writeFileSync(path.join(OUT,`sbnat-${th}.png`), Buffer.from(r.data,'base64'));
  console.log(`     → ${OUT}/sbnat-${th}.png`);
}
ws.close();try{ch.kill()}catch{};try{fs.rmSync(P,{recursive:true,force:true})}catch{}
