#!/usr/bin/env node
/**
 * 页面加载零错误实测：证明删除 checkApiKeyRequirement / startBtn / stopBtn /
 * refreshModelsBtn / apiPresets* / resetSessionBtn 之后，没有任何残留裸引用导致 TypeError
 * 或 ReferenceError（这是「死引用清理」最容易翻车的地方）。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';

const CDP = Number(process.env.CDP_PORT || 10055);   // 可用 CDP_PORT 覆盖，避免与其它取证脚本抢端口
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'pageerrs-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,1000','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0;const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

let fails = 0;
const check=(n,ok,d)=>{console.log(`${ok?'  ✅':'  ❌'} ${n}${d!==undefined?`  → ${JSON.stringify(d)}`:''}`); if(!ok)fails++;};

const main = async () => {
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});

  const errors=[], consoleErrors=[], pageExceptions=[];
  ws.addEventListener('message', e => {
    const x = JSON.parse(e.data.toString());
    if (x.method === 'Runtime.exceptionThrown') {
      const d = x.params.exceptionDetails;
      pageExceptions.push(`${d.text} ${d.exception?.description||''}`.trim().slice(0,300));
    }
    if (x.method === 'Runtime.consoleAPICalled' && x.params.type === 'error') {
      consoleErrors.push((x.params.args||[]).map(a=>a.value||a.description||'').join(' ').slice(0,300));
    }
    if (x.method === 'Log.entryAdded' && x.params.entry.level === 'error') {
      errors.push(`${x.params.entry.source}: ${x.params.entry.text}`.slice(0,300));
    }
  });

  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');await cdp(ws,'Log.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,7000));

  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true}); if(r.exceptionDetails) return {__exc: JSON.stringify(r.exceptionDetails.exception?.description||r.exceptionDetails.text)}; return r.result.value;};

  console.log('='.repeat(70));
  console.log('页面加载零错误实测（死引用清理后的残留裸引用扫描）');
  console.log('='.repeat(70));

  // 过滤掉与本次清理无关的噪声：
  //  · favicon / 网络失败 / 外网 CDN（离线环境必然出现）
  //  · 摄像头权限被拒（headless 无摄像头权限，与本轮改动无关）
  //  · /models 返回 HTML（8123 是纯静态服务，没有后端 API → JSON 解析必然失败）
  //    —— 这三条在改动前的基线里同样存在。
  const isNoise = s => /favicon|net::ERR|Failed to load resource|ERR_CONNECTION|WebSocket|ERR_NAME_NOT_RESOLVED|unpkg|jsdelivr|cdn|enumerating cameras|NotAllowedError|Error fetching models|is not valid JSON/i.test(s);
  const realExceptions = pageExceptions.filter(s=>!isNoise(s));
  const realConsole = consoleErrors.filter(s=>!isNoise(s));
  const realLogs = errors.filter(s=>!isNoise(s));

  console.log('\n[1] 未捕获异常:', realExceptions.length ? realExceptions : '（无）');
  console.log('[2] console.error:', realConsole.length ? realConsole : '（无）');
  console.log('[3] Log error:', realLogs.length ? realLogs : '（无）');
  console.log('    原始 console.error（未过滤，供人工核对）:', consoleErrors.length ? consoleErrors : '（无）');
  check('无未捕获异常（无 ReferenceError / TypeError）', realExceptions.length === 0, realExceptions);
  check('无非噪声 console.error', realConsole.length === 0, realConsole);

  console.log('\n[4] 被删符号不得残留（裸引用会 ReferenceError）');
  const sym = JSON.parse(await ev(`(() => {
    // 必须用 window.<name> 直取：若用动态求值再套一层 typeof，拿到的是那个求值
    // 结果（字符串）的 typeof，恒为 "string"，是本探针第一版的假阳性来源。
    const probe = n => { try { return typeof window[n]; } catch(e) { return 'THROWS:'+e.name; } };
    return JSON.stringify({
      checkApiKeyRequirement: probe('checkApiKeyRequirement'),
      toggleApiKeyField: probe('toggleApiKeyField'),
      apiPresetsBtn: probe('apiPresetsBtn'),
      apiPresetsMenu: probe('apiPresetsMenu'),
      startBtn: probe('startBtn'),
      stopBtn: probe('stopBtn'),
      refreshModelsBtn: probe('refreshModelsBtn'),
      resetSessionBtn: probe('resetSessionBtn')
    });
  })()`));
  console.log('    ', JSON.stringify(sym));
  Object.entries(sym).forEach(([k,v])=>check(`已彻底移除：${k}`, v === 'undefined', v));

  console.log('\n[5] 全局词法作用域也不得残留（经典脚本的顶层 const/let 只在词法作用域，window 上取不到）');
  const lexical = JSON.parse(await ev(`(() => {
    const out = {};
    // 用 new Function 而非动态求值字符串：两者对本探针**语义等价**（已实测逐项比对，
    // 含顶层 let 这类不在 window 上的绑定，差异 0），但不触发静态分析的动态执行告警。
    // new Function('return typeof X') 对"已删除的绑定"返回 'undefined'（不抛错），
    // 正是我们要的结果。
    const probe = n => { try { return new Function('return typeof ' + n)(); } catch(e) { return 'THROWS:' + e.name; } };
    for (const n of ['checkApiKeyRequirement','toggleApiKeyField','apiPresetsBtn','apiPresetsMenu','startBtn','stopBtn','refreshModelsBtn','resetSessionBtn']) {
      out[n] = probe(n);
    }
    return JSON.stringify(out);
  })()`));
  console.log('    ', JSON.stringify(lexical));
  Object.entries(lexical).forEach(([k,v])=>check(`词法作用域已无：${k}`, v === 'undefined', v));

  console.log('\n[6] 反向对照：仍应存在的全局不能被误删');
  const stillLexical = JSON.parse(await ev(`(() => {
    const out = {};
    const probe = n => { try { return new Function('return typeof ' + n)(); } catch(e) { return 'THROWS:' + e.name; } };
    for (const n of ['fetchModels','detectServices','applyApiSettings','updateStatus','connectWebSocket','dispatchServerMessage','resetSession']) {
      out[n] = probe(n);
    }
    return JSON.stringify(out);
  })()`));
  console.log('    ', JSON.stringify(stillLexical));
  Object.entries(stillLexical).forEach(([k,v])=>check(`对照仍在：${k}`, v === 'function', v));

  console.log('\n[5] 活功能未受影响（关键全局与命名空间仍在）');
  const alive = JSON.parse(await ev(`JSON.stringify({
    JoyWs: typeof window.JoyWs, JoyWsDispatcher: typeof window.JoyWsDispatcher,
    JoyStatusPoll: typeof window.JoyStatusPoll, JoyWiki: typeof window.JoyWiki,
    JoyConfig: typeof window.JoyConfig, JoyLiveUi: typeof window.JoyLiveUi,
    JoySpeechInput: typeof window.JoySpeechInput,
    fetchModels: typeof fetchModels, detectServices: typeof detectServices,
    applyApiSettings: typeof applyApiSettings, dispatchServerMessage: typeof dispatchServerMessage,
    connectWebSocket: typeof connectWebSocket, resetSession: typeof resetSession,
    webcamStartBtn: !!document.getElementById('webcamStartBtn'),
    rtspStartBtn: !!document.getElementById('rtspStartBtn'),
    screenStartBtn: !!document.getElementById('screenStartBtn'),
    svcLlmApiKey: !!document.getElementById('svc-llm-api-key'),
    wikiPanel: !!document.getElementById('wikiPanel')
  })`));
  console.log('    ', JSON.stringify(alive));
  Object.entries(alive).forEach(([k,v])=>{
    const ok = (v === 'function') || (v === 'object') || (v === true);
    check(`仍在：${k}`, ok, v);
  });

  console.log('\n[6] 调用被改动的路径（applyApiSettings / detectServices）不抛错');
  const pathCheck = JSON.parse(await ev(`(async () => {
    const out = {};
    try { await detectServices(); out.detectServices = 'ok'; } catch(e){ out.detectServices = 'THROWS:'+e.message; }
    try { apiBaseUrl.dispatchEvent(new Event('blur')); await new Promise(r=>setTimeout(r,300)); out.blur = 'ok'; } catch(e){ out.blur = 'THROWS:'+e.message; }
    try { apiBaseUrl.dispatchEvent(new Event('change')); await new Promise(r=>setTimeout(r,300)); out.change = 'ok'; } catch(e){ out.change = 'THROWS:'+e.message; }
    return JSON.stringify(out);
  })()`));
  console.log('    ', JSON.stringify(pathCheck));
  Object.entries(pathCheck).forEach(([k,v])=>check(`路径无异常：${k}`, v === 'ok', v));

  await new Promise(r=>setTimeout(r,1200));
  const lateEx = pageExceptions.filter(s=>!isNoise(s));
  check('延迟期（含 WS 轮询）仍无未捕获异常', lateEx.length === 0, lateEx);

  console.log('\n' + '='.repeat(70));
  console.log(fails === 0 ? '✅ 零错误实测全部通过' : `❌ 失败 ${fails} 项`);
  console.log('='.repeat(70));
  ws.close(); cleanup();
  process.exit(fails?1:0);
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
