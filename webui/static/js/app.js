/* ============================================================
   多源文本融合与语音克隆配音系统 —— 界面交互
   ============================================================

   这份文件负责"网页这边的事"：
     · 按钮点了要干嘛、数据发给谁
     · 文件拖进来怎么接、怎么显示
     · 配音进行中的计时、声波动画
     · 首屏背景里流动的声波（canvas 绘制）

   和后端的分工：网页只管"看"和"点"，真正干活的（抓文章、
   调 AI、合成语音）全在 Python 那边，网页通过 /api/xxx 呼叫它们。
*/

(() => {
  'use strict';

  /* ============================================================
     一、小工具
     ============================================================ */

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /** 把秒数格式化成 0:00 / 1:23 这种时间 */
  function fmtTime(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  /**
   * 呼叫后端接口的统一入口。
   * 自动做三件事：带 JSON、解析回复、把"网络层面"的错误抛出来。
   * 业务层面的错误（reply.ok === false）由各调用处自己判断。
   */
  async function api(path, { method = 'GET', body, timeout = 0 } = {}) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    let timer = null;
    if (timeout > 0) {
      // 超时保险：万一服务器毫无反应，别让界面永远转圈
      const ctrl = new AbortController();
      opts.signal = ctrl.signal;
      timer = setTimeout(() => ctrl.abort(), timeout);
    }
    try {
      const resp = await fetch(path, opts);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.detail || `服务器返回了错误（${resp.status}）`);
      }
      return data;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /* —— 通知气泡 —— */
  const toastBox = $('#toasts');
  // 三种状态各配一个小图标（对勾 / 叹号 / 叉），跟左侧色条同色
  const TOAST_ICONS = {
    ok: `<svg class="toast-icon" viewBox="0 0 20 20" fill="none"><path d="m4.5 10.5 3.6 3.6L15.5 6.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    warn: `<svg class="toast-icon" viewBox="0 0 20 20" fill="none"><path d="M10 3.5 17 16H3L10 3.5Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M10 8.5v3.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="10" cy="14" r=".4" stroke="currentColor" stroke-width="1.4"/></svg>`,
    bad: `<svg class="toast-icon" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m7.5 7.5 5 5m0-5-5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  };
  function toast(message, kind = 'ok', duration = 3800) {
    const el = document.createElement('div');
    el.className = 'toast' + (kind === 'bad' ? ' is-bad' : kind === 'warn' ? ' is-warn' : '');
    el.innerHTML = (TOAST_ICONS[kind] || TOAST_ICONS.ok) + '<span></span>';
    el.lastElementChild.textContent = message;   // 用 textContent 防注入
    toastBox.appendChild(el);
    setTimeout(() => {
      el.classList.add('is-out');
      el.addEventListener('animationend', () => el.remove(), { once: true });
    }, duration);
  }

  /* —— 按钮忙闲状态 ——
     忙的时候：转圈图标出现、原图标藏起来、点不动。
     第一次使用时自动给按钮补上"转圈"结构，不用 HTML 里到处写。 */
  function setBusy(btn, busy, busyText) {
    if (!btn) return;
    if (busy) {
      if (!btn.dataset.idleHtml) btn.dataset.idleHtml = 'KEEP';
      $$(':scope > svg', btn).forEach((s) => s.classList.add('btn-idle-icon'));
      if (!$('.btn-spinner', btn)) {
        const sp = document.createElement('span');
        sp.className = 'btn-spinner';
        btn.prepend(sp);
      }
      if (busyText) {
        if (!btn.dataset.label) btn.dataset.label = $('.btn-label', btn)?.textContent ?? btn.textContent.trim();
        let lab = $('.btn-label', btn);
        if (!lab) {
          // 把按钮里散的文本包成一个 span，方便替换
          lab = document.createElement('span');
          lab.className = 'btn-label';
          const texts = Array.from(btn.childNodes).filter((n) => n.nodeType === 3 && n.textContent.trim());
          texts.forEach((t) => { lab.textContent += t.textContent.trim(); t.remove(); });
          btn.appendChild(lab);
        }
        lab.textContent = busyText;
      }
      btn.classList.add('is-busy');
      btn.disabled = true;
    } else {
      btn.classList.remove('is-busy');
      btn.disabled = false;
      $$(':scope > svg', btn).forEach((s) => s.classList.remove('btn-idle-icon'));
      const lab = $('.btn-label', btn);
      if (lab && btn.dataset.label) lab.textContent = btn.dataset.label;
    }
  }

  /* ============================================================
     二、页面上的共享状态
     ============================================================ */

  const state = {
    files: [],        // 已上传的文章文件 [{path, name, ok, info}]
    audioFile: null,  // 已上传的参考音频
    models: {},       // 模型名（页脚展示）
    voices: [],       // 声音列表
    synthTimer: null, // 合成计时器
  };

  /* ============================================================
     三、开场：拉取基础信息
     ============================================================ */

  async function boot() {
    try {
      const data = await api('/api/bootstrap');
      state.voices = data.voices || [];
      state.models = data.models || {};

      // 声音下拉框
      const sel = $('#voiceSelect');
      sel.innerHTML = '';
      for (const v of state.voices) {
        const opt = document.createElement('option');
        opt.value = v.name;
        opt.textContent = v.name;
        sel.appendChild(opt);
      }

      // 页脚：只留用户认得出来的模型名。
      // 完整的权重文件名（如 7Nanometer_v2_e25_s1050）是训练参数，
      // 对观众是天书——把它剪成模型本名（7Nanometer_v2）就够了。
      $('#modelName').textContent =
        (state.models.acoustic || '—').replace(/_e\d+_s\d+$/, '').replace(/_s\d+$/, '');

      applySovits(data.sovits);
      loadRecent();
    } catch (e) {
      toast('打不开后端服务：' + e.message, 'bad', 6000);
    }
  }

  /* ============================================================
     四、语音服务状态
     ============================================================ */

  /** 把一份状态数据刷到界面各处（顶栏圆点、克隆卡片） */
  function applySovits(info) {
    const online = !!info?.online;
    const msg = info?.message || '';

    const dot = $('#topDot');
    dot.className = 'dot ' + (online ? 'is-on' : 'is-off');
    // 顶栏用的是"中性陈述"而不是"离线报警"：
    // 引擎没启动是一件正常的事（用户可能只想用在线声音），不是故障
    $('#topStatusText').textContent = online ? '语音引擎在线' : '语音引擎未启动';

    const sdot = $('#sovitsDot');
    sdot.className = 'dot ' + (online ? 'is-on' : 'is-off');
    $('#sovitsPillText').textContent = online ? '在线' : '未启动';
    $('#sovitsPill').className = 'pill pill--status ' + (online ? 'is-on' : 'is-off');

    // 状态盒只在本"有事要说"时出现：
    // 在线时药丸已经说明一切，不再重复第三遍（启动过程中的日志另行显示）
    const box = $('#sovitsStatusBox');
    if (online) {
      box.hidden = true;
    } else {
      box.hidden = false;
      box.textContent = `${msg}\n提示：点「启动服务」，等 10~30 秒模型加载完成。`;
      // 未启动只是"还没轮到"（琥珀提醒），不是出错（红色）——红色留给真正的故障
      box.className = 'status-box is-warn';
    }
  }

  async function refreshSovits(showToast = false) {
    try {
      const data = await api('/api/sovits/status');
      applySovits(data);
      if (showToast) toast(data.online ? '语音服务在线' : '语音服务离线', data.online ? 'ok' : 'warn');
    } catch (e) {
      if (showToast) toast('状态查询失败：' + e.message, 'bad');
    }
  }

  /**
   * 读取 SSE 流（服务器边干活边推消息）。
   * fetch 拿到的是一个"水管"，我们一小段一小段地读，
   * 每读到一个完整消息（以空行分隔）就交给 onEvent 处理。
   */
  async function streamPost(path, onEvent) {
    const resp = await fetch(path, { method: 'POST' });
    if (!resp.ok || !resp.body) throw new Error(`服务器返回了错误（${resp.status}）`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = chunk.split('\n').find((l) => l.startsWith('data: '));
        if (line) {
          try { onEvent(JSON.parse(line.slice(6))); } catch { /* 忽略坏消息 */ }
        }
      }
    }
  }

  $('#btnStartSovits').addEventListener('click', async () => {
    const btn = $('#btnStartSovits');
    const box = $('#sovitsStatusBox');
    setBusy(btn, true, '启动中…');
    box.hidden = false;   // 启动过程有实时日志要展示
    box.className = 'status-box';
    box.textContent = '正在发送启动命令…';
    try {
      await streamPost('/api/sovits/start', (ev) => {
        if (ev.message) box.textContent = ev.message;
      });
      // 流结束后再查一次真实状态
      await refreshSovits();
      toast('启动流程结束，当前状态见上方', 'ok');
    } catch (e) {
      toast('启动失败：' + e.message, 'bad', 6000);
      box.textContent = '启动失败：' + e.message;
      box.className = 'status-box is-bad';
    } finally {
      setBusy(btn, false);
    }
  });

  $('#btnStopSovits').addEventListener('click', async () => {
    const btn = $('#btnStopSovits');
    setBusy(btn, true, '停止中…');
    try {
      const r = await api('/api/sovits/stop', { method: 'POST' });
      toast(r.message || '已发送停止命令');
      await refreshSovits();
    } catch (e) {
      toast('停止失败：' + e.message, 'bad');
    } finally {
      setBusy(btn, false);
    }
  });

  $('#btnCheckSovits').addEventListener('click', () => refreshSovits(true));
  $('#topStatus').addEventListener('click', () => refreshSovits(true));

  /* 页面滚动后给顶栏加一层更实的底色（见 CSS 的 body.scrolled） */
  function onScroll() {
    document.body.classList.toggle('scrolled', window.scrollY > 8);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ============================================================
     五、标签页切换
     ============================================================ */

  const tabs = $$('.tab');
  const ink = $('#tabInk');

  function moveInk() {
    const active = $('.tab.is-active');
    if (!active) return;
    ink.style.width = active.offsetWidth + 'px';
    ink.style.transform = `translateX(${active.offsetLeft}px)`;
  }

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      if (tab.classList.contains('is-active')) return;
      tabs.forEach((t) => {
        t.classList.toggle('is-active', t === tab);
        t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
      });
      $$('.panel').forEach((p) => {
        p.classList.toggle('is-active', p.id === tab.dataset.panel);
      });
      moveInk();
    });
  });

  window.addEventListener('resize', moveInk);
  // 字体加载完宽度可能变，重新量一次下划线
  if (document.fonts?.ready) document.fonts.ready.then(moveInk);

  /* ============================================================
     六、滑块（实时显示数值 + 染色进度条）
     ============================================================ */

  /**
   * 绑定一个滑块。
   * bipolar=true 的表"双向滑块"（语速/音量，0 在正中）：
   * 它的染色从正中间向两边长，而不是从左边缘长——
   * 否则 0%（不变）看起来像"已经拉到 50%"，是控件在说谎。
   */
  function bindRange(id, valueId, format, bipolar = false) {
    const el = $('#' + id);
    const out = $('#' + valueId);
    const sync = () => {
      const min = +el.min, max = +el.max, v = +el.value;
      const p = ((v - min) / (max - min)) * 100;
      if (bipolar) {
        el.style.setProperty('--fill-start', Math.min(50, p) + '%');
        el.style.setProperty('--fill-end', Math.max(50, p) + '%');
      } else {
        el.style.setProperty('--fill-start', '0%');
        el.style.setProperty('--fill-end', p + '%');
      }
      out.textContent = format(v);
    };
    el.addEventListener('input', sync);
    sync();
  }

  bindRange('wordCountRange', 'wordCountValue', (v) => `${v} 字`);
  bindRange('creativityRange', 'creativityValue', (v) => v.toFixed(1));
  bindRange('rateRange', 'rateValue', (v) => (v > 0 ? `+${v}` : `${v}`) + '%', true);
  bindRange('volumeRange', 'volumeValue', (v) => (v > 0 ? `+${v}` : `${v}`) + '%', true);
  bindRange('pauseRange', 'pauseValue', (v) => `${v} ms`);

  /* ============================================================
     七、上传（文章文件 + 参考音频）
     ============================================================ */

  /**
   * 把一批文件发给服务器，然后刷新界面上的文件清单。
   * kind = 'article'（文章） 或 'audio'（参考音频）
   */
  async function uploadFiles(fileList, kind) {
    const form = new FormData();
    for (const f of fileList) form.append('files', f, f.name);
    const resp = await fetch(`/api/upload?kind=${kind}`, { method: 'POST', body: form });
    if (!resp.ok) throw new Error(`上传失败（${resp.status}）`);
    const data = await resp.json();

    for (const item of data.files || []) {
      if (kind === 'audio') {
        state.audioFile = item;
      } else if (item.ok) {
        state.files.push(item);
      } else {
        toast(`「${item.name}」没读成功：${item.info}`, 'bad', 6000);
      }
    }
    if (kind === 'audio') renderAudioFile();
    else renderFileList();
  }

  const FILE_ICON = `<svg viewBox="0 0 20 20" fill="none"><path d="M5 2.5h6l4 4v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-14a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M11 2.5v4h4" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  const X_ICON = `<svg viewBox="0 0 16 16" fill="none"><path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`;

  /** 重画文章文件清单 */
  function renderFileList() {
    const box = $('#fileList');
    if (!state.files.length) { box.hidden = true; box.innerHTML = ''; return; }
    box.hidden = false;
    box.innerHTML = '';
    state.files.forEach((f, i) => {
      const row = document.createElement('div');
      row.className = 'file-item';
      row.style.animationDelay = `${i * 40}ms`;
      row.innerHTML = `${FILE_ICON}
        <span class="file-name" title="${f.name}">${f.name}</span>
        <span class="file-info">${f.chars} 字</span>
        <button class="file-remove" title="移除" type="button">${X_ICON}</button>`;
      $('.file-remove', row).addEventListener('click', () => {
        state.files.splice(i, 1);
        renderFileList();
      });
      box.appendChild(row);
    });
  }

  /** 重画参考音频清单 */
  function renderAudioFile() {
    const box = $('#audioFileList');
    const f = state.audioFile;
    if (!f) { box.hidden = true; box.innerHTML = ''; return; }
    box.hidden = false;
    box.innerHTML = '';
    const row = document.createElement('div');
    row.className = 'file-item' + (f.ok ? '' : ' is-bad');
    row.innerHTML = `${FILE_ICON}
      <span class="file-name" title="${f.name}">${f.name}</span>
      <span class="file-info">${f.ok ? f.info : f.info}</span>
      <button class="file-remove" title="移除" type="button">${X_ICON}</button>`;
    $('.file-remove', row).addEventListener('click', () => {
      state.audioFile = null;
      renderAudioFile();
    });
    box.appendChild(row);
  }

  /** 给一个拖拽区接上"点击选择 + 拖拽进来"两套操作 */
  function bindDropzone(zoneSel, inputSel, kind) {
    const zone = $(zoneSel);
    const input = $(inputSel);

    zone.addEventListener('click', () => input.click());
    zone.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
    });
    input.addEventListener('change', async () => {
      if (input.files?.length) {
        try { await uploadFiles(input.files, kind); }
        catch (e) { toast(e.message, 'bad'); }
        input.value = '';
      }
    });

    // 拖拽：dragover 时必须 preventDefault，浏览器才允许"放进来"
    let depth = 0;
    zone.addEventListener('dragenter', (e) => {
      e.preventDefault(); depth++; zone.classList.add('is-dragover');
    });
    zone.addEventListener('dragover', (e) => e.preventDefault());
    zone.addEventListener('dragleave', () => {
      depth = Math.max(0, depth - 1);
      if (!depth) zone.classList.remove('is-dragover');
    });
    zone.addEventListener('drop', async (e) => {
      e.preventDefault();
      depth = 0;
      zone.classList.remove('is-dragover');
      const files = Array.from(e.dataTransfer?.files || []);
      if (!files.length) return;
      try { await uploadFiles(files, kind); }
      catch (err) { toast(err.message, 'bad'); }
    });
  }

  bindDropzone('#dropzone', '#fileInput', 'article');
  bindDropzone('#audioDropzone', '#audioInput', 'audio');

  /* ============================================================
     八、文案生成 / 润色
     ============================================================ */

  /** 从界面上收集"生成文案"需要的一切参数 */
  function collectGenerateParams() {
    return {
      files: state.files.map((f) => f.path),
      articles: $('#articlesInput').value,
      style: $('#styleSelect').value,
      word_count: +$('#wordCountRange').value,
      creativity: +$('#creativityRange').value,
    };
  }

  async function doGenerate(btn) {
    setBusy(btn, true, '正在生成…');
    document.body.classList.add('working');   // 让首屏声波加速（见 initHeroWave）
    try {
      const r = await api('/api/generate', {
        method: 'POST',
        body: collectGenerateParams(),
        timeout: 5 * 60 * 1000,   // 生成最多等 5 分钟
      });
      const script = r.script || '';
      // 以「❌」或「请先」开头的是"提示语"（比如"请先拖入文章文件"），
      // 不是真文案——只弹提示，不往文案框里塞，免得它被当成文案念出来。
      // （「【提示】…」开头的属于"成功但有话要说"，内容有效，正常显示。）
      if (script.startsWith('❌') || script.startsWith('请先')) {
        toast(script.split('\n')[0], 'warn', 6500);
      } else {
        $('#scriptOutput').value = script;
        updateWordCount();
        toast('文案生成完成');
        // 手机上单列排布，生成按钮和结果框可能不在同一屏——滚过去让用户看到
        if (window.innerWidth <= 1080) {
          $('#scriptOutput').scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }
    } catch (e) {
      toast('生成失败：' + e.message, 'bad', 6000);
    } finally {
      setBusy(btn, false);
      document.body.classList.remove('working');
    }
  }

  $('#btnGenerate').addEventListener('click', (e) => doGenerate(e.currentTarget));
  $('#btnRegenerate').addEventListener('click', (e) => doGenerate(e.currentTarget));

  $('#btnRefine').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const script = $('#scriptOutput').value.trim();
    const suggestions = $('#refineInput').value.trim();
    if (!script) { toast('请先生成或粘贴文案', 'warn'); return; }
    if (!suggestions) { toast('请先写点修改建议', 'warn'); return; }
    setBusy(btn, true, '润色中…');
    try {
      const r = await api('/api/refine', {
        method: 'POST',
        body: { script, suggestions },
        timeout: 5 * 60 * 1000,
      });
      $('#scriptOutput').value = r.script || '';
      updateWordCount();
      toast('润色完成');
    } catch (err) {
      toast('润色失败：' + err.message, 'bad');
    } finally {
      setBusy(btn, false);
    }
  });

  function updateWordCount() {
    const text = $('#scriptOutput').value || '';
    const n = text.replace(/[ \n]/g, '').length;
    $('#wordCount').textContent = `${n} 字`;
  }
  $('#scriptOutput').addEventListener('input', updateWordCount);

  /* ============================================================
     九、网址抓取
     ============================================================ */

  $('#btnExtract').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const urls = $('#urlInput').value.trim();
    const status = $('#extractStatus');
    if (!urls) { toast('请先填网址，每行一个', 'warn'); return; }
    setBusy(btn, true, '正在抓取…');
    status.className = 'hint';
    status.textContent = '';
    try {
      const r = await api('/api/extract', { method: 'POST', body: { urls }, timeout: 5 * 60 * 1000 });
      const box = $('#articlesInput');
      box.value = box.value ? box.value + '\n\n' + r.articles : r.articles;
      status.textContent = r.status || '';
      status.className = 'hint ' + (r.articles ? 'is-ok' : 'is-bad');
      toast(r.articles ? '抓取完成，已填入下方粘贴框' : '没有抓到内容', r.articles ? 'ok' : 'warn');
    } catch (err) {
      status.textContent = '抓取失败：' + err.message;
      status.className = 'hint is-bad';
    } finally {
      setBusy(btn, false);
    }
  });

  /* ============================================================
     十、语音合成
     ============================================================ */

  // "使用克隆声音"开关：打开时展开参考音频面板
  $('#useClone').addEventListener('change', (e) => {
    $('#clonePanel').hidden = !e.target.checked;
  });

  /** 合成进行中：界面进入"工作状态" */
  function setSynthUI(running) {
    const btn = $('#btnSynthesize');
    setBusy(btn, running, '正在合成…');
    document.body.classList.toggle('working', running);   // 首屏声波加速
    // 合成期间把"停止(服务)"锁住：那是停引擎的按钮，误点会让这次合成半路夭折
    $('#btnStopSovits').disabled = running;
    $('#btnStopSovits').title = running ? '合成进行中，暂时不能停止服务' : '';
    $('#synthStatus').hidden = !running;
    if (running) {
      $('#audioEmpty').style.display = 'none';
      $('#player').hidden = true;
      $('#synthMessage').textContent = '';
      startSynthTimer();
    } else {
      stopSynthTimer();
    }
  }

  function startSynthTimer() {
    const t0 = Date.now();
    const el = $('#synthElapsed');
    const text = $('#synthStatusText');
    stopSynthTimer();
    state.synthTimer = setInterval(() => {
      const sec = (Date.now() - t0) / 1000;
      el.textContent = fmtTime(sec);
      // 时间久了给一句"进度感"的说明，让等待不焦虑
      if (sec > 25 && text.textContent === '正在准备…') {
        text.textContent = '正在逐句合成，长文案需要几分钟…';
      }
    }, 250);
    text.textContent = '正在准备…';
    el.textContent = '0:00';
  }
  function stopSynthTimer() {
    if (state.synthTimer) { clearInterval(state.synthTimer); state.synthTimer = null; }
  }

  $('#btnSynthesize').addEventListener('click', async () => {
    const script = $('#scriptOutput').value.trim();
    if (!script) { toast('请先生成或粘贴文案', 'warn'); return; }

    const useClone = $('#useClone').checked;
    if (useClone && state.audioFile && state.audioFile.ok && !$('#promptText').value.trim()) {
      toast('用了自己上传的参考音频，就必须填写"录音里说的话"', 'warn', 6000);
      return;
    }

    const order = {
      script,
      voice_name: $('#voiceSelect').value,
      rate: +$('#rateRange').value,
      volume: +$('#volumeRange').value,
      pause: +$('#pauseRange').value,
      use_sovits: useClone,
      refer_audio: useClone && state.audioFile?.ok ? state.audioFile.path : '',
      prompt_text: useClone ? $('#promptText').value : '',
    };

    setSynthUI(true);
    try {
      const r = await api('/api/synthesize', { method: 'POST', body: order });
      if (!r.ok) {
        setSynthUI(false);
        toast(r.error || '下单失败', 'bad', 6000);
        return;
      }
      await pollSynth(r.task_id);
    } catch (e) {
      setSynthUI(false);
      toast('合成失败：' + e.message, 'bad', 6000);
    }
  });

  /** 拿着任务号轮询：每秒问一次后端"做好了吗" */
  async function pollSynth(taskId) {
    for (;;) {
      await sleep(1000);
      let r;
      try {
        r = await api(`/api/synthesize/status/${taskId}`);
      } catch {
        continue; // 网络打嗝，下秒再问
      }
      if (!r.ok) {
        setSynthUI(false);
        toast(r.error || '任务丢失', 'bad');
        return;
      }
      if (r.status === 'running') {
        if (r.message && r.message !== '已开始' && r.message !== '正在准备…') {
          $('#synthStatusText').textContent = r.message;
        }
        continue;
      }
      // —— 结束了 ——
      setSynthUI(false);
      if (r.status === 'done') {
        showPlayer(r.audio_url, r.filename);
        loadRecent();   // 把刚出炉的成品刷进"最近生成"列表最上面
        $('#synthMessage').textContent = r.message || '';
        toast('配音合成完成');
      } else {
        $('#synthMessage').textContent = r.message || '合成失败';
        // 之前若成功合成过，把上一版成品放回界面；否则回到空态
        if (audio.src) {
          $('#player').hidden = false;
          $('#audioEmpty').style.display = 'none';
        } else {
          $('#audioEmpty').style.display = '';
        }
        toast(r.message || '合成失败', 'bad', 7000);
      }
      return;
    }
  }

  /* ============================================================
     十一、播放器
     ============================================================ */

  const audio = $('#audioEl');
  const player = $('#player');

  function showPlayer(url, filename) {
    if (!url) return;
    audio.src = url;
    player.hidden = false;
    $('#audioEmpty').style.display = 'none';   // 有产物了，空态收起
    player.classList.remove('is-playing');
    syncRecentCurrent();   // "最近生成"列表里把正在载入的这条点亮
    $('#playerName').textContent = filename || '配音';
    $('#downloadBtn').href = url;
    $('#downloadBtn').setAttribute('download', filename || '');
    $('#playerNow').textContent = '0:00';
    $('#playerTotal').textContent = '0:00';
    $('#playerFill').style.width = '0%';
    // 滚到播放器（手机上是纵向排列，避免用户找不到）
    if (window.innerWidth <= 1080) player.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  $('#playerBtn').addEventListener('click', () => {
    if (audio.paused) audio.play(); else audio.pause();
  });
  audio.addEventListener('play', () => player.classList.add('is-playing'));
  audio.addEventListener('pause', () => player.classList.remove('is-playing'));
  audio.addEventListener('ended', () => {
    player.classList.remove('is-playing');
    audio.currentTime = 0;
  });
  audio.addEventListener('loadedmetadata', () => {
    $('#playerTotal').textContent = fmtTime(audio.duration);
  });
  audio.addEventListener('timeupdate', () => {
    const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
    $('#playerFill').style.width = pct + '%';
    $('#playerNow').textContent = fmtTime(audio.currentTime);
  });

  // 点进度条任意位置 → 跳到那里
  $('#playerBar').addEventListener('click', (e) => {
    if (!audio.duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration;
  });

  /* ============================================================
     十一·五、"最近生成"列表
     ============================================================
     列出输出目录里最新的几个成品。点一行 → 载入播放器回听；
     右侧小箭头 → 单独下载。合成完成后会自动刷新（新的排最前）。
  */

  /** 把列表里正载入播放器的那条点亮（其他熄灭） */
  function syncRecentCurrent() {
    let path = '';
    try {
      if (audio.src) {
        // 注意：文件名里有中文，浏览器把 src 里的中文转成了 %E9%85%8D 这种
        // 百分号编码——要和列表里的原始路径比对，得先解码回来。
        path = decodeURIComponent(new URL(audio.src, location.href).pathname);
      }
    } catch { /* 忽略 */ }
    $$('.recent-item').forEach((el) => {
      el.classList.toggle('is-current', !!path && el.dataset.url === path);
    });
  }

  async function loadRecent() {
    const box = $('#recentBox');
    const list = $('#recentList');
    let r;
    try {
      r = await api('/api/recent');
    } catch {
      return;   // 列不出来也不影响主流程，静默跳过
    }
    if (!r.files || !r.files.length) {
      box.hidden = true;
      list.innerHTML = '';
      return;
    }
    box.hidden = false;
    list.innerHTML = '';
    for (const f of r.files) {
      const row = document.createElement('div');
      row.className = 'recent-item';
      row.dataset.url = f.url;

      const t = new Date(f.mtime * 1000);
      const stamp = `${t.getMonth() + 1}/${t.getDate()} ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;

      row.innerHTML = `
        <span class="r-wave" aria-hidden="true"></span>
        <span class="r-name"></span>
        <span class="r-time">${stamp}</span>
        <button class="r-dl" title="下载这一条" type="button" aria-label="下载">
          <svg viewBox="0 0 20 20" fill="none"><path d="M10 3v9m0 0 3.5-3.5M10 12 6.5 8.5M4 15.5h12"
            stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>`;
      $('.r-name', row).textContent = f.name;   // textContent 防注入

      row.addEventListener('click', (e) => {
        if (e.target.closest('.r-dl')) return;   // 点下载按钮时不触发播放
        showPlayer(f.url, f.name);
      });
      $('.r-dl', row).addEventListener('click', () => {
        const a = document.createElement('a');
        a.href = f.url;
        a.download = f.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
      });
      list.appendChild(row);
    }
    syncRecentCurrent();
  }

  /* ============================================================
     十二、首屏声波（canvas）
     ============================================================
     在开场区背景画几条缓缓流动的正弦波，
     中间强、两头弱——像一段声音从文字里流淌出来。
     全部用 requestAnimationFrame 驱动，页面切到后台时自动暂停省电。
  */

  function initHeroWave() {
    const canvas = $('#heroWave');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let W = 0, H = 0, dpr = 1;

    function resize() {
      const rect = canvas.parentElement.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = Math.max(1, Math.round(rect.width));
      H = Math.max(1, Math.round(rect.height));
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    // 每条波：振幅（占高度的比例）、疏密、流速、透明度、线宽、相位
    const waves = [
      { amp: .30, freq: 1.6, speed: .22, alpha: .30, width: 1.6, phase: 0 },
      { amp: .22, freq: 2.3, speed: -.31, alpha: .16, width: 1.2, phase: 1.7 },
      { amp: .38, freq: 1.2, speed: .15, alpha: .10, width: 2.2, phase: 3.4 },
      { amp: .17, freq: 3.1, speed: .40, alpha: .13, width: 1, phase: 5.1 },
      { amp: .26, freq: .8,  speed: .11, alpha: .22, width: 1, phase: 2.6 },
    ];

    function draw(t) {
      ctx.clearRect(0, 0, W, H);

      /* 波的"站位"按屏幕宽窄分两套（大标题附近不能让波穿过去，
         绿字和绿波叠在一起会互相"毛化"，读起来费劲）：
         · 宽屏：标题只占左半边，波从右侧 42% 处渐强——像声音
           从文字的右边淌出去；左侧（标题脚下）不出波。
         · 窄屏（手机）：没有"右半边"可用，波整体下移到
           hero 底部、振幅减半，变成一条"从底部涌出的声音"。 */
      const narrow = window.innerWidth < 1081;
      const cy = narrow ? H * .9 : H * .52;
      const base = Math.min(H * .34, 150) * (narrow ? .55 : 1);

      const envelope = (u) => {
        if (narrow) return Math.sin(Math.PI * u) ** 1.2;   // 手机：中间强两头弱
        return Math.min(1, Math.max(0, (u - .42) / .3));   // 桌面：从 42% 处渐强
      };

      for (const w of waves) {
        ctx.beginPath();
        for (let x = 0; x <= W; x += 3) {
          const u = x / W;
          const env = envelope(u);
          const y = cy
            + Math.sin(u * Math.PI * 2 * w.freq + t * w.speed + w.phase) * w.amp * base * env
            + Math.sin(u * Math.PI * 2 * w.freq * .37 + t * w.speed * .6) * w.amp * base * .35 * env;
          if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = `rgba(52, 211, 153, ${w.alpha})`;
        ctx.lineWidth = w.width;
        ctx.lineCap = 'round';
        ctx.stroke();
      }
    }

    let raf = null;
    let paused = false;     // 页面切到后台时暂停
    let visible = true;     // 开场区滚出屏幕时暂停（省电）

    /* 波浪的"流速"是个会呼吸的数：
       平时 1 倍速；当界面上有任务在跑（body.working）时加速到 2.4 倍。
       用累计相位（而不是绝对时间）来画，加速时波浪是"逐渐变快"，
       不会突然跳一下。这个小联动让"系统正在思考"这件事变得看得见。 */
    let phase = 0;
    let boost = 1;
    let last = performance.now();

    function loop(now) {
      const dt = Math.min((now - last) / 1000, .06);
      last = now;
      if (!paused) {
        const target = document.body.classList.contains('working') ? 2.4 : 1;
        boost += (target - boost) * Math.min(1, dt * 2.2);
        if (visible) {
          phase += dt * boost;
          draw(phase);
        }
      }
      raf = requestAnimationFrame(loop);
    }

    // 页面切走时停笔（省电）；回来时把时间接上，避免"跳帧"
    document.addEventListener('visibilitychange', () => {
      paused = document.hidden;
      last = performance.now();
    });

    // 开场区不在可视范围时也停笔：用户滚到下面的工作台时，
    // 没必要为一个看不见的动画白白耗电
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; }).observe(canvas);
    }

    resize();
    if (reduceMotion) {
      draw(0.8);   // 只画一帧静态的
    } else {
      raf = requestAnimationFrame(loop);
    }

    let rT = null;
    window.addEventListener('resize', () => {
      clearTimeout(rT);
      rT = setTimeout(() => { resize(); if (reduceMotion) draw(0.8); }, 150);
    });
  }

  /* ============================================================
     十三、点火
     ============================================================ */

  /**
   * 等字体加载完成，再放开入场动画（body.is-ready）。
   * 兜底：最多等 1.8 秒——万一字体出问题，界面也要正常出现，不能空着。
   */
  function startReveal() {
    document.body.classList.add('is-ready');
    moveInk();                        // 字体定形后重量一次标签下划线
    requestAnimationFrame(moveInk);   // 再补一帧，保险
  }
  const fallback = setTimeout(startReveal, 1800);
  if (document.fonts?.ready) {
    document.fonts.ready.then(() => { clearTimeout(fallback); startReveal(); });
  } else {
    clearTimeout(fallback);
    startReveal();
  }

  boot();
  moveInk();
  initHeroWave();
})();
