#!/usr/bin/env node
/**
 * 实测「设置页 overlayPosition」的真实语义与实现状态（用户质疑：是否重复造轮子）
 *
 * 走**真实交互流程**（改下拉 → 触发 change），不走注入 class 的捷径。
 * 检查三个显示面在四种状态下的组合：
 *   #resultText           聊天框（VLM Output Info，含完整历史）
 *   #videoOverlay         视频上叠字（受 overlayPosition 控制）
 *   #fullscreenVlmOverlay 全屏字幕（全屏专属）
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP = 10112, PORT = 8123, OUT = 'scripts/.fs-inspect';
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars',
  `--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'], { stdio:'ignore' });
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
const URL_ = `http://127.0.0.1:${PORT}/index.html`;
const SEED = `(()=>{
  vlmHistory.length = 0;
  vlmHistory.push({key:'k1', prompt:'看一下我的桌面', response:'桌面上有一个红色瓶子。', rawText:''});
  lastText = '</response>桌面上有一个红色瓶子。';
  if (typeof renderVlmHistory === 'function') renderVlmHistory();
  return 'ok';
})()`;
const STATE = `(()=>{
  const vis = (id) => { const e=document.getElementById(id); if(!e) return 'missing';
    const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
    return (r.width>0&&r.height>0&&cs.display!=='none') ? ('可见 '+Math.round(r.width)+'x'+Math.round(r.height)+' @y'+Math.round(r.y)) : '隐藏'; };
  return JSON.stringify({
    fs: document.getElementById('videoCard').classList.contains('fullscreen'),
    pos: (document.getElementById('overlayPosition')||{}).value,
    resultText: vis('resultText'),
    videoOverlay: vis('videoOverlay'),
    subOverlay: vis('fullscreenVlmOverlay'),
    voText: (document.getElementById('videoOverlay')||{}).textContent||'',
    subText: (document.getElementById('fullscreenVlmContent')||{}).textContent||''
  });})()`;

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

  const label = {display:'none'};
  const fmt = (d) => `聊天框=${d.resultText} ｜ 视频叠字=${d.videoOverlay} ｜ 全屏字幕=${d.subOverlay}`;

  console.log('='.repeat(80));
  console.log('走真实交互：改设置下拉 → 触发 change → 观察三个显示面');
  console.log('='.repeat(80));

  for (const pos of ['none','top','bottom']) {
    await cdp(ws,'Page.navigate',{url:URL_});
    await new Promise(r=>setTimeout(r,5200));
    await ev('window.alert=function(){}');
    // 打开设置 → 外观 → 改下拉（真实 change 事件）
    const applied = await ev(`(()=>{
      const sel = document.getElementById('overlayPosition');
      if (!sel) return 'no-select';
      sel.value = ${JSON.stringify(pos)};
      sel.dispatchEvent(new Event('change', {bubbles:true}));
      return 'ok';
    })()`);
    await new Promise(r=>setTimeout(r,800));
    await ev(SEED);   // 灌入一轮 VLM 输出
    await new Promise(r=>setTimeout(r,700));

    console.log(`\n【overlayPosition = ${pos}】（change 事件: ${applied}）`);
    let d = JSON.parse(await ev(STATE));
    console.log(`  常规态  ${fmt(d)}`);
    console.log(`         视频叠字文本: "${d.voText.slice(0,36)}"`);

    await ev(`document.querySelector('.fullscreen-btn').click()`);
    await new Promise(r=>setTimeout(r,1000));
    d = JSON.parse(await ev(STATE));
    console.log(`  全屏态  ${fmt(d)}`);
    console.log(`         视频叠字: "${d.voText.slice(0,30)}" ／ 全屏字幕: "${d.subText.slice(0,30)}"`);
    // 判定「同一内容被显示几处」
    const surfaces = [d.resultText, d.videoOverlay, d.subOverlay].filter(s=>String(s).startsWith('可见')).length;
    console.log(`  → 同时可见的显示面数: ${surfaces}`);
    await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
    const shot = await cdp(ws,'Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(OUT,`sem-${pos}-fs.png`), Buffer.from(shot.data,'base64'));
    await ev(`document.querySelector('.fullscreen-btn').click()`);
    await new Promise(r=>setTimeout(r,600));
  }
  await ev(`localStorage.setItem('overlayPosition','none')`);
  ws.close(); cleanup();
  console.log(`\n截图: scripts/.fs-inspect/sem-{none,top,bottom}-fs.png`);
};
main().catch(e=>{console.error('FAILED:', e && (e.stack||e.message) || e); cleanup(); process.exit(2)});
