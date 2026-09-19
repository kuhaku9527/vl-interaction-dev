#!/usr/bin/env node
/**
 * 渲染实测：知识库徽章点击是否真能展开知识库面板
 * 复刻 scripts/audit-frontend-residue.mjs 的 CDP 取证方式（Chrome headless + CDP）。
 * 需先起 8123 静态服务。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';

const CDP = Number(process.env.CDP_PORT || 10033);   // 可用 CDP_PORT 覆盖，避免与其它取证脚本抢端口
const PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), 'wikibadge-'));
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars',`--remote-debugging-port=${CDP}`,`--user-data-dir=${PROFILE}`,'--window-size=1600,1000','about:blank'],{stdio:'ignore'});
const get=p=>new Promise((res,rej)=>{http.get({host:'127.0.0.1',port:CDP,path:p},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)});
let _id=0;const cdp=(ws,m,p={})=>new Promise((res,rej)=>{const id=++_id;const h=e=>{const x=JSON.parse(e.data.toString());if(x.id===id){ws.removeEventListener('message',h);x.error?rej(new Error(JSON.stringify(x.error))):res(x.result)}};ws.addEventListener('message',h);ws.send(JSON.stringify({id,method:m,params:p}))});
const cleanup=()=>{try{chrome.kill()}catch{};try{fs.rmSync(PROFILE,{recursive:true,force:true})}catch{}};

let fails = 0;
const check = (name, ok, detail) => { console.log(`${ok?'  ✅':'  ❌'} ${name}${detail!==undefined?`  → ${JSON.stringify(detail)}`:''}`); if(!ok) fails++; };

const main = async () => {
  for(let i=0;i<80;i++){try{await get('/json/version');break}catch{await new Promise(r=>setTimeout(r,300))}}
  const t=(await get('/json/list')).find(x=>x.type==='page');
  const ws=new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',j)});
  await cdp(ws,'Runtime.enable');await cdp(ws,'Page.enable');
  await cdp(ws,'Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
  await cdp(ws,'Page.navigate',{url:'http://127.0.0.1:8123/index.html'});
  await new Promise(r=>setTimeout(r,5500));
  const ev=async e=>{const r=await cdp(ws,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true}); if(r.exceptionDetails) throw new Error('page exception: '+JSON.stringify(r.exceptionDetails.exception?.description||r.exceptionDetails)); return r.result.value;};
  await ev('window.alert=function(){}');

  console.log('='.repeat(70));
  console.log('渲染实测：知识库徽章点击展开知识库面板');
  console.log('='.repeat(70));

  // 0) 前置事实：showSettingsPanel 是否可全局取用（决定实现走哪条路径）
  const scope = JSON.parse(await ev(`JSON.stringify({
    typeofShowSettingsPanel: typeof showSettingsPanel,
    typeofWindowShowSettingsPanel: typeof window.showSettingsPanel,
    typeofJoySettingsNav: typeof window.JoySettingsNav,
    joyNavShow: typeof (window.JoySettingsNav && window.JoySettingsNav.showSettingsPanel),
    navWikiItem: !!document.querySelector('#modalNav .nav-item[data-panel="wiki"]'),
    navItemCount: document.querySelectorAll('#modalNav .nav-item').length
  })`));
  console.log('\n[0] 作用域前置事实:', JSON.stringify(scope));
  console.log('     （裸 showSettingsPanel 与 window.showSettingsPanel 均为 function：IIFE 内的' +
              '函数声明已由 ' + '"window.showSettingsPanel = showSettingsPanel" 加法暴露）');
  check('前置：window.showSettingsPanel 已加法暴露为 function', scope.typeofWindowShowSettingsPanel === 'function', scope.typeofWindowShowSettingsPanel);
  check('前置：window.JoySettingsNav.showSettingsPanel 已暴露', scope.joyNavShow === 'function', scope.joyNavShow);
  check('前置：#modalNav 的 wiki 导航项存在', scope.navWikiItem === true, scope.navWikiItem);

  // 0b) 证明 window.showSettingsPanel 真的可用（直接调用看它是否切换面板）
  const direct = JSON.parse(await ev(`(() => {
    const wp = document.getElementById('wikiPanel');
    window.showSettingsPanel('wiki');
    const wikiShown = !wp.classList.contains('cat-hidden');
    window.showSettingsPanel('services');
    const wikiHiddenAgain = wp.classList.contains('cat-hidden');
    return JSON.stringify({ wikiShown, wikiHiddenAgain, type: typeof window.showSettingsPanel });
  })()`));
  console.log('[0b] window.showSettingsPanel 直调:', JSON.stringify(direct));
  check('直调 window.showSettingsPanel("wiki") 有效', direct.wikiShown === true, direct);
  check('直调可切回 services（幂等/可逆）', direct.wikiHiddenAgain === true, direct);

  // 1) 注入确定的 extended-status 响应，把 wiki 置为「已启用但异常」→ 这正是绑 onclick 的唯一分支
  console.log('\n[1] 注入 /api/services/extended-status（wiki: enabled=true, ok=false）');
  await ev(`(() => {
    const real = window.fetch.bind(window);
    window.fetch = function (input, init) {
      const u = typeof input === 'string' ? input : (input && input.url) || '';
      if (u.includes('/api/services/extended-status')) {
        return Promise.resolve(new Response(JSON.stringify({
          memory: { enabled: true, reachable: true, ok: true },
          wiki:   { enabled: true, reachable: true, ok: false, reason: 'verify-probe' }
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return real(input, init);
    };
    return 'patched';
  })()`);

  // 2) 触发轮询（pollExtendedStatus 是 JoyStatusPoll 的公开导出）
  console.log('\n[2] 调用 window.JoyStatusPoll.pollExtendedStatus()');
  const exported = await ev('typeof (window.JoyStatusPoll && window.JoyStatusPoll.pollExtendedStatus)');
  check('pollExtendedStatus 已导出', exported === 'function', exported);
  await ev('window.JoyStatusPoll.pollExtendedStatus()');
  await new Promise(r=>setTimeout(r,400));

  const badge = JSON.parse(await ev(`(() => {
    const el = document.getElementById('wikiBadge');
    return JSON.stringify({
      text: el.textContent, cls: el.className,
      cursor: el.style.cursor, hasOnclick: typeof el.onclick === 'function',
      title: el.title
    });
  })()`));
  console.log('    wikiBadge:', JSON.stringify(badge));
  check('徽章进入「异常」态且已绑 onclick', badge.hasOnclick === true && badge.cursor === 'pointer', badge);

  // 2b) 关键：证明确实走 showSettingsPanel 主路径，而不是导航项 click 兜底。
  //     必须在 onclick 绑定之后注入插桩，再重新 poll 一次以重建 onclick（否则
  //     计数器挂在旧闭包上，会得到 viaShow=0 的假阴性）。
  const whichPath = JSON.parse(await ev(`(async () => {
    const origShow = window.showSettingsPanel;
    const origJoyShow = window.JoySettingsNav.showSettingsPanel;
    let viaShow = 0, viaNavClick = 0;
    window.showSettingsPanel = function (k, s) { if (k === 'wiki') viaShow++; return origShow.call(this, k, s); };
    window.JoySettingsNav.showSettingsPanel = window.showSettingsPanel;
    const navItem = document.querySelector('#modalNav .nav-item[data-panel="wiki"]');
    const origNavClick = navItem.click.bind(navItem);
    navItem.click = function () { viaNavClick++; return origNavClick(); };
    // 重新 poll → renderExtBadge 重新赋值 el.onclick → 新闭包读到插桩后的 window.showSettingsPanel
    await window.JoyStatusPoll.pollExtendedStatus();
    await new Promise(r=>setTimeout(r,150));
    origShow('services');                       // 复位（原始引用，不计数）
    const wp = document.getElementById('wikiPanel');
    const beforeHidden = wp.classList.contains('cat-hidden');
    document.getElementById('wikiBadge').click();
    await new Promise(r=>setTimeout(r,250));
    const wikiVisible = !wp.classList.contains('cat-hidden');
    window.showSettingsPanel = origShow;
    window.JoySettingsNav.showSettingsPanel = origJoyShow;
    navItem.click = origNavClick;
    return JSON.stringify({ viaShowSettingsPanel: viaShow, viaNavItemClick: viaNavClick, beforeHidden, wikiVisible });
  })()`));
  console.log('[2b] 路径归属（插桩后重绑 onclick 再点击）:', JSON.stringify(whichPath));
  check('复位后 wikiPanel 确实隐藏（点击前基线）', whichPath.beforeHidden === true, whichPath.beforeHidden);
  check('实现走的是 showSettingsPanel("wiki") 主路径', whichPath.viaShowSettingsPanel >= 1, whichPath);
  check('未退化为导航项 click 兜底路径', whichPath.viaNavItemClick === 0, whichPath.viaNavItemClick);
  check('主路径下 wikiPanel 确实展开', whichPath.wikiVisible === true, whichPath.wikiVisible);

  // 3) 记录点击前面板状态（先把 [2b] 遗留的展开态复位，保证是干净基线）
  await ev(`(() => {
    document.getElementById('settingsModal').classList.remove('show');
    window.showSettingsPanel('services');
    return 'reset';
  })()`);
  await new Promise(r=>setTimeout(r,250));
  const before = JSON.parse(await ev(`(() => {
    const wp = document.getElementById('wikiPanel');
    const sp = document.getElementById('servicesPanel');
    const ni = document.querySelector('#modalNav .nav-item[data-panel="wiki"]');
    return JSON.stringify({
      settingsShown: document.getElementById('settingsModal').classList.contains('show'),
      wikiRootHidden: wp.classList.contains('cat-hidden'),
      wikiPanelVisible: wp.getBoundingClientRect().width > 0,
      servicesRootHidden: sp.classList.contains('cat-hidden'),
      navWikiActive: ni ? ni.classList.contains('active') : null
    });
  })()`));
  console.log('\n[3] 点击前:', JSON.stringify(before));
  check('点击前 wikiPanel 处于隐藏（cat-hidden）', before.wikiRootHidden === true, before.wikiRootHidden);
  check('点击前 wikiPanel 不可见（宽度 0）', before.wikiPanelVisible === false, before.wikiPanelVisible);

  // 4) 真实派发 click 事件（走用户路径，不是直接调 onclick）
  console.log('\n[4] 真实派发 click 到 #wikiBadge');
  await ev(`document.getElementById('wikiBadge').click()`);
  await new Promise(r=>setTimeout(r,500));

  const after = JSON.parse(await ev(`(() => {
    const wp = document.getElementById('wikiPanel');
    const sp = document.getElementById('servicesPanel');
    const ni = document.querySelector('#modalNav .nav-item[data-panel="wiki"]');
    const r = wp.getBoundingClientRect();
    const cs = getComputedStyle(wp);
    return JSON.stringify({
      settingsShown: document.getElementById('settingsModal').classList.contains('show'),
      wikiRootHidden: wp.classList.contains('cat-hidden'),
      wikiPanelVisible: r.width > 0,
      wikiPanelRect: { w: Math.round(r.width), h: Math.round(r.height) },
      wikiPanelDisplay: cs.display,
      wikiPanelOffsetParent: wp.offsetParent !== null,
      servicesRootHidden: sp.classList.contains('cat-hidden'),
      navWikiActive: ni ? ni.classList.contains('active') : null,
      knowledgeBaseSection: (() => {
        const kb = document.getElementById('knowledgeBase');
        if (!kb) return null;
        const kr = kb.getBoundingClientRect();
        return { present: true, inWikiPanel: wp.contains(kb), visible: kr.width > 0, h: Math.round(kr.height) };
      })()
    });
  })()`));
  console.log('[5] 点击后:', JSON.stringify(after, null, 2));

  console.log('');
  check('设置弹窗已打开', after.settingsShown === true, after.settingsShown);
  check('#wikiPanel 已解除 cat-hidden', after.wikiRootHidden === false, after.wikiRootHidden);
  check('#wikiPanel 实际可见（宽度 > 0）', after.wikiPanelVisible === true, after.wikiPanelRect);
  check('#wikiPanel 已渲染（offsetParent 非空）', after.wikiPanelOffsetParent === true, after.wikiPanelOffsetParent);
  check('#servicesPanel 已随之隐藏（面板互斥）', after.servicesRootHidden === true, after.servicesRootHidden);
  check('左侧导航 wiki 项高亮', after.navWikiActive === true, after.navWikiActive);
  check('知识库正文 #knowledgeBase 在 wikiPanel 内且可见', !!after.knowledgeBaseSection && after.knowledgeBaseSection.inWikiPanel && after.knowledgeBaseSection.visible, after.knowledgeBaseSection);

  // 6) 反向对照：先真复位（点导航 services 项），再用「旧实现」等价路径验证
  console.log('\n[6] 反向对照：真复位后，用「旧实现」等价路径（#knowledgeBaseToggle 恒 null）验证');
  const control = JSON.parse(await ev(`(() => {
    document.getElementById('settingsModal').classList.remove('show');
    // 真复位：走 UI 路径把面板切回 services（showSettingsPanel 不是全局，不能裸调）
    const navServices = document.querySelector('#modalNav .nav-item[data-panel="services"]');
    if (navServices) navServices.click();
    const wp = document.getElementById('wikiPanel');
    const beforeHidden = wp.classList.contains('cat-hidden');
    // 旧实现的核心一步：document.getElementById('knowledgeBaseToggle')
    const kb = document.getElementById('knowledgeBaseToggle');
    if (kb) kb.click();
    const stillHidden = wp.classList.contains('cat-hidden');
    return JSON.stringify({ toggleExists: !!kb, beforeHidden, afterOldPathStillHidden: stillHidden });
  })()`));
  console.log('    ', JSON.stringify(control));
  check('对照：复位后 wikiPanel 确实重新隐藏', control.beforeHidden === true, control.beforeHidden);
  check('对照：#knowledgeBaseToggle 确不存在（旧实现恒静默跳过）', control.toggleExists === false, control.toggleExists);
  check('对照：旧路径下 wikiPanel 仍然隐藏（证明原为静默失效）', control.afterOldPathStillHidden === true, control);

  // 7) 再点一次：连续点击不应抛错/破坏状态
  console.log('\n[7] 幂等性：再次点击徽章');
  const err = await ev(`(() => {
    try { document.getElementById('wikiBadge').click(); document.getElementById('wikiBadge').click();
      const wp = document.getElementById('wikiPanel');
      return JSON.stringify({ ok: true, wikiVisible: wp.getBoundingClientRect().width > 0 });
    } catch (e) { return JSON.stringify({ ok: false, err: String(e) }); }
  })()`);
  console.log('    ', err);
  check('重复点击不抛错且面板仍展开', JSON.parse(err).ok === true && JSON.parse(err).wikiVisible === true, err);

  console.log('\n' + '='.repeat(70));
  console.log(fails === 0 ? '✅ 渲染实测全部通过' : `❌ 渲染实测失败 ${fails} 项`);
  console.log('='.repeat(70));
  ws.close(); cleanup();
  process.exit(fails ? 1 : 0);
};
main().catch(e=>{console.error('FAILED:',e.message);cleanup();process.exit(2)});
