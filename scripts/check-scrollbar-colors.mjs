#!/usr/bin/env node
/**
 * 验收：输入框滚动条配色（2026-09-19 用户反馈）
 *
 * 用户原话：「它的颜色设计得有问题：因为聊天框背景是灰色的，背景是黑色/白色，
 * 一旦滑动条出现，它又变成白底了/黑底，搞得像是缺了一块一样。」
 *
 * 判据：滚动条 track 的颜色必须与它所在容器背景【同色】，且深/浅两主题都成立。
 * 用 CDP 读 ::-webkit-scrollbar-track 的实际计算样式（这是唯一能取到伪元素样式的方式）。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP = 10150, PORT = 8123, OUT = 'scripts/.fs-inspect';
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'sbv-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars=0',
  `--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,900','about:blank'], { stdio:'ignore' });
const get=p=>new Promise((r,j)=>{http.get({host:'127.0.0.1',port:CDP,path:p},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>r(JSON.parse(d)))}).on('error',j)});
let i=0;const cdp=(ws,m,p={})=>new Promise((r,j)=>{const id=++i;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?j(new Error(JSON.stringify(x.error))):r(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};
let pass=0,fail=0;
const check=(c,l,d='')=>{console.log(`  ${c?'✅':'❌'} ${l}${d?'  '+d:''}`);c?pass++:fail++;};

const main = async () => {
  for(let k=0;k<80;k++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:900,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:`http://127.0.0.1:${PORT}/index.html`});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});
    return r.exceptionDetails?'EXC '+((r.exceptionDetails.exception||{}).description||'').split('\n')[0]:r.result.value;};
  await ev('window.alert=function(){}');

  // 让 textarea 真的溢出（灌多行），否则滚动条不存在
  await ev(`(()=>{const t=document.getElementById('promptText');
    t.value='第一行内容\\n第二行内容\\n第三行内容\\n第四行内容\\n第五行内容\\n第六行内容\\n第七行内容\\n第八行内容';
    t.style.maxHeight='34px'; t.style.overflowY='auto';
    return t.scrollHeight>t.clientHeight ? 'overflow-ok' : 'no-overflow';})()`);
  await new Promise(r=>setTimeout(r,500));

  console.log('=== 输入框滚动条配色（伪元素计算样式）===');
  for (const theme of ['dark','light']) {
    await ev(`document.body.classList.toggle('light-theme', ${theme==='light'})`);
    await new Promise(r=>setTimeout(r,600));
    const d = JSON.parse(await ev(`(()=>{
      const t=document.getElementById('promptText');
      const shell=document.querySelector('.chat-prompt-shell');
      const g=(el,pe)=>{ try{ const cs=getComputedStyle(el,pe); return {bg:cs.backgroundColor, w:cs.width}; }catch(e){ return null; } };
      const btn=document.getElementById('camBtn');
      return JSON.stringify({
        theme:'${theme}',
        scrolls: t.scrollHeight>t.clientHeight,
        shellBg: getComputedStyle(shell).backgroundColor,
        inputBg: getComputedStyle(t).backgroundColor,
        track: g(t,'::-webkit-scrollbar-track'),
        thumb: g(t,'::-webkit-scrollbar-thumb'),
        btnBorder: getComputedStyle(btn).borderColor,
        tokenInput: getComputedStyle(document.documentElement).getPropertyValue('--bg-input').trim(),
        tokenBorderStrong: getComputedStyle(document.documentElement).getPropertyValue('--border-strong').trim()
      });})()`));
    console.log(`\n  【${d.theme}】`);
    console.log(`    输入框会滚动: ${d.scrolls}`);
    console.log(`    容器背景 shell     = ${d.shellBg}`);
    console.log(`    滚动条 track       = ${d.track ? d.track.bg : '(取不到)'}`);
    console.log(`    滚动条 thumb       = ${d.thumb ? d.thumb.bg : '(取不到)'}`);
    console.log(`    旁边按钮 border     = ${d.btnBorder}`);
    check(d.scrolls === true, `${d.theme} 输入框确实出现滚动条（前提成立）`);

    // 判据：track 背景 == 容器背景（同色 → 不再"缺一块"）
    const rgb = (s) => { const m=String(s).match(/\d+/g); return m? m.slice(0,3).join(','):String(s); };
    const sameAsShell = d.track && rgb(d.track.bg) === rgb(d.shellBg);
    check(sameAsShell, `${d.theme} track 与输入框容器**同色**（不再缺一块）`,
      `track=${d.track&&d.track.bg} vs shell=${d.shellBg}`);
    // thumb 不应等于旧 token 的深黑（浅色下最刺眼的那个问题）
    const thumbBg = d.thumb && d.thumb.bg;
    const isOldDark = rgb(thumbBg) === '18,16,16' || rgb(thumbBg) === '27,21,21';
    check(!isOldDark, `${d.theme} thumb 未沿用旧 token（#121010/#1b1515）`, String(thumbBg));
    if (d.theme === 'light') {
      // 浅色下 track 必须是「浅」的（亮度高）
      const m = String(d.track.bg).match(/\d+/g) || [];
      const bright = m.length>=3 ? (Number(m[0])+Number(m[1])+Number(m[2]))/3 : 0;
      check(bright > 150, '浅色主题下 track 是浅色（修掉"近黑底"）', `平均亮度 ${Math.round(bright)}`);
    }
    const shot = await cdp(ws,'Page.captureScreenshot',{format:'png',
      clip:{x:0,y:800,width:900,height:100,scale:2}});
    fs.writeFileSync(path.join(OUT,`scrollbar-${d.theme}.png`), Buffer.from(shot.data,'base64'));
    console.log(`    截图: scrollbar-${d.theme}.png`);
  }
  ws.close(); cleanup();
  console.log(`\n${'='.repeat(52)}\n通过 ${pass} / 失败 ${fail}`);
  process.exit(fail?1:0);
};
main().catch(e=>{console.error('FAILED:', e && (e.stack||e.message) || e); cleanup(); process.exit(2)});
