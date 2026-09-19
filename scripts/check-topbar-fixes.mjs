#!/usr/bin/env node
/**
 * 顶栏两处修复验收（2026-09-19 用户反馈）：
 *  (a) live/kws chip 的红框只在【使用时】出现，非常态
 *  (b) 健康菜单不再越出视口左边界
 *
 * 判据一律用实测几何 + 计算样式，不用"看着对"。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10021, OUT='scripts/.fs-inspect';
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'topfix-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT,{recursive:true});
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0;const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
let pass=0,fail=0;
const check=(c,l,d='')=>{console.log(`  ${c?'✅':'❌'} ${l}${d?'  '+d:''}`);c?pass++:fail++;};

const main=async()=>{
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>(await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true})).result.value;
  await ev('window.alert=function(){}');

  const chipInfo = () => ev(`(()=>{const brand=getComputedStyle(document.documentElement).getPropertyValue('--brand').trim();
    return JSON.stringify([...document.querySelectorAll('.mode-chip')].map(b=>{const cs=getComputedStyle(b);const r=b.getBoundingClientRect();
      return {mode:b.dataset.mode, active:b.classList.contains('active'), dataState:b.getAttribute('data-state'),
        border:cs.borderColor, brand, rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};}))})()`);

  console.log('\n【A】mode-chip 红框语义');
  // 初始（未连接任何模式）：应无红框
  let A = JSON.parse(await chipInfo());
  console.log('  ' + JSON.stringify(A));
  check(A.every(c=>c.active===false), '初始两 chip 均非 active（红框非常态）');
  check(A.every(c=>c.dataState==='off'), '初始 data-state=off');

  // ★ 2026-09-19 修复本脚本自身的 flaky（captain 自查发现）：
  //   status_poll.js 的 pollLiveStatus 每 1000ms 跑一次；当 liveModeActive=false 时
  //   它走 renderLiveStatus(null) → **内部直接调** setModeChipState('live','off')
  //   （是闭包内直接引用，所以包 window.JoyModeChip.setState 探针抓不到）。
  //   本脚本原本"设 'ok' 后固定等 300ms 就断言" → 只要这 300ms 跨过一次 1s tick，
  //   chip 就被打回 off，断言失败。实测失败率约 30%（10 次里 3 次）。
  //
  //   修法（而非放宽断言）：先停掉该轮询，再驱动 chip —— 这样测的是
  //   「setModeChipState 的映射逻辑」本身，不再与后台轮询的相位竞争。
  //   定向验证：停轮询后连测 12 次 12/12 稳定通过（修复前 7/10）。
  const pollStopped = await ev(`(()=>{ if (typeof window.stopLiveStatusPoll==='function'){ window.stopLiveStatusPoll(); return 'stopped'; } return 'no-api'; })()`);
  console.log(`  （已停 live 状态轮询以消除相位竞争: ${pollStopped}）`);

  // 用真实 API 驱动，模拟"live 在跑"
  await ev(`window.JoyModeChip.setState('live','ok')`);
  await new Promise(r=>setTimeout(r,300));
  let B = JSON.parse(await chipInfo());
  const liveB = B.find(c=>c.mode==='live'), kwsB = B.find(c=>c.mode==='kws');
  check(liveB.active===true, 'live 运行中 → 变 active（红框出现）');
  const isBrand = (c)=>c.border.replace(/\s/g,'').includes(c.brand.replace(/\s/g,''))
                     || c.border.replace(/\s/g,'').includes('200,30,42');
  check(isBrand(liveB), 'live 红框为 brand 色', liveB.border);
  check(kwsB.active===false, 'kws 未运行 → 保持非 active');
  check(!isBrand(kwsB), 'kws 边框非 brand 色', kwsB.border);
  // 回到 off
  await ev(`window.JoyModeChip.setState('live','off')`);
  await new Promise(r=>setTimeout(r,300));
  let C = JSON.parse(await chipInfo());
  check(C.find(c=>c.mode==='live').active===false, 'live 关闭后红框消失');

  console.log('\n【B】健康菜单边界');
  await ev(`document.getElementById('healthPill').click()`);
  await new Promise(r=>setTimeout(r,600));
  const m = JSON.parse(await ev(`(()=>{const menu=document.getElementById('healthMenu');const r=menu.getBoundingClientRect();
    return JSON.stringify({x:Math.round(r.x),right:Math.round(r.right),w:Math.round(r.width),vw:innerWidth,
      open:menu.classList.contains('open')})})()`));
  console.log('  ' + JSON.stringify(m));
  check(m.open, '菜单已打开');
  check(m.x >= 0, '菜单未越出视口左边界', `x=${m.x}`);
  check(m.right <= m.vw, '菜单未越出视口右边界', `right=${m.right} / vw=${m.vw}`);
  const r=await cdp(ws,'Page.captureScreenshot',{format:'png',clip:{x:0,y:0,width:700,height:420,scale:1}});
  fs.writeFileSync(path.join(OUT,'K-topbar-fixed.png'),Buffer.from(r.data,'base64'));
  console.log('  截图: scripts/.fs-inspect/K-topbar-fixed.png');

  ws.close();cleanup();
  console.log(`\n${'='.repeat(52)}\n通过 ${pass} / 失败 ${fail}`);
  process.exit(fail?1:0);
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
