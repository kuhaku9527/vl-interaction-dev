#!/usr/bin/env node
/**
 * 最终确认：输入框滚动条观感（深/浅主题）
 *
 * 关键教训（本轮踩到）：之前几次截图失败是因为**用 JS 设 textarea.value 后，
 * 页面自身的 input 处理会重置它**，导致截图时已无溢出、滚动条根本不存在，
 * 于是几张图字节相同、看起来"修复没生效"。
 * 可靠做法：用 CDP 的 Input.insertText 真实键入（触发正常输入流程），
 * 并在截图前断言 scrollHeight > clientHeight。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10163, OUT='scripts/.fs-inspect';
const P=fs.mkdtempSync(path.join(os.tmpdir(),'fin-'));
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

const TEXT = ['第一行测试内容','第二行测试内容','第三行测试内容','第四行测试内容','第五行测试内容','第六行测试内容','第七行测试内容'].join('\n');

for (const th of ['dark','light']) {
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,5000));
  await ev('window.alert=function(){}');
  if(th==='light'){ await ev(`document.body.classList.add('light-theme')`); await new Promise(r=>setTimeout(r,400)); }
  // 真实聚焦 + 键入（走正常输入流程，不会被重置）
  await ev(`document.getElementById('promptText').focus()`);
  await cdp(ws,'Input.insertText',{text:TEXT});
  await new Promise(r=>setTimeout(r,900));
  const st = JSON.parse(await ev(`(()=>{const t=document.getElementById('promptText');
    const cs=getComputedStyle(t);
    return JSON.stringify({val:t.value.length, sh:t.scrollHeight, ch:t.clientHeight,
      overflow: t.scrollHeight>t.clientHeight+1,
      track:getComputedStyle(t,'::-webkit-scrollbar-track').backgroundColor,
      thumb:getComputedStyle(t,'::-webkit-scrollbar-thumb').backgroundColor,
      btn:getComputedStyle(t,'::-webkit-scrollbar-button').display,
      shellBg:getComputedStyle(document.querySelector('.chat-prompt-shell')).backgroundColor});})()`));
  console.log(`  ${th}: 溢出=${st.overflow} (sh=${st.sh} ch=${st.ch})  track=${st.track}  shell=${st.shellBg}`);
  if(!st.overflow) console.log('     ⚠️ 未产生溢出，截图不代表真实观感');
  const box=JSON.parse(await ev(`(()=>{const b=document.getElementById('promptEditor').getBoundingClientRect();
    return JSON.stringify({x:Math.max(0,b.x-4),y:Math.max(0,b.y-4),width:Math.min(b.width+8,900),height:b.height+8})})()`));
  const r=await cdp(ws,'Page.captureScreenshot',{format:'png',clip:{...box,scale:2}});
  fs.writeFileSync(path.join(OUT,`sbfin-${th}.png`), Buffer.from(r.data,'base64'));
  console.log(`     → ${OUT}/sbfin-${th}.png`);
}
ws.close();try{ch.kill()}catch{};try{fs.rmSync(P,{recursive:true,force:true})}catch{}
