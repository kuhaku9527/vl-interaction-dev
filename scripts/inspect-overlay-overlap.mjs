#!/usr/bin/env node
/**
 * 复现「全屏 overlay 与字幕重叠」（用户要求说明显示效果）
 *
 * 涉及两个浮层：
 *   #videoOverlay          —— 受设置页 overlayPosition（None/顶部/底部）控制，
 *                             常态与全屏都生效，显示 VLM 文本
 *   #fullscreenVlmOverlay  —— 全屏专属字幕（只显示 AI 回复，空则隐藏）
 *
 * 当 overlayPosition ≠ none 时两者同屏，需实测到底重不重叠、怎么重叠。
 * 三个取值各截一图 + 量测几何。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP = 10110, PORT = 8123, OUT = 'scripts/.fs-inspect';
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'ovl-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars',
  `--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'], { stdio:'ignore' });
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

const URL_ = `http://127.0.0.1:${PORT}/index.html`;
const VLM_TEXT = '屏幕中央出现一个红色瓶子，左边还有两个蓝色杯子，桌面有反光';

const INJECT = (pos) => `(()=>{
  const vo = document.getElementById('videoOverlay');
  vo.textContent = ${JSON.stringify(VLM_TEXT)};
  vo.classList.remove('show','top','bottom');
  if (${JSON.stringify(pos)} !== 'none') vo.classList.add('show', ${JSON.stringify(pos)});
  vlmHistory.length = 0;
  vlmHistory.push({key:'k1', prompt:'q', response:'我看到一个红色的瓶子，旁边有两个蓝色杯子。', rawText:''});
  syncVlmToFullscreen();
  return 'ok';
})()`;

const MEASURE = `(()=>{
  const g = (id) => { const e=document.getElementById(id); if(!e) return null;
    const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
    return { id, x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height),
      vis:(r.width>0&&r.height>0&&cs.display!=='none'&&cs.visibility!=='hidden'),
      z:cs.zIndex, pos:cs.position, fs:cs.fontSize, align:cs.textAlign }; };
  const vo = g('videoOverlay'), sub = g('fullscreenVlmOverlay'), bar = g('promptEditor');
  let inter = null;
  if (vo && sub && vo.vis && sub.vis) {
    const ox = Math.min(vo.x+vo.w, sub.x+sub.w) - Math.max(vo.x, sub.x);
    const oy = Math.min(vo.y+vo.h, sub.y+sub.h) - Math.max(vo.y, sub.y);
    if (ox > 0 && oy > 0) inter = { ox, oy, area: ox*oy };
  }
  // 字幕是否被输入栏挡住
  let subVsBar = null;
  if (sub && bar && sub.vis) {
    const oy = Math.min(sub.y+sub.h, bar.y+bar.h) - Math.max(sub.y, bar.y);
    if (oy > 0) subVsBar = { oy };
  }
  return JSON.stringify({ vh: innerHeight, overlayPos: localStorage.getItem('overlayPosition'),
    vo, sub, bar, inter, subVsBar,
    voText: (document.getElementById('videoOverlay')||{}).textContent || '',
    subText: (document.getElementById('fullscreenVlmContent')||{}).textContent || '' });
})()`;

const main = async () => {
  for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:URL_});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
    return r.exceptionDetails?'EXC '+((r.exceptionDetails.exception||{}).description||'').split('\n')[0]:r.result.value;};
  await ev('window.alert=function(){}');

  for (const pos of ['none','top','bottom']) {
    await ev(`localStorage.setItem('overlayPosition', ${JSON.stringify(pos)})`);
    await cdp(ws,'Page.navigate',{url:URL_});
    await new Promise(r=>setTimeout(r,5200));
    await ev('window.alert=function(){}');
    await ev(INJECT(pos));
    await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
    await ev(`document.querySelector('.fullscreen-btn').click()`);
    await new Promise(r=>setTimeout(r,1000));
    const d = JSON.parse(await ev(MEASURE));

    console.log(`\n${'='.repeat(76)}`);
    console.log(`【overlayPosition = ${pos}】全屏态`);
    console.log(`${'='.repeat(76)}`);
    const show=(n,o)=>{ if(!o){console.log(`  ${n}: (元素不存在)`);return;}
      console.log(`  ${n.padEnd(22)} ${o.vis?'可见':'隐藏'}  rect=[x=${o.x} y=${o.y} ${o.w}x${o.h}]  z=${o.z} pos=${o.pos} align=${o.align}`);};
    show('#videoOverlay', d.vo);
    show('#fullscreenVlmOverlay', d.sub);
    show('#promptEditor(输入栏)', d.bar);
    console.log(`  videoOverlay 文本: "${d.voText.slice(0,44)}"`);
    console.log(`  字幕文本        : "${d.subText.slice(0,44)}"`);
    console.log(`  → 两者重叠: ${d.inter ? `❌ 是，重叠区 ${d.inter.ox}x${d.inter.oy} = ${d.inter.area}px²` : '✅ 无重叠'}`);
    if (d.subVsBar) console.log(`  → 字幕与输入栏纵向重叠 ${d.subVsBar.oy}px`);

    const full = await cdp(ws,'Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(OUT,`overlay-${pos}-full.png`), Buffer.from(full.data,'base64'));
    // 底部 45% 特写
    const band = await cdp(ws,'Page.captureScreenshot',{format:'png',
      clip:{x:0,y:Math.round(d.vh*0.55),width:1600,height:Math.round(d.vh*0.45),scale:1}});
    fs.writeFileSync(path.join(OUT,`overlay-${pos}-bottom.png`), Buffer.from(band.data,'base64'));
    console.log(`  截图: overlay-${pos}-{full,bottom}.png`);

    await ev(`document.querySelector('.fullscreen-btn').click()`);
    await new Promise(r=>setTimeout(r,600));
  }
  await ev(`localStorage.setItem('overlayPosition','none')`);
  ws.close(); cleanup();
};
main().catch(e=>{console.error('FAILED:', e && (e.stack||e.message) || e); cleanup(); process.exit(2)});
