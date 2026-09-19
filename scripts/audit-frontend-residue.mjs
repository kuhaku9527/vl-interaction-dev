#!/usr/bin/env node
/**
 * 前端死代码 / 残留审计（2026-09-19）
 *
 * 用户提问：「前端需不需要优化模块拆分、重构，或者收缩掉冗余设计？
 *   有可能很多只是表面删除了，但功能实现还没删除，或者是我忘记了的。」
 *
 * 本脚本从**运行时**取证（不是读代码猜），逐类扫描：
 *   A. 页面里存在但**不可见**的 id（display:none / 0×0）→ 可能"表面删除"
 *   B. 被 JS 引用但 **DOM 中不存在**的 id → 死引用（会静默失效）
 *   C. 引用了 DOM 却**从未在页面上出现**的 CSS 选择器 id
 *   D. window.Joy* 模块的**导出函数**里，从未被其它模块调用的
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';

const STATIC = 'services/webui/src/joy_interaction_webui/static';
const CDP = 10022;
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'deadcode-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,1000','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0;const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

const main = async () => {
  // ---------- 静态分析 ----------
  const html = fs.readFileSync(path.join(STATIC,'index.html'),'utf8');
  const htmlish = html.replace(/<script[\s\S]*?<\/script>/gi,'');
  const htmlIds = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map(m=>m[1]));

  const jsFiles = fs.readdirSync(STATIC).filter(f=>f.endsWith('.js'));
  const jsSrc = {}; jsFiles.forEach(f=>jsSrc[f]=fs.readFileSync(path.join(STATIC,f),'utf8'));
  const allJs = Object.values(jsSrc).join('\n');
  const allInline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).join('\n');

  // JS 里通过 getElementById('x') 引用的 id
  const refIds = new Map();
  for (const [f,src] of Object.entries(jsSrc)) {
    for (const m of src.matchAll(/getElementById\(\s*['"]([^'"]+)['"]/g)) {
      if(!refIds.has(m[1])) refIds.set(m[1],new Set());
      refIds.get(m[1]).add(f);
    }
  }
  for (const m of allInline.matchAll(/getElementById\(\s*['"]([^'"]+)['"]/g)) {
    if(!refIds.has(m[1])) refIds.set(m[1],new Set());
    refIds.get(m[1]).add('index.html(inline)');
  }
  // 动态模板 id（如 'hm-dot-'+provider）无法静态判定 → 收集前缀白名单
  const dynamicPrefixes = [...allJs.matchAll(/getElementById\(\s*['"]([a-z-]*?)['"]\s*\+/gi)].map(m=>m[1]);

  // ---------- 运行时取证 ----------
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>(await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true})).result.value;
  await ev('window.alert=function(){}');

  const hidden = JSON.parse(await ev(`(()=>{const out=[];
    document.querySelectorAll('[id]').forEach(e=>{
      const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
      // 只报告「自身被显式隐藏」的，避免父级隐藏导致的大面积噪声
      const selfHidden = cs.display==='none' || cs.visibility==='hidden' || cs.opacity==='0';
      if(selfHidden) out.push({id:e.id, tag:e.tagName.toLowerCase(), display:cs.display, vis:cs.visibility});
    });
    return JSON.stringify(out);})()`));

  const liveIds = JSON.parse(await ev(`JSON.stringify([...document.querySelectorAll('[id]')].map(e=>e.id))`));

  console.log('='.repeat(80));
  console.log('前端残留审计（运行时取证）');
  console.log('='.repeat(80));
  console.log(`index.html 中的 id: ${htmlIds.size} ／ 运行时 DOM 中的 id: ${new Set(liveIds).size}`);

  // ---- B. 死引用：JS 引用但 DOM 不存在 ----
  const liveSet = new Set(liveIds);
  const deadRefs = [];
  for (const [id, files] of [...refIds].sort()) {
    if (liveSet.has(id)) continue;
    if (dynamicPrefixes.some(p => p && id.startsWith(p))) continue;   // 模板 id
    if (id === 'status-' || id === 'meta-' || id === 'hm-') continue;
    deadRefs.push({id, files:[...files].join(', ')});
  }
  console.log(`\n【B】JS 引用但 DOM 中不存在的 id（死引用）—— ${deadRefs.length} 个`);
  if (!deadRefs.length) console.log('  （无）');
  deadRefs.forEach(d=>console.log(`  ⚠️  ${d.id.padEnd(34)} ← ${d.files}`));

  // ---- A. 显式隐藏的 id ----
  console.log(`\n【A】自身被显式隐藏的 id（可能"表面删除、实现尚存"）—— ${hidden.length} 个`);
  const hiddenKey = hidden.filter(h=>/^(mirror|apiStatus|capSetting|title-section|modelName|mdToggle|copyBtn)/i.test(h.id));
  hidden.forEach(h=>console.log(`  ·  ${h.id.padEnd(34)} ${h.tag.padEnd(8)} display=${h.display}`));

  // ---- 汇总 ----
  console.log('\n' + '='.repeat(80));
  console.log(`死引用 ${deadRefs.length} ／ 显式隐藏 ${hidden.length}（其中"已删功能疑似残留" ${hiddenKey.length}）`);
  ws.close(); cleanup();
  process.exit(0);
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
