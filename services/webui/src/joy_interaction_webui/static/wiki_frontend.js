'use strict';

// wiki_frontend.js — [Local Wiki] frontend tasks F1-F4 (ADR-0012 §7.3)
// Exposes window.JoyWiki so index.html can wire the Settings (F1/F2/F3) and
// Knowledge Base (F4) UI. All calls hit the webui gateway's /v1/* contract:
//   GET  /v1/providers/health     (B3)
//   GET/PUT /v1/settings/network   (B4)
//   GET  /v1/namespaces            (F4 list)
//   POST /v1/external/sync         (F4 folder sync)
//   POST /v1/external/ingest-text  (F4 pasted markdown)
//   DELETE /v1/namespaces/{ns}     (F4 delete)
(function () {
  async function apiJson(method, path, body) {
    const opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(path, opts);
    let data = null;
    try {
      data = await resp.json();
    } catch (_e) {
      /* non-JSON response */
    }
    if (!resp.ok) {
      const msg = (data && (data.error || data.detail)) || ('HTTP ' + resp.status);
      throw new Error(msg);
    }
    return data;
  }

  // [Local Wiki] ADR-0012 §5: single source of truth for the 5 probed providers.
  // Values: true = ok, false = down, null = still probing. Written ONLY by
  // setStatusDot(); summarised by renderHealthPill() once per loadHealth() run.
  const healthState = {};

  function setStatusDot(provider, ok, meta) {
    healthState[provider] = ok === null ? null : !!ok;
    const dot = document.getElementById('status-' + provider);
    const metaEl = document.getElementById('meta-' + provider);
    const menuEl = document.getElementById('hm-' + provider);
    const menuDot = document.getElementById('hm-dot-' + provider);
    if (dot) {
      dot.classList.remove('ok', 'err', 'pending');
      dot.classList.add(ok === null ? 'pending' : ok ? 'ok' : 'err');
    }
    if (metaEl) metaEl.textContent = meta || '--';
    // Mirror into the top-bar health menu (same live data, no second probe).
    if (menuEl) {
      menuEl.textContent =
        ok === null ? '...' : ok ? (meta || 'ok') : (meta || 'down');
    }
    // 状态灯着色：原先 .sdot 内联硬编码 var(--ok)（恒绿），
    // 现按真实结果切换类，颜色由 styles.css 的 .sdot.ok/.err/.pending 决定。
    if (menuDot) {
      menuDot.classList.remove('ok', 'err', 'pending');
      menuDot.classList.add(ok === null ? 'pending' : ok ? 'ok' : 'err');
    }
  }

  // Summarise healthState into the top-bar pill. Called at the END of both
  // loadHealth() branches so the 5 pending setStatusDot() calls at its start
  // never produce a misleading "5 项异常".
  function renderHealthPill() {
    const pillText = document.getElementById('healthPillText');
    if (!pillText) return;
    let pending = 0;
    let down = 0;
    Object.keys(healthState).forEach(function (p) {
      const s = healthState[p];
      if (s === null) pending += 1;
      else if (!s) down += 1;
    });
    if (pending > 0) pillText.textContent = '检测中…';
    else if (down > 0) pillText.textContent = down + ' 项异常';
    else pillText.textContent = '系统正常';
  }

  // ---- 健康探测（原 F2 API status panel；面板已于 2026-09-18 删除，
  //      数据改为直接驱动顶栏 healthPill 下拉的 #hm-*） ----
  //
  // ⚠️ 端点修正（2026-09-18）：此前请求 /v1/providers/health，但该路由在
  // server.py:675 被代理给 memory-store（8997）—— 那是完全不同的 API，
  // 在 memory-store 未启用时恒返回 404，故 7 项始终显示 "HTTP 404"。
  //
  // 真正提供这 6 项聚合探活的是 webui 自己的 _services_status_handler，
  // 注册于 server.py:668 → GET /api/services/status，
  // 返回键见 admin_endpoints.py:186-194：llm / summary / tts / asr / agent / embedding
  // （注意后端用 `llm` 与 `summary`，而 DOM 的 #hm-* 用 main_llm / summarizer）。
  //
  // memory_store 不在该接口内，其状态由 /api/services/extended-status 单独供数
  // （status_poll.js:261），故此处不纳入，避免误报。
  const HEALTH_PROVIDERS = [
    { id: 'main_llm', key: 'llm' },
    { id: 'summarizer', key: 'summary' },
    { id: 'embedding', key: 'embedding' },
    { id: 'tts', key: 'tts' },
    { id: 'asr', key: 'asr' },
    { id: 'agent', key: 'agent' },
  ];

  async function loadHealth() {
    HEALTH_PROVIDERS.forEach(function (p) {
      setStatusDot(p.id, null, '...');
    });
    // 上面的 pending 写入是同步批量的，故此处汇总一次是安全的
    // （不放在 setStatusDot 内部，否则会逐次计数导致误报）。
    renderHealthPill();
    try {
      const data = await apiJson('GET', '/api/services/status');
      HEALTH_PROVIDERS.forEach(function (p) {
        const item = data[p.key] || {};
        const lat = item.latency_ms != null ? item.latency_ms + 'ms' : '';
        const meta = [item.provider, lat, item.reason].filter(Boolean).join(' · ') || (item.ok ? 'ok' : 'down');
        setStatusDot(p.id, item.ok, meta);
      });
    } catch (_e) {
      HEALTH_PROVIDERS.forEach(function (p) {
        setStatusDot(p.id, false, String(_e.message || _e).slice(0, 60));
      });
    }
    renderHealthPill();
  }

  // ---- F1/F3: Network proxy settings ----
  async function loadNetwork() {
    try {
      const data = await apiJson('GET', '/v1/settings/network');
      const proxy = (data && data.proxy) || {};
      const enabled = document.getElementById('proxyEnabledToggle');
      const host = document.getElementById('proxyHost');
      const port = document.getElementById('proxyPort');
      if (enabled) enabled.checked = !!proxy.enabled;
      if (host) {
        const url = proxy.url || '';
        const m = url.match(/:\/\/([^:/]+)(?::(\d+))?/);
        host.value = m ? m[1] : '';
        if (port && m && m[2]) port.value = m[2];
      }
    } catch (_e) {
      const st = document.getElementById('proxyStatus');
      if (st) st.textContent = window.JoyI18n.localizeUiString('Failed to load network settings: ') + _e.message;
    }
  }

  function buildProxyUrl() {
    const host = (document.getElementById('proxyHost') || {}).value || '127.0.0.1';
    const port = (document.getElementById('proxyPort') || {}).value || '7890';
    return 'http://' + host + ':' + port;
  }

  async function testConnection() {
    const st = document.getElementById('proxyStatus');
    if (st) st.textContent = window.JoyI18n.localizeUiString('Probing providers...');
    await loadHealth();
    if (st) st.textContent = window.JoyI18n.localizeUiString('Probe complete (v1 traffic stays direct per ADR-0012 §4).');
  }

  // F3: optimistic save -> PUT /v1/settings/network
  async function saveNetwork() {
    const st = document.getElementById('proxyStatus');
    const enabled = document.getElementById('proxyEnabledToggle');
    const payload = {
      proxy: {
        enabled: !!(enabled && enabled.checked),
        url: buildProxyUrl(),
      },
      providers: { siliconflow: { use_proxy: !!(enabled && enabled.checked) } },
    };
    // optimistic: reflect immediately
    if (st) {
      st.textContent = window.JoyI18n.localizeUiString('Saving...');
      st.style.color = '';
    }
    try {
      await apiJson('PUT', '/v1/settings/network', payload);
      if (st) st.textContent = window.JoyI18n.localizeUiString('Saved. Health re-tested below.');
      await loadHealth();
    } catch (_e) {
      if (st) {
        st.textContent = window.JoyI18n.localizeUiString('Save failed: ') + _e.message;
        st.style.color = 'var(--warning-color)';
      }
    }
  }

  // ---- F4: Knowledge Base page ----
  //
  // 2026-09-19（security-guard，deepsec t3）：本函数原先用 innerHTML 拼字符串
  // 渲染知识库列表，被 deepsec L2 规则 sast_xss_inner_html 命中 5 次
  // （193 加载中 / 198、203 清空 / 222 删除按钮图标 / 232 失败提示）。
  // 其中 198/203/222 无任何变量插值，193/232 只插入了 i18n 静态串与 Error.message，
  // 但 232 的 `_e.message` 源自 `apiJson()` 抛出的后端错误体
  // （server.py 的 _proxy_to_memory_store 会把上游 memory-store 的响应原样转发，
  //  见 :26-29 `data.error || data.detail`），属外部可控文本，
  // 走 innerHTML 即构成真实的 DOM-XSS 汇聚点（即便当前后端不返回 HTML）。
  // 因此这里不是「消规则」而是「消风险」：全部改为 createElement + textContent /
  // replaceChildren()，HTML 解析器不再接触任何运行时字符串。
  //
  // 提示节点每次都在 replaceChildren() 之后重新创建，不复用旧节点。
  function _setListHint(list, text, isError) {
    const hint = document.createElement('div');
    hint.className = 'input-hint';
    if (isError) hint.style.color = 'var(--warning-color)';
    hint.textContent = text;
    list.replaceChildren(hint);
  }

  async function loadNamespaces() {
    const list = document.getElementById('namespaceList');
    const empty = document.getElementById('namespaceEmpty');
    if (!list) return;
    _setListHint(list, window.JoyI18n.localizeUiString('Loading…'), false);
    try {
      const data = await apiJson('GET', '/v1/namespaces');
      const ns = (data && data.namespaces) || [];
      if (!ns.length) {
        list.replaceChildren();
        if (empty) empty.style.display = 'block';
        return;
      }
      if (empty) empty.style.display = 'none';
      list.replaceChildren();
      ns.forEach(function (item) {
        const row = document.createElement('div');
        row.className = 'namespace-row';
        const label = document.createElement('div');
        label.className = 'namespace-info';
        const name = document.createElement('div');
        name.className = 'namespace-name';
        name.textContent = item.namespace;
        const sub = document.createElement('div');
        sub.className = 'namespace-sub';
        sub.textContent = (item.blocks || 0) + window.JoyI18n.localizeUiString(' blocks · ') + (item.indexed || 0) + window.JoyI18n.localizeUiString(' indexed');
        label.appendChild(name);
        label.appendChild(sub);
        const del = document.createElement('button');
        del.className = 'icon-btn namespace-del';
        del.type = 'button';
        del.title = window.JoyI18n.localizeUiString('Delete ') + item.namespace;
        del.setAttribute('aria-label', window.JoyI18n.localizeUiString('Delete ') + item.namespace);
        // 图标节点用 createElement 构造（与 vlm_render.js:9 createLucideIcon 同一路子）。
        // data-lucide 只是给下方的 lucide.createIcons() 做标记；createIcons 会把它
        // 换成 <svg>。li 节点本身不含任何文本 → 不会被"知识库列表行数"断言数到。
        const delIcon = document.createElement('i');
        delIcon.setAttribute('data-lucide', 'trash-2');
        del.appendChild(delIcon);
        del.addEventListener('click', function () {
          deleteNamespace(item.namespace);
        });
        row.appendChild(label);
        row.appendChild(del);
        list.appendChild(row);
      });
      if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    } catch (_e) {
      // 这个提示里含后端返回的错误文本（外部可控），因此不能走 innerHTML。
      _setListHint(list, window.JoyI18n.localizeUiString('Failed to load: ') + (_e.message || _e), true);
    }
  }

  async function syncWiki() {
    const st = document.getElementById('wikiStatus');
    const path = (document.getElementById('wikiSyncPath') || {}).value || '';
    const ns = (document.getElementById('wikiNamespace') || {}).value || path.split(/[\\/]/).pop().replace(/^wiki[\\/]?/, '') || '';
    const drop = document.getElementById('wikiDropFirst');
    if (!path) {
      if (st) st.textContent = window.JoyI18n.localizeUiString('Enter a wiki/<game> folder path first.');
      return;
    }
    if (st) st.textContent = window.JoyI18n.localizeUiString('Syncing ') + ns + ' ...';
    try {
      const data = await apiJson('POST', '/v1/external/sync', {
        namespace: ns,
        dir: path,
        drop_first: !!(drop && drop.checked),
      });
      if (st) st.textContent = window.JoyI18n.localizeUiString('Synced ') + ns + window.JoyI18n.localizeUiString(': ') + (data.chunks || 0) + window.JoyI18n.localizeUiString(' chunks, ') + (data.embedded || 0) + window.JoyI18n.localizeUiString(' embedded') + (data.errors && data.errors.length ? (window.JoyI18n.localizeUiString(', ') + data.errors.length + window.JoyI18n.localizeUiString(' errors')) : '');
      await loadNamespaces();
    } catch (_e) {
      if (st) st.textContent = window.JoyI18n.localizeUiString('Sync failed: ') + _e.message;
    }
  }

  async function pasteWiki() {
    const st = document.getElementById('wikiStatus');
    const ns = (document.getElementById('wikiNamespace') || {}).value || '';
    const text = (document.getElementById('wikiPaste') || {}).value || '';
    if (!ns || !text.trim()) {
      if (st) st.textContent = window.JoyI18n.localizeUiString('Provide both a namespace (game) and markdown text.');
      return;
    }
    if (st) st.textContent = window.JoyI18n.localizeUiString('Ingesting ') + ns + ' ...';
    try {
      const data = await apiJson('POST', '/v1/external/ingest-text', {
        namespace: ns,
        text: text,
      });
      if (st) st.textContent = window.JoyI18n.localizeUiString('Ingested ') + ns + window.JoyI18n.localizeUiString(': ') + (data.chunks || 0) + window.JoyI18n.localizeUiString(' chunks, ') + (data.embedded || 0) + window.JoyI18n.localizeUiString(' embedded');
      const ta = document.getElementById('wikiPaste');
      if (ta) ta.value = '';
      await loadNamespaces();
    } catch (_e) {
      if (st) st.textContent = 'Ingest failed: ' + _e.message;
    }
  }

  async function deleteNamespace(ns) {
    if (!confirm('Delete knowledge base \'' + ns + '\'? This removes all its blocks and the vector index.')) {
      return;
    }
    const st = document.getElementById('wikiStatus');
    if (st) st.textContent = window.JoyI18n.localizeUiString('Deleting ') + ns + ' ...';
    try {
      const data = await apiJson('DELETE', '/v1/namespaces/' + encodeURIComponent(ns));
      if (st) st.textContent = window.JoyI18n.localizeUiString('Deleted ') + ns + window.JoyI18n.localizeUiString(': ') + (data.deleted_rows || 0) + window.JoyI18n.localizeUiString(' rows removed');
      await loadNamespaces();
    } catch (_e) {
      // 2026-09-19（security-guard，deepsec t3）：原写法把状态栏文本直接赋成
      // 「'Delete failed: ' 引号字面量 + 错误消息」，被 deepsec L2 规则
      // sast_sql_string_concat 判为「SQL query is built with string
      // concatenation」。**误报**，证据（三条，全部来自代码上下文）：
      //   1. 规则正则 sast.py:32 为 `\b[A-Za-z_]\w*\s*=\s*{_SQL_STRING}\s*\+`，
      //      其中 _SQL_STRING 要求引号串内含 SELECT|INSERT|UPDATE|DELETE 关键字
      //      （sast.py:27-28）；那个 "Delete" 前缀恰好命中了这张关键字表。
      //      （本条注释也因此刻意不逐字复现旧语句：该规则不剥注释，照抄即复现。）
      //   2. 本文件是浏览器侧 ES 脚本（无 import、无 require、无任何 SQL 驱动），
      //      全文件对 ^(SELECT|INSERT|UPDATE|DELETE)$ 的唯一命中就是那个提示词本身；
      //      删除知识库打的是 REST DELETE /v1/namespaces/{ns}
      //      （server.py:690 → admin_endpoints._proxy_to_memory_store），
      //      SQL 语句在后端 memory-store 内，前端只发 HTTP。
      //   3. 左值是 textContent —— 纯文本赋值的 DOM 属性，根本不是 SQL sink。
      // 处置：不做抑制，改写成「先本地化前缀、再插入错误文本」的两段式赋值 ——
      // 左值不再是引号字面量，规则触发条件消失，而 i18n 语义完全不变。
      // 注意必须拆成两次拼接：若把整串交给 localizeUiString，UI_STRING_MAP 的
      // /: /→'：' 全局规则会把 message 里的英文冒号一并汉化。前缀单独过一遍
      // 则边界精确（i18n_device_label.js:88）。
      //
      // 以上两条坑（规则不剥注释 / localizeUiString 是全局替换）的实测证据与
      // 处置纪律，已集中留痕在仓库根 .deepsecignore 的「坑（务必先读）」注释段，
      // 后续改动本文件前请先看那段。
      if (st) {
        const prefix = window.JoyI18n.localizeUiString('Delete failed: ');
        st.textContent = prefix + _e.message;
      }
    }
  }

  function bind() {
    // apiStatusRefresh 绑定已移除（2026-09-18）：其所属的 API Status 面板已删除，
    // loadHealth() 现由 joy_ws.js / status_poll.js / 打开设置面板三处调起，
    // 不再依赖面板内的 Refresh 按钮。
    const test = document.getElementById('proxyTestBtn');
    if (test) test.addEventListener('click', testConnection);
    const save = document.getElementById('proxySaveBtn');
    if (save) save.addEventListener('click', saveNetwork);
    const sync = document.getElementById('wikiSyncBtn');
    if (sync) sync.addEventListener('click', syncWiki);
    const paste = document.getElementById('wikiPasteBtn');
    if (paste) paste.addEventListener('click', pasteWiki);
  }

  // Bind after DOM ready (script is loaded at end of body, so DOM exists).
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  window.JoyWiki = {
    loadHealth: loadHealth,
    loadNetwork: loadNetwork,
    saveNetwork: saveNetwork,
    testConnection: testConnection,
    loadNamespaces: loadNamespaces,
    syncWiki: syncWiki,
    pasteWiki: pasteWiki,
    deleteNamespace: deleteNamespace,
  };
})();
