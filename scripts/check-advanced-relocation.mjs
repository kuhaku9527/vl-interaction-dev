#!/usr/bin/env node
/**
 * 卡片归位验收（2026-09-19 用户拍板）：
 *   1. 「Live 常驻模式」「Wake / ASR」在「高级」面板可见，在「外观」不可见
 *   2. 「实时」按钮回到聊天输入栏
 *   3. 「主动搭话」用 .toggle-switch（而非原生 checkbox）
 *
 * 需要先起静态服务：
 *   services/.venv/Scripts/python.exe -m http.server 8123 \
 *     --directory services/webui/src/joy_interaction_webui/static --bind 127.0.0.1
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';

const PORT = 8123, CDP = 10017, OUT = 'scripts/.fs-inspect';
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'reloc-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
if (!fs.existsSync(CHROME)) { console.error('未找到 Chrome'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,1000','about:blank'], { stdio:'ignore' });
const get = p => new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0; const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

let pass=0, fail=0;
const check=(c,l,d='')=>{console.log(`  ${c?'✅':'❌'} ${l}${d?'  '+d:''}`); c?pass++:fail++;};

const main = async () => {
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>(await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true})).result.value;
  await ev('window.alert=function(){}');

  const vis = (id) => `(()=>{const e=document.getElementById(${JSON.stringify(id)}); if(!e) return 'MISSING';
    const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
    if(cs.display==='none'||r.width===0||r.height===0) return 'hidden';
    // 再确认祖先链上没有 cat-hidden
    let n=e; while(n&&n!==document.body){ if(n.classList&&n.classList.contains('cat-hidden')) return 'hidden-by-panel'; n=n.parentElement; }
    return 'visible';})()`;

  // ---- 打开设置 ----
  await ev(`document.getElementById('settingsBtn').click()`);
  await new Promise(r=>setTimeout(r,700));

  const shot = async (name, sel) => {
    let r;
    if (sel) {
      const box = JSON.parse(await ev(`(()=>{const e=document.querySelector(${JSON.stringify(sel)}); if(!e) return 'null';
        const b=e.getBoundingClientRect(); return JSON.stringify({x:Math.max(0,b.x-8),y:Math.max(0,b.y-8),width:b.width+16,height:b.height+16})})()`));
      r = await cdp(ws,'Page.captureScreenshot',{format:'png',clip:{...box,scale:1}});
    } else r = await cdp(ws,'Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(OUT,name), Buffer.from(r.data,'base64'));
    return path.join(OUT,name);
  };

  for (const [panel, expectVisible, expectHidden] of [
    ['advanced',  ['liveModeSection','wakeAsrSection'], ['liveModeSection']],
    ['appearance',['appearanceSection'],                ['liveModeSection','wakeAsrSection']],
  ]) {
    await ev(`document.querySelector('#modalNav .nav-item[data-panel="${panel}"]').click()`);
    await new Promise(r=>setTimeout(r,600));
    console.log(`\n【${panel}】面板`);
    if (panel === 'advanced') {
      // 必须**先切到本面板再展开** —— 切面板前的展开会被 cat-hidden 重置显示状态，
      // 导致卡片仍处于 collapsed（实测：截图只框到 35px 高的标题行）。
      await ev(`['liveModeSection','wakeAsrSection'].forEach(id=>{const e=document.getElementById(id); if(e) e.classList.remove('collapsed');})`);
      await new Promise(r=>setTimeout(r,500));
      const st = await ev(`JSON.stringify(['liveModeSection','wakeAsrSection'].map(id=>{const e=document.getElementById(id);
        return {id, collapsed:e.classList.contains('collapsed'), h:Math.round(e.getBoundingClientRect().height)};}))`);
      console.log(`  卡片展开状态: ${st}`);
      JSON.parse(st).forEach(c => check(!c.collapsed && c.h > 80, `#${c.id} 已展开且内容完整`, `高 ${c.h}px`));
      expectVisible.forEach(async id => {});
      for (const id of expectVisible) check(await ev(vis(id)) === 'visible', `#${id} 可见`);
      for (const id of expectHidden.filter(x=>!expectVisible.includes(x))) check(await ev(vis(id)) !== 'visible', `#${id} 不在本面板`);
      console.log(`  截图: ${await shot('H-advanced.png', '#radioSilenceSection')}`);
      // 单独特写两张新卡（H-advanced 只框住无线电静默卡）
      console.log(`  Live 卡截图: ${await shot('H1-live.png', '#liveModeSection')}`);
      console.log(`  Wake 卡截图: ${await shot('H2-wake.png', '#wakeAsrSection')}`);
      // 主动搭话是否用 .toggle-switch
      const sw = await ev(`(()=>{const i=document.getElementById('liveProactiveToggle'); if(!i) return 'MISSING';
        const p=i.parentElement; return JSON.stringify({parentTag:p.tagName, parentClass:p.className,
          hasSlider: !!p.querySelector('.toggle-slider'), type:i.type});})()`);
      console.log(`  主动搭话: ${sw}`);
      const s = JSON.parse(sw);
      check(s.parentClass && s.parentClass.includes('toggle-switch'), '主动搭话使用 .toggle-switch（非原生 checkbox）');
      check(s.hasSlider === true, '主动搭话含 .toggle-slider 滑块');
      check(s.type === 'checkbox', '语义仍是 checkbox（a11y 正确）');
    } else {
      // #appearanceSection 是 display:contents 容器 —— 它本身没有盒子（0x0），
      // 直接测它的 rect 会假失败。应改测它的**子卡片**。
      for (const id of expectVisible) {
        if (id === 'appearanceSection') {
          const r = await ev(`(()=>{const s=document.getElementById('appearanceSection');
            const kids=[...s.querySelectorAll('.settings-section')];
            const shown=kids.filter(k=>{const b=k.getBoundingClientRect();const cs=getComputedStyle(k);
              return cs.display!=='none'&&b.width>0&&b.height>0;});
            return JSON.stringify({total:kids.length, shown:shown.length,
              titles:shown.map(k=>{const t=k.querySelector('.settings-section-title');return t?t.textContent.trim():'?';})});})()`);
          const R = JSON.parse(r);
          check(R.shown > 0, `#appearanceSection 的子卡片可见`, `${R.shown}/${R.total} 张：${R.titles.join(', ')}`);
        } else {
          check(await ev(vis(id)) === 'visible', `#${id} 可见`);
        }
      }
      for (const id of expectHidden) check(await ev(vis(id)) !== 'visible', `#${id} 已不在外观`);
    }
  }

  // ---- 「实时」是否回到聊天栏 ----
  await ev(`document.getElementById('settingsClose').click()`);
  await new Promise(r=>setTimeout(r,600));
  console.log('\n【聊天输入栏】');
  const bar = await ev(`(()=>{const bar=document.getElementById('promptEditor'); const ids=[...bar.querySelectorAll('button[id]')].map(e=>e.id);
    const b=document.getElementById('liveModeBtn'); if(!b) return JSON.stringify({ids, liveModeBtn:'MISSING'});
    const r=b.getBoundingClientRect(); return JSON.stringify({ids, liveModeBtn:[Math.round(r.width),Math.round(r.height)],
      inBar: bar.contains(b)});})()`);
  console.log(`  输入栏按钮: ${JSON.parse(bar).ids.join(', ')}`);
  const B = JSON.parse(bar);
  check(B.liveModeBtn !== 'MISSING' && B.inBar === true, '「实时」按钮回到聊天输入栏');
  check(B.liveModeBtn && B.liveModeBtn[0] > 0 && B.liveModeBtn[1] >= 40, '「实时」渲染为双行按钮', B.liveModeBtn ? `${B.liveModeBtn[0]}x${B.liveModeBtn[1]}` : '');
  console.log(`  截图: ${await shot('I-chatbar.png', '#promptEditor')}`);

  ws.close(); cleanup();
  console.log(`\n${'='.repeat(56)}\n通过 ${pass} / 失败 ${fail}`);
  process.exit(fail?1:0);
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
