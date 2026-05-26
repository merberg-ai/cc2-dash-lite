(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const page = document.body.dataset.page;
  const cfgEl = $('#bootConfig');
  let cfg = cfgEl ? JSON.parse(cfgEl.textContent) : {};

  function toast(message, type = 'info', timeout = 4200) {
    const host = $('#toastHost');
    if (!host) return;
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      setTimeout(() => el.remove(), 200);
    }, timeout);
  }

  async function api(path, options = {}) {
    const resp = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const text = await resp.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!resp.ok) {
      const msg = data.detail || data.error || data.message || `HTTP ${resp.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }

  function setButtonBusy(button, busy, label) {
    if (!button) return;
    const labelEl = $('.button-label', button);
    if (busy) {
      button.dataset.originalLabel = labelEl ? labelEl.textContent : button.textContent;
      button.disabled = true;
      if (labelEl) labelEl.innerHTML = `<span class="spinner"></span> ${label || 'Working...'}`;
    } else {
      button.disabled = false;
      if (labelEl) labelEl.textContent = button.dataset.originalLabel || labelEl.textContent;
    }
  }

  function tempLine(current, target) {
    const c = current === null || current === undefined ? '-' : Number(current).toFixed(1);
    const t = target === null || target === undefined || Number(target) <= 0 ? 'off' : Number(target).toFixed(1);
    return `${c} / ${t}`;
  }

  function setText(id, value) {
    const el = $('#' + id);
    if (el) el.textContent = value ?? '-';
  }

  window.cc2CameraFailed = function () {
    const ph = $('#cameraPlaceholder');
    const cam = $('#cameraStream');
    if (ph) {
      ph.classList.remove('hidden');
      ph.innerHTML = '<span>Camera unavailable. Use Portal or check camera URL.</span>';
    }
    if (cam) cam.classList.add('hidden');
    setText('cameraState', 'Unavailable');
  };

  async function refreshDashboard() {
    try {
      const st = await api('/api/status');
      const progress = Math.max(0, Math.min(100, Number(st.progress || 0)));
      const progressBar = $('#progressBar');
      const progressText = $('#progressText');
      if (progressBar) progressBar.style.width = `${progress}%`;
      if (progressText) progressText.textContent = `${progress.toFixed(1)}%`;

      setText('statusText', st.status_text || st.state || 'Unknown');
      setText('assistantText', st.reachable ? 'Standing By' : 'Connection Lost');
      setText('printTime', st.print_time || '-');
      setText('timeLeft', st.time_left || '-');
      setText('completion', st.completion || `${progress.toFixed(1)}%`);
      setText('filamentUsed', st.filament_used || '-');
      setText('hotendTemp', tempLine(st.hotend_current, st.hotend_target));
      setText('bedTemp', tempLine(st.bed_current, st.bed_target));
      setText('fileName', st.file || '-');
      setText('printerHost', st.host || '-');
      setText('apiState', st.reachable ? 'Reachable' : 'Offline');
      setText('lastUpdate', new Date().toLocaleTimeString());
      if (st.portal_url) setText('portalState', st.portal_url);
      if (st.camera_url) setText('cameraState', st.camera_url);

      const portalButton = $('#portalButton');
      if (portalButton && st.portal_url) portalButton.href = st.portal_url;

      const statusEl = $('#statusText');
      if (statusEl) {
        statusEl.classList.toggle('bad-text', !st.reachable || /error|fail|offline/i.test(st.status_text || st.state || ''));
        statusEl.classList.toggle('good-text', st.reachable && /print|ready|standby|idle/i.test(st.status_text || st.state || ''));
      }
      const ph = $('#cameraPlaceholder');
      const cam = $('#cameraStream');
      if (ph && cam && !cam.classList.contains('hidden')) ph.classList.add('hidden');
    } catch (err) {
      setText('apiState', 'Error');
      setText('assistantText', 'Connection Trouble');
      const statusEl = $('#statusText');
      if (statusEl) {
        statusEl.textContent = 'Printer Error';
        statusEl.classList.add('bad-text');
      }
      console.warn(err);
    }
  }

  function initDashboard() {
    refreshDashboard();
    const interval = Number(cfg?.dashboard?.refresh_interval_seconds || 3) * 1000;
    setInterval(refreshDashboard, Math.max(1500, interval));
    $$('.action-button').forEach(btn => {
      btn.addEventListener('click', async () => {
        const action = btn.dataset.action;
        const requires = btn.dataset.requiresConfirm === 'true';
        if (requires && !confirm(btn.dataset.confirm || 'Are you sure?')) return;
        setButtonBusy(btn, true, btn.dataset.spinnerText || 'Sending...');
        try {
          const data = await api(`/api/action/${action}`, { method: 'POST', body: JSON.stringify({}) });
          toast(data.message || 'Command sent', data.ok ? 'success' : 'warn');
          await refreshDashboard();
        } catch (err) {
          toast(err.message, 'error', 7000);
        } finally {
          setButtonBusy(btn, false);
        }
      });
    });
  }

  async function savePrinter(host, name, portalUrl, cameraUrl, serial, accessCode) {
    const data = await api('/api/printers', {
      method: 'POST',
      body: JSON.stringify({
        host,
        name,
        serial: serial || host,
        access_code: accessCode || '123456',
        portal_url: portalUrl,
        camera_url: cameraUrl,
        set_default: true,
        enabled: true,
        allow_commands: true,
        allow_dangerous_commands: false
      })
    });
    cfg = data.config;
    toast('Printer saved. Opening dashboard...', 'success');
    setTimeout(() => location.href = '/', 600);
  }

  function renderScanResults(candidates) {
    const box = $('#scanResults');
    if (!box) return;
    box.innerHTML = '';
    if (!candidates.length) {
      box.innerHTML = '<div class="result-item"><strong>No candidates found</strong><span>Try manual add or widen the subnet.</span></div>';
      return;
    }
    candidates.forEach((c, idx) => {
      const item = document.createElement('div');
      item.className = 'result-item';
      const name = c.likely_printer ? 'Centauri candidate' : 'Network device';
      const serial = c.serial || '';
      const model = c.machine_model || c.http_title || '';
      item.innerHTML = `
        <strong>${name}: ${c.host}</strong>
        <span>Ports: ${(c.open_ports || []).join(', ')} ${model ? ' • ' + model : ''}</span>
        ${serial ? `<span>Serial: ${serial}</span>` : `<label class="field-label" for="scanSerial${idx}">Serial number</label><input id="scanSerial${idx}" class="input scan-serial" placeholder="Printer serial / SN" />`}
        <label class="field-label" for="scanPin${idx}">Printer PIN / access code</label>
        <input id="scanPin${idx}" class="input scan-pin" type="password" inputmode="numeric" value="123456" placeholder="123456" />
        <button class="button primary full" style="margin-top:.65rem"><span class="button-label">Pair / Save This Printer</span></button>
      `;
      $('button', item).addEventListener('click', async e => {
        const pin = $('.scan-pin', item)?.value?.trim() || '123456';
        const serialValue = serial || $('.scan-serial', item)?.value?.trim() || c.host;
        setButtonBusy(e.currentTarget, true, 'Pairing...');
        try { await savePrinter(c.host, c.host_name || c.machine_model || 'Centauri Carbon 2', c.portal_url, c.camera_url, serialValue, pin); }
        catch (err) { toast(err.message, 'error'); setButtonBusy(e.currentTarget, false); }
      });
      box.appendChild(item);
    });
  }

  function initSetup() {
    const scanButton = $('#scanButton');
    if (scanButton) scanButton.addEventListener('click', async () => {
      const subnet = $('#scanSubnet').value.trim();
      const scanStatus = $('#scanStatus');
      if (scanStatus) scanStatus.classList.remove('hidden');
      setButtonBusy(scanButton, true, 'Scanning...');
      try {
        const data = await api('/api/scan', { method: 'POST', body: JSON.stringify({ subnet }) });
        renderScanResults(data.candidates || []);
        toast(`Scan complete: ${(data.candidates || []).length} candidate(s)`, 'success');
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        if (scanStatus) scanStatus.classList.add('hidden');
        setButtonBusy(scanButton, false);
      }
    });

    const manual = $('#manualAddButton');
    if (manual) manual.addEventListener('click', async () => {
      const host = $('#manualHost').value.trim();
      const name = $('#manualName').value.trim() || 'Centauri Carbon 2';
      const serial = $('#manualSerial').value.trim() || host;
      const pin = $('#manualPin').value.trim() || '123456';
      if (!host) return toast('Enter a printer IP first.', 'warn');
      setButtonBusy(manual, true, 'Pairing...');
      try { await savePrinter(host, name, `http://${host}/`, `http://${host}:8080/`, serial, pin); }
      catch (err) { toast(err.message, 'error'); setButtonBusy(manual, false); }
    });

    const themeBtn = $('#saveSetupTheme');
    if (themeBtn) themeBtn.addEventListener('click', async () => {
      cfg.app.theme = $('#setupTheme').value;
      setButtonBusy(themeBtn, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Theme saved. Reloading...', 'success'); setTimeout(()=>location.reload(), 500); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(themeBtn, false); }
    });
  }

  async function loadFreshConfig() {
    const data = await api('/api/config');
    cfg = data.config;
    return data;
  }

  function populateFontSelects(fonts) {
    $$('.font-select').forEach(sel => {
      const role = sel.dataset.fontRole;
      sel.innerHTML = fonts.map(f => `<option value="${f}">${f}</option>`).join('');
      sel.value = cfg?.appearance?.fonts?.[role] || cfg?.appearance?.font_pack || 'Terminal Modern';
    });
  }

  function renderSettings() {
    const cardBox = $('#cardSettings');
    if (cardBox) {
      cardBox.innerHTML = (cfg.dashboard.cards || []).map(c => `
        <div class="setting-row" data-card-id="${c.id}">
          <div><strong>${c.label || c.id}</strong><small>${c.id}</small></div>
          <div class="setting-controls">
            <label><input class="toggle card-enabled" type="checkbox" ${c.enabled ? 'checked' : ''}> show</label>
            <input class="input card-order" type="number" value="${c.order ?? 99}">
          </div>
        </div>
      `).join('');
    }

    const actionBox = $('#actionSettings');
    if (actionBox) {
      actionBox.innerHTML = Object.entries(cfg.actions || {}).sort((a,b)=>(a[1].order||99)-(b[1].order||99)).map(([id,a]) => `
        <div class="setting-row" data-action-id="${id}">
          <div><strong>${a.label || id}</strong><small>${id}</small></div>
          <div class="setting-controls">
            <label><input class="toggle action-visible" type="checkbox" ${a.visible ? 'checked' : ''}> visible</label>
            <label><input class="toggle action-confirm" type="checkbox" ${a.requires_confirm ? 'checked' : ''}> confirm</label>
            <input class="input action-order" type="number" value="${a.order ?? 99}">
          </div>
        </div>
      `).join('');
    }

    const printerBox = $('#printerSettings');
    if (printerBox) {
      const entries = Object.entries(cfg.printers || {});
      printerBox.innerHTML = entries.length ? entries.map(([id,p]) => `
        <div class="setting-row">
          <div><strong>${p.name || id}</strong><small>${p.host} • SN: ${p.serial || 'unknown'} • PIN: ${p.access_code_set ? 'saved' : 'missing'}</small></div>
          <div class="setting-controls"><span class="pill">${cfg.app.default_printer === id ? 'Default' : id}</span></div>
        </div>
      `).join('') : '<div class="result-item"><strong>No printers configured</strong><span>Run setup to add one.</span></div>';
    }
  }

  function initSettings() {
    loadFreshConfig().then(data => {
      populateFontSelects(data.font_stacks || []);
      renderSettings();
      const editor = $('#configEditor');
      if (editor) editor.value = JSON.stringify(cfg, null, 2);
    }).catch(err => toast(err.message, 'error'));

    const saveTheme = $('#saveThemeButton');
    if (saveTheme) saveTheme.addEventListener('click', async () => {
      cfg.app.theme = $('#themeSelect').value;
      cfg.appearance.fonts = cfg.appearance.fonts || {};
      $$('.font-select').forEach(sel => cfg.appearance.fonts[sel.dataset.fontRole] = sel.value);
      setButtonBusy(saveTheme, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Appearance saved. Reloading...', 'success'); setTimeout(()=>location.reload(), 500); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveTheme, false); }
    });

    const saveLayout = $('#saveLayoutButton');
    if (saveLayout) saveLayout.addEventListener('click', async () => {
      $$('#cardSettings [data-card-id]').forEach(row => {
        const id = row.dataset.cardId;
        const c = cfg.dashboard.cards.find(x => x.id === id);
        if (c) { c.enabled = $('.card-enabled', row).checked; c.order = Number($('.card-order', row).value || 99); }
      });
      setButtonBusy(saveLayout, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Layout saved', 'success'); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveLayout, false); }
    });

    const saveActions = $('#saveActionsButton');
    if (saveActions) saveActions.addEventListener('click', async () => {
      $$('#actionSettings [data-action-id]').forEach(row => {
        const id = row.dataset.actionId;
        const a = cfg.actions[id];
        if (a) { a.visible = $('.action-visible', row).checked; a.requires_confirm = $('.action-confirm', row).checked; a.order = Number($('.action-order', row).value || 99); }
      });
      setButtonBusy(saveActions, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Buttons saved', 'success'); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveActions, false); }
    });

    const saveNetwork = $('#saveNetworkButton');
    if (saveNetwork) saveNetwork.addEventListener('click', async () => {
      cfg.network.allowed_subnets = $('#allowedSubnets').value.split('\n').map(x=>x.trim()).filter(Boolean);
      cfg.network.allowed_hosts = $('#allowedHosts').value.split('\n').map(x=>x.trim()).filter(Boolean);
      setButtonBusy(saveNetwork, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Network settings saved', 'success'); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveNetwork, false); }
    });

    const saveJson = $('#saveJsonButton');
    if (saveJson) saveJson.addEventListener('click', async () => {
      if (!confirm('Save the full raw JSON config? Bad JSON can make the app grumpy.')) return;
      try { cfg = JSON.parse($('#configEditor').value); }
      catch (err) { return toast('Invalid JSON: ' + err.message, 'error'); }
      setButtonBusy(saveJson, true, 'Saving...');
      try { await api('/api/config', { method:'POST', body:JSON.stringify({ config: cfg }) }); toast('Full config saved. Reloading...', 'success'); setTimeout(()=>location.reload(), 500); }
      catch (err) { toast(err.message, 'error'); }
      finally { setButtonBusy(saveJson, false); }
    });
  }

  async function refreshLogs() {
    const out = $('#logOutput');
    if (!out) return;
    try {
      const data = await api('/api/logs');
      out.innerHTML = (data.logs || []).map(l => `<div class="log-line"><span>${l.ts}</span> <strong class="${l.level}">[${l.level}]</strong> <span>${l.source}</span> — ${l.message}</div>`).join('') || '<div class="log-line">No logs yet.</div>';
    } catch (err) {
      out.innerHTML = `<div class="log-line"><strong class="ERROR">ERROR</strong> ${err.message}</div>`;
    }
  }

  function initLogs() {
    refreshLogs();
    setInterval(refreshLogs, 3000);
    const btn = $('#refreshLogs');
    if (btn) btn.addEventListener('click', refreshLogs);
  }

  if (page === 'dashboard') initDashboard();
  if (page === 'setup') initSetup();
  if (page === 'settings') initSettings();
  if (page === 'logs') initLogs();
})();
