#!/usr/bin/env node
/** 截图设置页「外观 → Live 常驻模式」区块，人工确认排版。 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const CDP = 10016, OUT = 'scripts/.fs-inspect';
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'shot-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
fs.mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,1000','about:blank'], { stdio:'ignore' });
const get = p => new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0; const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
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
// 打开设置 → 外观 → 展开 Live 区块
await ev(`document.getElementById('settingsBtn').click()`);
await new Promise(r=>setTimeout(r,700));
await ev(`document.querySelector('#modalNav .nav-item[data-panel="appearance"]').click()`);
await new Promise(r=>setTimeout(r,500));
await ev(`(()=>{const s=document.getElementById('liveModeSection'); s.classList.remove('collapsed');
  // 显示预设菜单以便一并确认排版（默认 hidden）
  const m=document.getElementById('promptPresetMenu'); if(m) m.classList.remove('hidden');
  s.scrollIntoView({block:'center'});})()`);
await new Promise(r=>setTimeout(r,700));
// 精确裁剪到 Live 区块
const box = JSON.parse(await ev(`(()=>{const r=document.getElementById('liveModeSection').getBoundingClientRect();
  return JSON.stringify({x:Math.max(0,r.x-8),y:Math.max(0,r.y-8),w:r.width+16,h:Math.min(r.height+16, 1000-Math.max(0,r.y-8))})})()`));
const r = await cdp(ws,'Page.captureScreenshot',{format:'png',clip:{x:box.x,y:box.y,width:box.w,height:box.h,scale:1}});
fs.writeFileSync(path.join(OUT,'G-settings-live.png'), Buffer.from(r.data,'base64'));
console.log('截图: scripts/.fs-inspect/G-settings-live.png  区块', JSON.stringify(box));
// 同时输出该区块内所有控件的几何，供客观判定
console.log(await ev(`(()=>{const out=[];
 document.querySelectorAll('#liveModeSection .chat-prompt-action, #liveModeSection select, #liveModeSection input, #liveModeSection .prompt-preset-option').forEach(e=>{
  const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
  if(r.width===0||r.height===0) return;
  out.push((e.id||e.className.split(' ')[0]).padEnd(24)+' '+Math.round(r.width)+'x'+Math.round(r.height)+'  disp='+cs.display);});
 return out.join('\\n')})()`));
ws.close();try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}
