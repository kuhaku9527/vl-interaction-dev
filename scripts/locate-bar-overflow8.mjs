#!/usr/bin/env node
/**
 * 定位 8px 溢出的真正来源 + 设计更有效的响应式修复。
 * 上一轮教训：B1/B2（min-width:0）与 B3（窄屏收紧）都没消除 8px → 假设错误，需实测定位。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10056, PORT=8123;
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'find8-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
const main=async()=>{
  for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
    return r.exceptionDetails?'EXC '+(r.exceptionDetails.exception?.description||'').split('\n')[0]:r.result.value;};
  await ev('window.alert=function(){}');

  console.log('=== 定位 8px：扫描 bar 内所有后代，找右缘越界者 ===');
  console.log(await ev(`(()=>{
    const bar=document.getElementById('promptEditor');
    const cs=getComputedStyle(bar);
    const br=bar.getBoundingClientRect();
    const padRight=parseFloat(cs.paddingRight), bdRight=parseFloat(cs.borderRightWidth);
    const contentRight = br.right - padRight - bdRight;
    const contentLeft  = br.left + parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth);
    const hits=[];
    bar.querySelectorAll('*').forEach(e=>{
      const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
      if (r.width===0&&r.height===0) return;
      const over = (r.right - contentRight);
      const under = (contentLeft - r.left);
      if (over > 0.5 || under > 0.5) {
        hits.push({ tag:e.tagName, id:e.id, cls:(e.className||'').toString().slice(0,30),
          right:+r.right.toFixed(1), over:+over.toFixed(1), under:+under.toFixed(1),
          pos:s.position, disp:s.display, w:+r.width.toFixed(1) });
      }
    });
    // 也看是否有元素自身 scrollWidth 超出
    const scrollers=[];
    bar.querySelectorAll('*').forEach(e=>{ const o=e.scrollWidth-e.clientWidth;
      if (o>0) scrollers.push({id:e.id||e.className.toString().slice(0,24), over:o, tag:e.tagName}); });
    return JSON.stringify({contentRight:+contentRight.toFixed(1), contentLeft:+contentLeft.toFixed(1),
      barScrollW:bar.scrollWidth, barClientW:bar.clientWidth, hits, scrollers}, null, 1);
  })()`));

  ws.close(); cleanup();
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
