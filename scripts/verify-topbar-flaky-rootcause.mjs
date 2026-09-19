#!/usr/bin/env node
/**
 * 定向验证 check-topbar-fixes 的 flaky 根因（2026-09-19，captain 复核 t2 的报告）
 *
 * t2 声称：check-topbar-fixes 的间歇失败源于「status_poll.js 的 1s 轮询」与
 * 「脚本设 setState('live','ok') 后固定等 300ms」之间的竞争 —— 即测试自身的缺陷，
 * 与 t2 的内联外移无关（约 30% 概率抽到坏相位）。
 *
 * 本脚本用**同一页面、同一断言**做两组对照，唯一变量是「是否停掉该轮询」：
 *   A 组：原样（轮询在跑）→ 预期间歇失败
 *   B 组：先 clearInterval 停掉轮询 → 预期稳定通过
 * 若 B 组稳定而 A 组间歇 → 根因确为轮询竞争，与代码改动无关。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP=10040, N=Number(process.argv[2]||6);
const PROFILE=fs.mkdtempSync(path.join(os.tmpdir(),'flaky-'));
const CHROME=process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome=spawn(CHROME,['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0;const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

// 复刻 check-topbar-fixes 的核心断言（与它同序同时长）
const CORE = `(async()=>{
  const chip = () => { const b=document.querySelector('.mode-chip[data-mode="live"]');
    return {active:b.classList.contains('active'), dataState:b.getAttribute('data-state')}; };
  const sleep = ms => new Promise(r=>setTimeout(r,ms));
  const out = {};
  window.JoyModeChip.setState('live','ok');
  await sleep(300);                       // ← 与脚本相同的 300ms 窗口
  out.afterOk = chip();
  out.pass = out.afterOk.active === true; // 关键断言
  window.JoyModeChip.setState('live','off');
  await sleep(300);
  out.afterOff = chip();
  return JSON.stringify(out);
})()`;

const main=async()=>{
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});return r.exceptionDetails?('EXC '+(r.exceptionDetails.exception?.description||'').split('\n')[0]):r.result.value;};
  await ev('window.alert=function(){}');

  console.log('=== A 组：轮询在跑（原样）===');
  let aPass=0,aFail=0;
  for(let i=0;i<N;i++){
    const r=JSON.parse(await ev(CORE));
    if(r.pass) aPass++; else { aFail++; console.log(`  第${i+1}次 FAIL → afterOk=${JSON.stringify(r.afterOk)}`); }
  }
  console.log(`  结果: pass=${aPass} fail=${aFail}  (共 ${N})`);

  console.log('\n=== B 组：先停掉 live 轮询（唯一变量）===');
  const stopped=await ev(`(()=>{ try{
      // status_poll.js 内部用闭包变量 _livePollTimer；用最粗暴但有效的方式：
      // 冻结所有 1000ms 定时器的回调 —— 记录 id 后逐个 clear。
      const ids = []; const orig = window.setInterval;
      return (function(){ 
        // 无法枚举既有定时器 → 改用"覆盖 renderLiveStatus 的干扰源"：
        // 把 liveModeActive 之外的分支屏蔽：直接停掉 poll 的入口。
        if (typeof window.stopLiveStatusPoll === 'function') { window.stopLiveStatusPoll(); return 'stopped-via-api'; }
        return 'no-api';
      })();
    }catch(e){ return 'ERR '+e.message; } })()`);
  console.log('  停轮询结果:', stopped);
  let bPass=0,bFail=0;
  for(let i=0;i<N;i++){
    const r=JSON.parse(await ev(CORE));
    if(r.pass) bPass++; else { bFail++; console.log(`  第${i+1}次 FAIL → afterOk=${JSON.stringify(r.afterOk)}`); }
  }
  console.log(`  结果: pass=${bPass} fail=${bFail}  (共 ${N})`);

  console.log('\n=== 结论 ===');
  if(aFail>0 && bFail===0) console.log('  ✅ 根因确认为「轮询 vs 300ms 窗口」竞争 —— 测试自身 flaky，与代码改动无关');
  else if(aFail===0 && bFail===0) console.log('  ⚠️ A 组本轮未抽到坏相位（样本不足），需加大 N 才能定论');
  else console.log(`  ❓ 需进一步分析：A fail=${aFail}, B fail=${bFail}`);
  ws.close();cleanup();
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
