#!/usr/bin/env node
/**
 * 输入栏响应式修复的候选验证（纠正上一轮的错误假设）
 *
 * 上一轮结论修正：
 *   - "8px 溢出"是 scrollWidth 伪影（无后代越界、overflow-x:visible → 不裁剪不出滚动条），
 *     不是用户看到的问题。真正的问题是**输入框被挤到不可用**：
 *     900px 时 16px、1024px 时 63px —— 用户截图里 "和 BT-7274 对话..." 折成 3 行即此。
 *   - B1/B2/B3（min-width:0 / 收紧按钮）都没解决它，因为根因是**栅格列宽**：
 *     .main-content 在 >900px 时保持 1.9fr/1fr 两列，右列被压到 ~310px。
 *
 * 候选：
 *   C1 提高堆叠断点（900 → 1200）：窄视口直接单列，输入栏获得全宽
 *   C2 给右列 min-width（grid minmax）
 *   C3 C1 + 窄屏按钮收紧（组合）
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10058, PORT=8123;
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'barfix-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

const C1 = `@media (max-width:1200px){ .main-content{grid-template-columns:1fr !important;grid-template-rows:auto auto !important;} }`;
const C2 = `.main-content{grid-template-columns:minmax(0,1.9fr) minmax(340px,1fr) !important;}`;
const C3 = C1 + `
@media (max-width:1200px){
  .chat-prompt-action#camBtn,.chat-prompt-action#liveModeBtn{min-width:46px;padding:6px 6px;}
  .chat-prompt-action#camBtn .ctrl-label,.chat-prompt-action#liveModeBtn .ctrl-label{font-size:10px;}
}`;

const M = `(()=>{const bar=document.getElementById('promptEditor');
  const t=document.getElementById('promptText').getBoundingClientRect();
  const card=document.getElementById('vlmOutputCard').getBoundingClientRect();
  const cols=getComputedStyle(document.querySelector('.main-content')).gridTemplateColumns;
  return JSON.stringify({txt:Math.round(t.width), txtH:Math.round(t.height), bar:Math.round(bar.getBoundingClientRect().width), card:Math.round(card.width), cols});})()`;

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
  const inject=css=>ev(`(()=>{let s=document.getElementById('cf');if(!s){s=document.createElement('style');s.id='cf';document.head.appendChild(s);}s.textContent=${JSON.stringify(css)};return 'ok'})()`);

  const widths=[1920,1600,1440,1280,1200,1128,1024,980,900,768,600,420];
  for (const [name,css] of [['修复前',null],['C1 断点1200',C1],['C2 minmax340',C2],['C3 组合',C3]]) {
    if (css) await inject(css); else await ev(`document.getElementById('cf')?.remove()`);
    await new Promise(r=>setTimeout(r,350));
    let minTxt=1e9, minAt=0, stacked=[], bad=[];
    const row=[];
    for (const w of widths) {
      await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:w,height:900,deviceScaleFactor:1,mobile:false});
      await new Promise(r=>setTimeout(r,330));
      const d=JSON.parse(await ev(M));
      row.push(`${w}:${d.txt}`);
      if (d.txt<minTxt){minTxt=d.txt;minAt=w;}
      if (d.txt<100) bad.push(`${w}px→${d.txt}px`);
      if (!d.cols.includes(' ')) stacked.push(w);
    }
    console.log(`\n  ── ${name} ──`);
    console.log(`     输入框宽度: ${row.join('  ')}`);
    console.log(`     最窄 ${minTxt}px @${minAt}px视口   ${bad.length? '❌ 不可用(<100px): '+bad.join(', ') : '✅ 全部 ≥100px'}`);
  }
  await ev(`document.getElementById('cf')?.remove()`);
  ws.close(); cleanup();
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
