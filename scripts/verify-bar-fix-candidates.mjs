#!/usr/bin/env node
/**
 * 候选修复的实测验证（2026-09-19 用户反馈 a/b）
 * 只注入 CSS 测结果，不写文件 —— 通过后才落盘。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10055, PORT=8123;
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'fixbar-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

// ---- 候选修复 ----
const FIX_A = `
.video-tools .fullscreen-btn{opacity:.86;}
.video-card.fullscreen .fullscreen-btn{opacity:1;background:var(--overlay-deep);border-color:var(--border);}
.video-card.fullscreen .quick-camera-btn{opacity:1;}
`;
const FIX_B1 = `
.chat-prompt-shell{min-width:0;}
.chat-prompt-input{min-width:0;}
`;
const FIX_B2 = `
.chat-prompt-shell{min-width:0;}
.chat-prompt-input{min-width:0;}
.ctrl-wrap{min-width:0;}
.ctrl-wrap>.chat-prompt-action{min-width:0;}
`;
// 窄屏收紧：带文字的按钮在窄屏变紧凑
const FIX_B3 = `
.chat-prompt-shell{min-width:0;}
.chat-prompt-input{min-width:0;}
@media (max-width:1180px){
  .chat-prompt-action#camBtn,.chat-prompt-action#liveModeBtn{min-width:46px;padding:6px 6px;}
  .chat-prompt-action#camBtn .ctrl-label,.chat-prompt-action#liveModeBtn .ctrl-label{font-size:10px;}
}
@media (max-width:980px){
  .chat-prompt-action#camBtn,.chat-prompt-action#liveModeBtn{min-width:42px;padding:6px 4px;}
}
`;

const BAR = `(()=>{const bar=document.getElementById('promptEditor');
  const kids=[...bar.querySelectorAll('button[id],textarea[id]')];
  const t=document.getElementById('promptText').getBoundingClientRect();
  return JSON.stringify({over:bar.scrollWidth-bar.clientWidth, txt:Math.round(t.width), txtH:Math.round(t.height)});})()`;

const TOOL = `(()=>{const g=s=>{const e=document.querySelector(s);const c=getComputedStyle(e);
  return {bg:c.backgroundColor,op:c.opacity,col:c.color};};
  return JSON.stringify({theme:document.body.classList.contains('light-theme')?'light':'dark',
    mode:document.getElementById('videoCard').classList.contains('fullscreen')?'fs':'normal',
    fs:g('.fullscreen-btn'), cam:g('#quickCameraBtn')});})()`;

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
  const inject = (css) => ev(`(()=>{let s=document.getElementById('fixprobe');
    if(!s){s=document.createElement('style');s.id='fixprobe';document.head.appendChild(s);}
    s.textContent=${JSON.stringify(css)}; return 'ok';})()`);
  const clear = () => ev(`document.getElementById('fixprobe')?.remove()`);

  console.log('='.repeat(76));
  console.log('【A】全屏按钮一致性 —— 候选修复验证');
  console.log('='.repeat(76));
  for (const label of ['修复前','修复后']) {
    if (label==='修复后') await inject(FIX_A);
    console.log(`\n  ── ${label} ──`);
    for (const th of ['dark','light']) {
      await ev(`document.body.classList.toggle('light-theme', ${th==='light'})`);
      await new Promise(r=>setTimeout(r,250));
      for (const fs_ of [false,true]) {
        const isFs = await ev(`document.getElementById('videoCard').classList.contains('fullscreen')`);
        if (isFs!==fs_) { await ev(`document.querySelector('.fullscreen-btn').click()`); await new Promise(r=>setTimeout(r,500)); }
        const d=JSON.parse(await ev(TOOL));
        const same = d.fs.bg===d.cam.bg && d.fs.op===d.cam.op;
        console.log(`    [${d.theme}/${d.mode}] 全屏 bg=${d.fs.bg.padEnd(24)} op=${String(d.fs.op).padEnd(5)} | 后置 bg=${d.cam.bg.padEnd(24)} op=${d.cam.op}  → ${same?'✅一致':'❌不一致'}`);
        if (fs_) { await ev(`document.querySelector('.fullscreen-btn').click()`); await new Promise(r=>setTimeout(r,500)); }
      }
    }
  }
  await ev(`document.body.classList.remove('light-theme')`);
  await clear();
  await new Promise(r=>setTimeout(r,300));

  console.log('\n'+'='.repeat(76));
  console.log('【B】输入栏溢出/挤压 —— 候选修复验证（over=溢出px, txt=输入框宽）');
  console.log('='.repeat(76));
  for (const [name, css] of [['修复前',''],['B1 min-width:0',''],['B2 +ctrl-wrap',''],['B3 +窄屏收紧','']]) {
    if (name==='B1 min-width:0') await inject(FIX_B1);
    if (name==='B2 +ctrl-wrap') await inject(FIX_B2);
    if (name==='B3 +窄屏收紧') await inject(FIX_B3);
    let line=`  ${name.padEnd(16)}`;
    let worstOver=0, worstTxt=1e9;
    for (const w of [1920,1600,1280,1128,1024,900,768,600,420]) {
      await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:w,height:900,deviceScaleFactor:1,mobile:false});
      await new Promise(r=>setTimeout(r,380));
      const d=JSON.parse(await ev(BAR));
      worstOver=Math.max(worstOver,d.over); worstTxt=Math.min(worstTxt,d.txt);
      line += ` [${w}:${d.over>0?'⚠'+d.over:'✓'}/${d.txt}]`;
    }
    console.log(line);
    console.log(`  ${''.padEnd(16)} → 最大溢出 ${worstOver}px ／ 输入框最窄 ${worstTxt}px`);
  }
  await clear();
  ws.close(); cleanup();
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
