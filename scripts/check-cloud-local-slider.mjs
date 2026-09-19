#!/usr/bin/env node
/**
 * 复现用户反馈：云端/本地滑块「没有动态效果」（2026-09-19）
 *
 * 假设：.svc-seg-indicator 的位移由两条**同特异性**规则共同决定：
 *   styles.css:3125  .service-row[data-mode="local"] .svc-seg-indicator{transform:translateX(100%)}
 *   styles.css:4755  .svc-seg-btn.on ~ .svc-seg-indicator{transform:translateX(100%)}
 * 后者在文件更后面 → 同特异性下胜出，于是：
 *   - 只要「第一个按钮」带 .on，指示块就永远在右边；
 *   - 切到 local 时靠 3125 右移，但切回 cloud 时 4755 仍把它钉在右边 → 不动。
 * 用实测验证「点击切换后指示块位置是否真的变化」。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10091, PORT=8123, OUT='scripts/.fs-inspect';
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'seg-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT,{recursive:true});
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,1000','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
let pass=0,fail=0;
const check=(c,l,d='')=>{console.log(`  ${c?'✅':'❌'} ${l}${d?'  '+d:''}`);c?pass++:fail++;};

const main=async()=>{
  for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
    return r.exceptionDetails?'EXC '+(r.exceptionDetails.exception?.description||'').split('\n')[0]:r.result.value;};
  await ev('window.alert=function(){}');
  // 打开设置
  await ev(`document.getElementById('settingsBtn').click()`);
  await new Promise(r=>setTimeout(r,700));

  console.log('=== 每个 svc-seg 的滑块行为（点击 cloud→local→cloud，看指示块 translateX）===');
  const probe = `(slot)=>{
    const seg=document.querySelector('.svc-seg[data-slot="'+slot+'"]');
    if(!seg) return JSON.stringify({slot, missing:true});
    const ind=seg.querySelector('.svc-seg-indicator');
    const btn=seg.querySelector('.svc-seg-btn.on');
    const row=seg.closest('.service-row');
    return JSON.stringify({slot,
      on: btn? btn.getAttribute('data-mode'):null,
      rowMode: row? row.getAttribute('data-mode'):null,
      tx: getComputedStyle(ind).transform,
      indX: Math.round(ind.getBoundingClientRect().x - seg.getBoundingClientRect().x),
      segW: Math.round(seg.getBoundingClientRect().width)});}`;

  for (const slot of ['summary','asr','embedding','tts']) {
    const s0=JSON.parse(await ev(`(${probe})('${slot}')`));
    if (s0.missing) { console.log(`  ⚠️ ${slot}: 未找到`); continue; }
    // 点击 local
    await ev(`(()=>{const seg=document.querySelector('.svc-seg[data-slot="${slot}"]');
      const b=seg.querySelector('.svc-seg-btn[data-mode="local"]'); if(b) b.click();})()`);
    await new Promise(r=>setTimeout(r,600));
    const s1=JSON.parse(await ev(`(${probe})('${slot}')`));
    // 再点 cloud
    await ev(`(()=>{const seg=document.querySelector('.svc-seg[data-slot="${slot}"]');
      const b=seg.querySelector('.svc-seg-btn[data-mode="cloud"]'); if(b) b.click();})()`);
    await new Promise(r=>setTimeout(r,600));
    const s2=JSON.parse(await ev(`(${probe})('${slot}')`));

    console.log(`\n  [${slot}] seg 宽 ${s0.segW}px`);
    console.log(`    初始    on=${s0.on} rowMode=${s0.rowMode} indX=${s0.indX}  ${s0.tx}`);
    console.log(`    点local on=${s1.on} rowMode=${s1.rowMode} indX=${s1.indX}  ${s1.tx}`);
    console.log(`    点cloud on=${s2.on} rowMode=${s2.rowMode} indX=${s2.indX}  ${s2.tx}`);
    // 判定：cloud 时指示块应在左(≈2/3px)、local 时在右(≈segW/2)
    const left  = s0.indX <= 6;
    const right = s1.indX >= s0.segW/2 - 6;
    const back  = s2.indX <= 6;
    check(left,  `${slot} 初始(cloud) 指示块在左`, `indX=${s0.indX}`);
    check(right, `${slot} 切 local 指示块滑到右`, `indX=${s1.indX} (期望≥${Math.round(s0.segW/2-6)})`);
    check(back,  `${slot} 切回 cloud 指示块回到左`, `indX=${s2.indX}`);
  }

  const r=await cdp(ws,'Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(path.join(OUT,'seg-before.png'), Buffer.from(r.data,'base64'));
  console.log(`\n  截图: scripts/.fs-inspect/seg-before.png`);
  ws.close(); cleanup();
  console.log(`\n${'='.repeat(52)}\n通过 ${pass} / 失败 ${fail}`);
};
main().catch(e=>{console.error("FAILED:", e && (e.stack || e.message) || String(e));cleanup();process.exit(2)});
