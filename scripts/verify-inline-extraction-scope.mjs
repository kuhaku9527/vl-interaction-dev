#!/usr/bin/env node
/**
 * criterion 2 专项：外移后「看似全局实则词法作用域」符号的可见性是否与基线一致。
 *
 * 关键符号 showSettingsPanel / PANEL_ROOTS / nav 都声明在内联块 #3 的 IIFE 内。
 * 该块已外移为 incremental_wiring.js。必须证明：
 *   ① showSettingsPanel 仍**不是**裸全局（跨文件裸调用仍会 ReferenceError）—— 与 t1 实测一致
 *   ② t1 的加法暴露仍生效（window.showSettingsPanel / window.JoySettingsNav.showSettingsPanel）
 *   ③ 外部脚本走暴露后的路径仍能真正展开知识库面板（功能未被外移破坏）
 *   ④ 基线里就不可达的符号（PANEL_ROOTS / nav）在外移后同样不可达 —— 可见性零变化
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';

const CDP = Number(process.env.CDP_PORT || 10041);
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(),'lexscope-'));
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',
  ['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,1000','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0;const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
let fails=0; const check=(n,ok,d)=>{console.log(`${ok?'  ✅':'  ❌'} ${n}${d!==undefined?`  → ${JSON.stringify(d)}`:''}`); if(!ok)fails++;};

const main = async () => {
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,6500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true}); if(r.exceptionDetails) return {__exc:String(r.exceptionDetails.exception?.description||'').slice(0,160)}; return r.result.value;};

  console.log('='.repeat(74));
  console.log('criterion 2：外移后「看似全局实则词法作用域」符号可见性');
  console.log('='.repeat(74));

  console.log('\n[1] 关键对照：删掉 window 暴露后，showSettingsPanel 是否仍为 IIFE 作用域');
  // 方法学说明：t1 之后 window.showSettingsPanel 已存在，而**全局对象属性**在 sloppy
  // mode 下本来就能当裸标识符解析 —— 所以「直接裸调用」会成功，不能据此判断词法作用域。
  // 正确做法（与 scripts/verify-settings-nav-scope.mjs 一致）：先删掉 window 上的暴露，
  // 再测裸调用。若此时 ReferenceError，即证明该函数本身仍是 IIFE 作用域、
  // 跨文件可见性完全依赖暴露（这正是 t1 的加法暴露为必要的证据）。
  const bare1 = await ev(`(() => {
    const savedDirect = window.showSettingsPanel;
    const savedNs = window.JoySettingsNav && window.JoySettingsNav.showSettingsPanel;
    delete window.showSettingsPanel;
    if (window.JoySettingsNav) delete window.JoySettingsNav.showSettingsPanel;
    let viaFn, viaBare;
    try { viaFn = new Function('return typeof showSettingsPanel')(); } catch(e){ viaFn = 'THROWS:'+e.name; }
    try { viaBare = new Function('showSettingsPanel("wiki"); return "CALLED";')(); } catch(e){ viaBare = e.name; }
    // 还原
    window.showSettingsPanel = savedDirect;
    window.JoySettingsNav = window.JoySettingsNav || {};
    window.JoySettingsNav.showSettingsPanel = savedNs;
    return JSON.stringify({ viaFn, viaBare });
  })()`);
  const b = JSON.parse(bare1);
  console.log(`    删暴露后 new Function typeof = ${JSON.stringify(b.viaFn)}`);
  console.log(`    删暴露后裸调用             = ${JSON.stringify(b.viaBare)}`);
  check('删掉暴露后 showSettingsPanel 跨文件不可见（仍为 IIFE 作用域，与 t1 实测一致）', b.viaFn === 'undefined', b.viaFn);
  check('删掉暴露后裸调用抛 ReferenceError（暴露确为必要，非冗余）', b.viaBare === 'ReferenceError', b.viaBare);

  console.log('\n[1b] 还原后：暴露重新生效（确认上面的对照未破坏页面）');
  const restored = await ev(`JSON.stringify({ winDirect: typeof window.showSettingsPanel, winNs: typeof (window.JoySettingsNav && window.JoySettingsNav.showSettingsPanel) })`);
  console.log('    ', restored);
  check('对照后暴露已完整还原', JSON.parse(restored).winDirect === 'function' && JSON.parse(restored).winNs === 'function', restored);

  console.log('\n[2] t1 的加法暴露仍生效');
  const exposed = JSON.parse(await ev(`JSON.stringify({
    winDirect: typeof window.showSettingsPanel,
    winNs: typeof (window.JoySettingsNav && window.JoySettingsNav.showSettingsPanel),
    viaWindowFn: (function(){ try { return new Function('return typeof window.showSettingsPanel')(); } catch(e){ return 'THROWS'; } })()
  })`));
  console.log('    ', JSON.stringify(exposed));
  check('window.showSettingsPanel 为 function', exposed.winDirect === 'function', exposed.winDirect);
  check('window.JoySettingsNav.showSettingsPanel 为 function', exposed.winNs === 'function', exposed.winNs);
  check('跨文件（new Function）也能看到 window.showSettingsPanel', exposed.viaWindowFn === 'function', exposed.viaWindowFn);

  console.log('\n[3] 基线里就不可达的符号：外移后仍不可达（可见性零变化）');
  const lex = JSON.parse(await ev(`(() => {
    const probe = n => { try { return new Function('return typeof ' + n)(); } catch(e){ return 'THROWS:'+e.name; } };
    return JSON.stringify({ PANEL_ROOTS: probe('PANEL_ROOTS'), nav: probe('nav'), setOpen: probe('setOpen'), buildCameraOption: probe('buildCameraOption'), buildThemeIcon: probe('buildThemeIcon') });
  })()`));
  console.log('    ', JSON.stringify(lex));
  // 基线实测：PANEL_ROOTS=undefined, nav=undefined（均 IIFE 内）
  check('PANEL_ROOTS 仍不可达（与基线一致）', lex.PANEL_ROOTS === 'undefined', lex.PANEL_ROOTS);
  check('nav 仍不可达（与基线一致）', lex.nav === 'undefined', lex.nav);

  console.log('\n[4] 功能未被外移破坏：走暴露路径真能展开知识库面板');
  const fn = JSON.parse(await ev(`(async () => {
    // 注入确定性 extended-status，使 wiki 进入唯一绑 onclick 的分支
    const real = window.fetch.bind(window);
    window.fetch = function(input, init){
      const u = typeof input === 'string' ? input : (input && input.url) || '';
      if (u.includes('/api/services/extended-status')) {
        return Promise.resolve(new Response(JSON.stringify({
          memory: { enabled: true, reachable: true, ok: true },
          wiki:   { enabled: true, reachable: true, ok: false, reason: 't2-crit2-probe' }
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return real(input, init);
    };
    await window.JoyStatusPoll.pollExtendedStatus();
    await new Promise(r=>setTimeout(r,200));
    const badge = document.getElementById('wikiBadge');
    const bound = typeof badge.onclick === 'function';
    window.showSettingsPanel('services');                 // 复位
    await new Promise(r=>setTimeout(r,250));
    const beforeHidden = document.getElementById('wikiPanel').classList.contains('cat-hidden');
    badge.click();                                        // 真实用户路径
    await new Promise(r=>setTimeout(r,350));
    const wp = document.getElementById('wikiPanel');
    const r = wp.getBoundingClientRect();
    return JSON.stringify({
      bound, beforeHidden,
      afterVisible: !wp.classList.contains('cat-hidden'),
      rectW: Math.round(r.width), rectH: Math.round(r.height),
      navActive: (document.querySelector('#modalNav .nav-item[data-panel="wiki"]')||{}).classList ? document.querySelector('#modalNav .nav-item[data-panel="wiki"]').classList.contains('active') : null
    });
  })()`));
  console.log('    ', JSON.stringify(fn));
  check('徽章仍绑有 onclick（status_poll.js → 暴露入口链路完好）', fn.bound === true, fn.bound);
  check('点击后 wikiPanel 真正展开（可见且非 cat-hidden）', fn.afterVisible === true && fn.rectW > 0, {visible:fn.afterVisible, w:fn.rectW, h:fn.rectH});

  console.log('\n' + '='.repeat(74));
  console.log(fails===0 ? '✅ 词法作用域可见性零变化，且功能完好' : `❌ 失败 ${fails} 项`);
  console.log('='.repeat(74));
  ws.close(); cleanup();
  process.exit(fails?1:0);
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
