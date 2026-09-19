#!/usr/bin/env node
/**
 * 遗留 4：移动端 ≤768px 全屏未验 —— 补验（2026-09-19）
 * 同时复查本轮响应式改动（断点 1200px）在移动端是否引入回归。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10060, PORT=8123, OUT='scripts/.fs-inspect';
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'mobfs-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT,{recursive:true});
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
let pass=0,fail=0;
const check=(c,l,d='')=>{console.log(`  ${c?'✅':'❌'} ${l}${d?'  '+d:''}`);c?pass++:fail++;};

const PROBE = `(()=>{
  const d=x=>document.getElementById(x);
  const vis=e=>{ if(!e) return null; const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
    return (r.width>0&&r.height>0&&cs.display!=='none')? {w:Math.round(r.width),h:Math.round(r.height),
      x:Math.round(r.x),y:Math.round(r.y),z:cs.zIndex,pos:cs.position}:null; };
  const vc=d('videoCard'); const fs=vc.classList.contains('fullscreen');
  const hit=(e)=>{ const v=vis(e); if(!v) return 'not-visible';
    const t=document.elementFromPoint(v.x+v.w/2, v.y+v.h/2);
    return (t===e||e.contains(t))?'reachable':'blocked-by:'+(t?(t.id||t.className):'null'); };
  const t=document.getElementById('promptText');
  return JSON.stringify({
    fullscreen: fs,
    headerVisible: !!(vis(document.querySelector('.header'))),
    videoCard: vis(vc),
    fsBtn: vis(document.querySelector('.fullscreen-btn')), fsBtnHit: hit(document.querySelector('.fullscreen-btn')),
    promptText: t? Math.round(t.getBoundingClientRect().width) : null,
    bar: vis(d('promptEditor')),
    subtitle: vis(d('fullscreenVlmOverlay')),
    bgScrollW: document.documentElement.scrollWidth, vw: innerWidth
  });})()`;

const main=async()=>{
  for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
    return r.exceptionDetails?'EXC '+(r.exceptionDetails.exception?.description||'').split('\n')[0]:r.result.value;};

  for (const [w,h,label] of [[768,1024,'平板竖 768'],[600,900,'窄 600'],[420,800,'手机 420'],[360,740,'小屏 360']]) {
    await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:true});
    await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
    await new Promise(r=>setTimeout(r,5000));
    await ev('window.alert=function(){}');
    console.log(`\n【${label}】视口 ${w}x${h}`);

    // 常规态
    let d = JSON.parse(await ev(PROBE));
    console.log(`  常规态: 输入框 ${d.promptText}px  输入栏 ${d.bar?d.bar.w+'x'+d.bar.h:'—'}`);
    check(d.promptText !== null && d.promptText >= 100, `${label} 常规态输入框可用`, `${d.promptText}px`);
    check(d.bgScrollW <= d.vw + 1, `${label} 无横向溢出`, `scrollW=${d.bgScrollW} vw=${d.vw}`);

    // 进入全屏
    const clicked = await ev(`(()=>{const b=document.querySelector('.fullscreen-btn'); if(!b) return 'no-btn'; b.click(); return 'ok';})()`);
    if (clicked !== 'ok') { check(false, `${label} 全屏按钮存在`); continue; }
    await new Promise(r=>setTimeout(r,900));
    d = JSON.parse(await ev(PROBE));
    check(d.fullscreen === true, `${label} 进入全屏`);
    check(d.fsBtnHit === 'reachable', `${label} ①退出按钮可点（未被遮挡）`, d.fsBtnHit);
    check(d.headerVisible === false, `${label} ②顶栏已收起`);
    check(d.videoCard && d.videoCard.w >= w-2 && d.videoCard.h >= h-60, `${label} ③画面铺满`,
      d.videoCard? `${d.videoCard.w}x${d.videoCard.h}`:'—');
    check(d.promptText !== null && d.promptText >= 80, `${label} ④全屏输入框可用`, `${d.promptText}px`);
    check(d.bgScrollW <= d.vw + 1, `${label} ⑤全屏无横向溢出`, `scrollW=${d.bgScrollW}`);

    const r=await cdp(ws,'Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(OUT,`mobfs-${w}.png`), Buffer.from(r.data,'base64'));
    console.log(`  截图: scripts/.fs-inspect/mobfs-${w}.png`);
  }
  ws.close(); cleanup();
  console.log(`\n${'='.repeat(52)}\n通过 ${pass} / 失败 ${fail}`);
  process.exit(fail?1:0);
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
