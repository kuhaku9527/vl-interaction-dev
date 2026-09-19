#!/usr/bin/env node
/**
 * 验收 A + B（2026-09-19 用户拍板）
 *   A：全屏时禁用画面叠字（#videoOverlay），全屏字幕为唯一显示面 → 消除撞车
 *   B：设置项改名，文案与真实语义一致（"VLM 输出位置"，两端都说清）
 *
 * 必须走**真实交互**：改下拉 → 触发 change → 模拟后端推 VLM 帧（与 ws_dispatcher 同款调用）。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP = 10120, PORT = 8123, OUT = 'scripts/.fs-inspect';
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'ab-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars',
  `--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'], { stdio:'ignore' });
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
let pass=0,fail=0;
const check=(c,l,d='')=>{console.log(`  ${c?'✅':'❌'} ${l}${d?'  '+d:''}`);c?pass++:fail++;};
const URL_ = `http://127.0.0.1:${PORT}/index.html`;

// 模拟一帧真实 VLM 输出（复制 ws_dispatcher.js:83-86 的调用序列）
// ★ 顺序很重要（实测教训）：vlm_history.js 的 renderVlmHistory() 会用
//   shouldShowVlmHistoryShell() **重算并覆盖** resultText.style.display，
//   而 applyOverlayPosition() 也写同一个属性。真实产品流程里两者都会跑，
//   故这里按「先 render 灌数据 → 再 applyOverlayPosition 定显示」的顺序调用，
//   才等价于用户在设置里改完下拉后的真实状态。
const PUSH_FRAME = (pos) => `(()=>{
  const displayText='桌面上有一个红色瓶子，左边两个蓝色杯子。';
  lastText='</response>'+displayText;
  videoOverlay.classList.remove('show','top','bottom');
  vlmHistory.length=0;
  vlmHistory.push({key:'k1',prompt:'看一下',response:displayText,rawText:''});
  if (typeof renderVlmHistory==='function') renderVlmHistory();
  if (typeof updateResultText==='function') { try{ updateResultText(displayText, null, null); }catch(e){} }
  if (typeof applyOverlayPosition==='function') applyOverlayPosition(settings.overlayPosition);
  if (typeof syncVlmToFullscreen==='function') syncVlmToFullscreen();
  return 'ok';
})()`;

const STATE = `(()=>{
  const vis=(id)=>{const e=document.getElementById(id); if(!e) return null;
    const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
    return (r.width>0&&r.height>0&&cs.display!=='none'&&cs.visibility!=='hidden')
      ? {w:Math.round(r.width),h:Math.round(r.height),y:Math.round(r.y)} : null;};
  return JSON.stringify({
    fs: document.getElementById('videoCard').classList.contains('fullscreen'),
    pos: (document.getElementById('overlayPosition')||{}).value,
    vo: vis('videoOverlay'), sub: vis('fullscreenVlmOverlay'),
    chat: vis('resultText'), bar: vis('promptEditor')
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

  console.log('【B】设置项文案（真实渲染值）');
  await ev(`document.getElementById('settingsBtn').click()`); await new Promise(r=>setTimeout(r,700));
  await ev(`(()=>{const n=document.querySelector('#modalNav .nav-item[data-panel="appearance"]'); if(n)n.click();})()`);
  await new Promise(r=>setTimeout(r,600));
  const txt = JSON.parse(await ev(`(()=>{
    const sel=document.getElementById('overlayPosition');
    const item=sel.closest('.settings-item');
    return JSON.stringify({
      label: item.querySelector('.settings-item-label').textContent.trim(),
      desc: item.querySelector('.settings-item-description').textContent.trim(),
      opts: [...sel.options].map(o=>({v:o.value,t:o.textContent.trim()}))
    });})()`));
  console.log(`    标签: ${txt.label}`);
  console.log(`    说明: ${txt.desc}`);
  txt.opts.forEach(o=>console.log(`    选项 ${o.v} → ${o.t}`));
  check(/位置|location/i.test(txt.label), 'B 标签已改为「输出位置」语义', txt.label);
  check(/聊天|chat/i.test(txt.desc) || /hidden|隐藏/i.test(txt.desc), 'B 说明提到聊天框（不再只讲叠字）', txt.desc);
  check(txt.opts[0].t.includes('聊天') || /chat/i.test(txt.opts[0].t), 'B 首个选项明示「聊天框」', txt.opts[0].t);
  check(/画面/.test(txt.opts[1].t) && /画面/.test(txt.opts[2].t), 'B top/bottom 明示「画面」', `${txt.opts[1].t} / ${txt.opts[2].t}`);
  await ev(`document.getElementById('settingsClose').click()`); await new Promise(r=>setTimeout(r,500));

  console.log('\n【A】全屏时画面叠字应被禁用（消除撞车）');
  for (const pos of ['none','top','bottom']) {
    await cdp(ws,'Page.navigate',{url:URL_});
    await new Promise(r=>setTimeout(r,5200));
    await ev('window.alert=function(){}');
    await ev(`(()=>{const s=document.getElementById('overlayPosition'); s.value=${JSON.stringify(pos)};
      s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await new Promise(r=>setTimeout(r,500));
    await ev(PUSH_FRAME(pos));
    await new Promise(r=>setTimeout(r,500));
    const norm = JSON.parse(await ev(STATE));
    console.log(`\n  [${pos}] 常规态: 叠字=${norm.vo?'可见':'隐藏'} 聊天框=${norm.chat?'可见':'隐藏'}`);
    // 常规态行为必须**不变**：top/bottom 仍显示叠字且隐藏聊天框
    if (pos === 'none') {
      check(norm.chat !== null && norm.vo === null, 'A 常规态 none：聊天框可见、叠字隐藏');
    } else {
      check(norm.vo !== null, `A 常规态 ${pos}：叠字仍可见（行为不变）`);
      check(norm.chat === null, `A 常规态 ${pos}：聊天框仍被隐藏（互斥语义不变）`);
    }
    await ev(`document.querySelector('.fullscreen-btn').click()`);
    await new Promise(r=>setTimeout(r,1000));
    await ev(PUSH_FRAME(pos));
    await new Promise(r=>setTimeout(r,500));
    const fsState = JSON.parse(await ev(STATE));
    console.log(`  [${pos}] 全屏态: 叠字=${fsState.vo?'可见':'隐藏'} 字幕=${fsState.sub?'可见 y'+fsState.sub.y:'隐藏'} 聊天框=${fsState.chat?'可见':'隐藏'}`);
    check(fsState.vo === null, `A 全屏 ${pos}：画面叠字已禁用`, fsState.vo?`仍可见 ${fsState.vo.w}x${fsState.vo.h}`:'已隐藏');
    check(fsState.sub !== null, `A 全屏 ${pos}：全屏字幕仍正常显示`);
    if (pos !== 'none') {
      // 撞车判定：字幕与叠字不得同时可见
      check(!(fsState.vo && fsState.sub), `A 全屏 ${pos}：无「同一内容显示两遍」`);
    }
    if (pos === 'bottom' && fsState.bar) {
      const overlap = (fsState.sub && fsState.sub.y + fsState.sub.h > fsState.bar.y) ? '有' : '无';
      check(overlap === '无', 'A 全屏 bottom：字幕不被输入栏遮挡');
    }
    await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
    const shot = await cdp(ws,'Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(OUT,`ab-${pos}-fs.png`), Buffer.from(shot.data,'base64'));
    await ev(`document.querySelector('.fullscreen-btn').click()`); await new Promise(r=>setTimeout(r,500));
  }
  await ev(`localStorage.setItem('overlayPosition','none')`);
  ws.close(); cleanup();
  console.log(`\n截图: scripts/.fs-inspect/ab-{none,top,bottom}-fs.png`);
  console.log(`\n${'='.repeat(52)}\n通过 ${pass} / 失败 ${fail}`);
  process.exit(fail?1:0);
};
main().catch(e=>{console.error('FAILED:', e && (e.stack||e.message) || e); cleanup(); process.exit(2)});
