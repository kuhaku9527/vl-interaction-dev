#!/usr/bin/env node
/**
 * 复现用户反馈的两个问题（2026-09-19）：
 *  (a) 全屏按钮颜色不统一：浅色模式下偏深，全屏下几乎看不见
 *  (b) 输入栏在不同宽度下的比例兼容问题（挤压/重叠）
 *
 * 全部用实测几何 + 计算样式，不读码推断。
 *   服务: python -m http.server 8123 --directory .../static
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';

const CDP = 10050, PORT = 8123, OUT = 'scripts/.fs-inspect';
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'uibar-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'], { stdio:'ignore' });
const get = p => new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0; const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

const TOOLBAR = `(()=>{
  const g = (sel) => { const e=document.querySelector(sel); if(!e) return null;
    const cs=getComputedStyle(e); const r=e.getBoundingClientRect();
    return { bg:cs.backgroundColor, color:cs.color, border:cs.borderColor, opacity:cs.opacity,
             w:Math.round(r.width), h:Math.round(r.height),
             visible:(r.width>0&&r.height>0&&cs.display!=='none') }; };
  const csRoot = getComputedStyle(document.documentElement);
  return JSON.stringify({
    theme: document.body.classList.contains('light-theme') ? 'light' : 'dark',
    mode: document.getElementById('videoCard').classList.contains('fullscreen') ? 'fullscreen' : 'normal',
    fullscreenBtn: g('.fullscreen-btn'),
    quickCameraBtn: g('#quickCameraBtn'),
    inkDeep: csRoot.getPropertyValue('--overlay-deep').trim(),
    inkText: csRoot.getPropertyValue('--text').trim()
  });})()`;

const BAR = `(()=>{
  const bar = document.getElementById('promptEditor');
  const shell = bar.closest('.chat-prompt-shell') || bar;
  const br = shell.getBoundingClientRect();
  const kids = [...bar.querySelectorAll('button[id], textarea[id], select[id]')].map(e=>{
    const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
    return { id:e.id, x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height),
             flex:cs.flex, minW:cs.minWidth, shrink:cs.flexShrink };
  });
  // 两两重叠检测（水平方向）
  const ov=[];
  for(let i=0;i<kids.length;i++)for(let j=i+1;j<kids.length;j++){
    const a=kids[i],b=kids[j];
    const ox=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x);
    const oy=Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y);
    if(ox>1&&oy>1) ov.push(a.id+' ⨯ '+b.id+' = '+ox+'px');
  }
  const squeezed = kids.filter(k=>k.w<=6);
  // 溢出容器检测
  const overflow = kids.filter(k=> k.x < br.x-1 || (k.x+k.w) > (br.x+br.width+1)).map(k=>k.id);
  return JSON.stringify({ vw: innerWidth, shell:[Math.round(br.x),Math.round(br.y),Math.round(br.width),Math.round(br.height)],
    kids, overlaps:ov, squeezed:squeezed.map(k=>k.id), overflow, scrollW: shell.scrollWidth, clientW: shell.clientWidth });})()`;

const main = async () => {
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable'); await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
    return r.exceptionDetails?('EXC '+(r.exceptionDetails.exception?.description||'').split('\n')[0]):r.result.value;};
  await ev('window.alert=function(){}');

  console.log('='.repeat(78));
  console.log('【A】全屏按钮 vs 后置按钮 —— 颜色/透明度对比（用户反馈 a）');
  console.log('='.repeat(78));
  for (const theme of ['dark','light']) {
    await ev(`document.body.classList.toggle('light-theme', ${theme==='light'})`);
    await new Promise(r=>setTimeout(r,300));
    for (const fs_ of [false,true]) {
      const isFs = await ev(`document.getElementById('videoCard').classList.contains('fullscreen')`);
      if (isFs !== fs_) { await ev(`document.querySelector('.fullscreen-btn').click()`); await new Promise(r=>setTimeout(r,600)); }
      const d = JSON.parse(await ev(TOOLBAR));
      console.log(`\n  [${d.theme} / ${d.mode}]   tokens: --overlay-deep=${d.inkDeep}  --text=${d.inkText}`);
      const show=(n,b)=>{ if(!b){console.log(`    ${n}: (无)`);return;}
        console.log(`    ${n.padEnd(16)} bg=${b.bg.padEnd(26)} color=${b.color.padEnd(20)} opacity=${String(b.opacity).padEnd(5)} ${b.w}x${b.h} ${b.visible?'':'❌不可见'}`);};
      show('全屏按钮', d.fullscreenBtn);
      show('后置按钮', d.quickCameraBtn);
      if (d.fullscreenBtn && d.quickCameraBtn) {
        const same = d.fullscreenBtn.bg===d.quickCameraBtn.bg && d.fullscreenBtn.opacity===d.quickCameraBtn.opacity;
        console.log(`    → 一致性: ${same ? '✅ 相同' : '❌ 不同（bg 或 opacity 有差异）'}`);
      }
      if (fs_) { await ev(`document.querySelector('.fullscreen-btn').click()`); await new Promise(r=>setTimeout(r,600)); }
    }
  }
  await ev(`document.body.classList.remove('light-theme')`);
  await new Promise(r=>setTimeout(r,300));

  console.log('\n' + '='.repeat(78));
  console.log('【B】输入栏在不同宽度下的兼容性（用户反馈 b）');
  console.log('='.repeat(78));
  for (const w of [1920,1600,1280,1128,1024,900,768,600,420]) {
    await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:w,height:900,deviceScaleFactor:1,mobile:false});
    await new Promise(r=>setTimeout(r,500));
    const d = JSON.parse(await ev(BAR));
    const flag = (d.overlaps.length||d.squeezed.length) ? '❌' : '✅';
    console.log(`\n  ${flag} 视口 ${String(w).padStart(4)}px   输入栏 ${d.shell[2]}x${d.shell[3]} @x=${d.shell[0]}   scrollW=${d.scrollW} clientW=${d.clientW}`);
    console.log(`     控件: ${d.kids.map(k=>`${k.id}:${k.w}x${k.h}`).join('  ')}`);
    if (d.squeezed.length) console.log(`     ⚠️ 被挤到 ≤6px: ${d.squeezed.join(', ')}`);
    if (d.overlaps.length) console.log(`     ⚠️ 重叠: ${d.overlaps.join(' | ')}`);
    if (d.overflow.length) console.log(`     ⚠️ 溢出容器: ${d.overflow.join(', ')}`);
    if (d.scrollW > d.clientW+1) console.log(`     ⚠️ 横向溢出 ${d.scrollW-d.clientW}px`);
    if ([1128,420].includes(w)) {
      const r = await cdp(ws,'Page.captureScreenshot',{format:'png',clip:{x:0,y:600,width:w,height:300,scale:1}});
      fs.writeFileSync(path.join(OUT,`bar-${w}.png`), Buffer.from(r.data,'base64'));
      console.log(`     截图: scripts/.fs-inspect/bar-${w}.png`);
    }
  }
  ws.close(); cleanup();
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
