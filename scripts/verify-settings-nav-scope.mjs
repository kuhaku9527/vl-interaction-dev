#!/usr/bin/env node
/**
 * 裁定 t1 的决策点：showSettingsPanel 到底是不是全局？
 * 方法：在真实页面里做「移除 window 暴露后裸调用」的对照实验 —— 这是唯一权威判据。
 * 若移除后裸调用仍可用 → 它本就是全局，t1 的加法暴露是不必要的；
 * 若移除后裸调用抛 ReferenceError → it was truly IIFE-scoped，暴露是必要的。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10030, PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'scope-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0;const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
const main=async()=>{
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});return r.exceptionDetails?('EXC: '+(r.exceptionDetails.exception?.description||r.exceptionDetails.text).split('\n')[0]):r.result.value;};
  await ev('window.alert=function(){}');

  console.log('=== 1. 当前状态（t1 已加暴露）===');
  console.log('  window.showSettingsPanel     :', await ev(`typeof window.showSettingsPanel`));
  console.log('  window.JoySettingsNav        :', await ev(`typeof (window.JoySettingsNav&&window.JoySettingsNav.showSettingsPanel)`));

  console.log('\n=== 2. 关键对照：删掉 window 暴露后，裸调用 showSettingsPanel 会怎样？ ===');
  console.log('  (先删 window 暴露)');
  await ev(`delete window.showSettingsPanel; if(window.JoySettingsNav) delete window.JoySettingsNav.showSettingsPanel; 'deleted'`);
  console.log('  删除后 window.showSettingsPanel:', await ev(`typeof window.showSettingsPanel`));
  const bare = await ev(`(function(){ try { showSettingsPanel('wiki'); return 'BARE_OK — 它是全局!'; } catch(e){ return 'BARE_FAIL — '+e.constructor.name+': '+e.message; } })()`);
  console.log('  裸调用结果:', bare);

  console.log('\n=== 3. 页面上其它脚本是否同样能裸调用（跨文件可达性）===');
  const fromOtherFile = await ev(`(function(){ try { const f=new Function('return typeof showSettingsPanel'); return f(); } catch(e){ return 'ERR '+e.message; } })()`);
  console.log('  new Function 作用域内 typeof:', fromOtherFile);

  console.log('\n=== 4. 结论 ===');
  const isReallyGlobal = String(bare).includes('BARE_OK');
  console.log(isReallyGlobal
    ? '  ❗ showSettingsPanel 原本就是全局 → t1 的加法暴露**不必要**（可移除）'
    : '  ✅ showSettingsPanel 确为 IIFE 作用域 → t1 的加法暴露**必要**（否则 ReferenceError）');
  ws.close();cleanup();
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
