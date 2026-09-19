#!/usr/bin/env node
/**
 * 修复 360px 级小屏输入栏（用户反馈 b 的同族问题）
 * 实测现状：5 个定宽控件占 ~220px，360px 视口下留给 textarea 仅 64px×83px
 *（高度反而涨到 83px —— 文字折行，与用户截图的"折成多行"同因）。
 *
 * 候选（逐个实测，选最优）：
 *   D1 超窄屏隐藏两个带文字的按钮的**文字**（只留图标，省 ~60px）
 *   D2 超窄屏把按钮改紧凑（40px 方钮）
 *   D3 D1+D2 组合
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10062, PORT=8123;
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'d-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

const D1 = `@media (max-width:520px){
  .chat-prompt-action#camBtn .ctrl-label,
  .chat-prompt-action#liveModeBtn .ctrl-label{display:none;}
  .chat-prompt-action#camBtn,.chat-prompt-action#liveModeBtn{min-width:42px;width:42px;height:42px;padding:0;}
}`;
const D2 = `@media (max-width:520px){
  .chat-prompt-action#camBtn,.chat-prompt-action#liveModeBtn{min-width:40px;width:40px;height:40px;padding:0;}
  .chat-prompt-shell{gap:5px;padding:8px;}
  .chat-prompt-action.speech,.chat-prompt-action.send{width:38px;height:38px;}
}`;
const D3 = D1 + `
@media (max-width:520px){
  .chat-prompt-shell{gap:5px;padding:8px;}
  .chat-prompt-action.speech,.chat-prompt-action.send{width:38px;height:38px;}
}`;

const M = `(()=>{const t=document.getElementById('promptText').getBoundingClientRect();
  const bar=document.getElementById('promptEditor').getBoundingClientRect();
  // 文字是否单行：比较 scrollHeight 与单行高度
  const el=document.getElementById('promptText');
  return JSON.stringify({txt:Math.round(t.width), txtH:Math.round(t.height),
    bar:Math.round(bar.width)+'x'+Math.round(bar.height), singleLine: el.scrollHeight<=el.clientHeight+2});})()`;

const main=async()=>{
  for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
    return r.exceptionDetails?'EXC':r.result.value;};
  const inject=css=>ev(`(()=>{let s=document.getElementById('df');if(!s){s=document.createElement('style');s.id='df';document.head.appendChild(s);}s.textContent=${JSON.stringify(css)};return 'ok'})()`);

  for (const [name,css] of [['修复前',null],['D1 隐文字',D1],['D2 紧按钮',D2],['D3 组合',D3]]) {
    if (css) await inject(css); else await ev(`document.getElementById('df')?.remove()`);
    let line=`  ${name.padEnd(12)}`;
    for (const w of [430,400,380,360,340,320]) {
      await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:w,height:740,deviceScaleFactor:1,mobile:true});
      await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
      await new Promise(r=>setTimeout(r,3600));
      await ev('window.alert=function(){}');
      if (css) await inject(css);
      await new Promise(r=>setTimeout(r,250));
      const d=JSON.parse(await ev(M));
      line += ` ${w}:${d.txt}${d.singleLine?'':'↕'}`;
    }
    console.log(line + '   (↕ = 文字折行)');
  }
  await ev(`document.getElementById('df')?.remove()`);
  ws.close(); cleanup();
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
