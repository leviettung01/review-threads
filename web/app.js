/**
 * Auto Threads — ReviewThreads Studio Pro (Vanilla JS Frontend)
 * High-performance 60 FPS GPU Web App.
 * 100% Matching Layout & Features of Screenshots 1, 2, 3, 4.
 */

let appState = null;
let tiktokProducts = [];
let selectedTiktokProduct = null;
let selectedTiktokImagePath = null;
let activeRoleFile = 'RoleSkill.MD';
let activeRerenderProductId = null;
let saveDebounceTimer = null;

// Initialize App
document.addEventListener('DOMContentLoaded', () => {
  fetchState();
  initLogStream();
  bindEvents();
});

// Fetch full state from backend API
async function fetchState() {
  try {
    const res = await fetch('/api/state');
    appState = await res.json();
    renderUI();
  } catch (err) {
    console.error('Lỗi nạp state:', err);
  }
}

// Live SSE Log Streaming with Auto-Reconnect
function initLogStream() {
  const logBody = document.getElementById('logBody');
  const logBadge = document.getElementById('logCountBadge');
  let lineCount = 0;

  function connectSSE() {
    const evtSource = new EventSource('/api/logs/stream');

    evtSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        const line = document.createElement('div');
        line.className = 'log-line';
        line.textContent = data.message;
        logBody.appendChild(line);
        lineCount++;
        logBadge.textContent = `${lineCount} dòng`;

        if (logBody.children.length > 1000) {
          logBody.removeChild(logBody.firstChild);
        }
        logBody.scrollTop = logBody.scrollHeight;
      } catch (err) {
        console.error('Lỗi parse SSE log:', err);
      }
    };

    // Auto-reconnect khi SSE bị ngắt (timeout hoặc lỗi mạng)
    evtSource.onerror = () => {
      evtSource.close();
      console.warn('SSE disconnected — auto-reconnect sau 2s...');
      setTimeout(connectSSE, 2000);
    };
  }

  connectSSE();
}

// Tab Navigation Switching
function switchTab(tabKey) {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabKey);
  });

  document.querySelectorAll('.page-view').forEach(view => {
    view.classList.remove('active');
  });

  const targetView = document.getElementById(`page${capitalize(tabKey)}`);
  if (targetView) {
    targetView.classList.add('active');
  }

  if (tabKey === 'dashboard') {
    renderDashboard();
  } else if (tabKey === 'tiktokshop') {
    fetchTiktokProducts();
  } else if (tabKey === 'accounts') {
    fetchAccounts();
  }
}

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Format local file path to Flask image streaming endpoint
function getImgUrl(filePath) {
  if (!filePath) return '';
  return `/api/images?path=${encodeURIComponent(filePath)}`;
}

// Image Lightbox Modal Preview
function previewImage(path) {
  if (!path) return;
  const modal = document.getElementById('imageLightboxModal');
  const img = document.getElementById('lightboxImage');
  if (modal && img) {
    img.src = getImgUrl(path);
    modal.classList.remove('hidden');
  }
}

function closeLightbox() {
  const modal = document.getElementById('imageLightboxModal');
  if (modal) modal.classList.add('hidden');
}

// Render entire UI based on state
function renderUI() {
  if (!appState) return;

  // Sidebar Projects List
  const sbList = document.getElementById('sidebarProjectsList');
  if (sbList) {
    sbList.innerHTML = appState.projects.map(p => `
      <div class="sb-proj-item ${p.id === appState.active_project_id ? 'active' : ''}" onclick="selectAndOpenProject('${p.id}')">
        📁 ${escapeHtml(p.name)}
      </div>
    `).join('');
  }

  // Active Project Header Title in Studio
  const actProj = appState.active_project;
  const studioTitle = document.getElementById('studioHeaderTitle');
  if (studioTitle && actProj) {
    studioTitle.textContent = `🎭 Role Skill Pipeline — ${actProj.name}`;
  }

  // Settings inputs
  const st = appState.settings || {};
  
  // Roles
  const roleSelect = document.getElementById('roleSkillSelect');
  if (roleSelect) {
    roleSelect.innerHTML = (appState.roles || []).map(r => 
      `<option value="${r}" ${r === st.selected_role ? 'selected' : ''}>${escapeHtml(r)}</option>`
    ).join('');
    
    const roleBadge = document.getElementById('lblHeaderRoleBadge');
    if (roleBadge) {
      roleBadge.textContent = `Role: [${st.selected_role || 'RoleSkill.MD'}]`;
    }
  }

  // Voice mode
  const vmSelect = document.getElementById('voiceModeSelect');
  if (vmSelect) vmSelect.value = st.voice_mode_display || 'Review dẫn chuyện (narrative_review)';

  // LLM models
  const llmSelect = document.getElementById('llmModelSelect');
  if (llmSelect) {
    llmSelect.innerHTML = (appState.llm_models || []).map(m => 
      `<option value="${m}" ${m === st.llm_model ? 'selected' : ''}>${m}</option>`
    ).join('');
  }

  // Image models
  const imgSelect = document.getElementById('imgModelSelect');
  if (imgSelect) {
    imgSelect.innerHTML = (appState.img_models || []).map(m => 
      `<option value="${m}" ${m === st.img_model ? 'selected' : ''}>${m}</option>`
    ).join('');
  }

  // Image quality
  const iqSelect = document.getElementById('imgQualitySelect');
  if (iqSelect) iqSelect.value = st.image_quality || '1K (1024x1280 / Standard)';

  // Context
  const ctxSelect = document.getElementById('contextSelect');
  if (ctxSelect) ctxSelect.value = st.context || 'Bối cảnh theo Role Skill';

  // Spinners & Options
  const concInp = document.getElementById('concurrencyInput');
  if (concInp) concInp.value = st.concurrency || 8;
  const bdInp = document.getElementById('burstDelayInput');
  if (bdInp) bdInp.value = st.burst_delay || 10;
  const bsInp = document.getElementById('burstSizeInput');
  if (bsInp) bsInp.value = st.burst_size || 4;
  const upInp = document.getElementById('upscaleThreadsInput');
  if (upInp) upInp.value = st.upscale_threads || 1;
  const scInp = document.getElementById('sceneCountInput');
  if (scInp) scInp.value = st.scene_count || 8;

  // Voice engine & Vbee
  const veSelect = document.getElementById('voiceEngineSelect');
  if (veSelect) veSelect.value = st.voice_engine || 'gtts';
  const vbTok = document.getElementById('vbeeTokenInput');
  if (vbTok) vbTok.value = st.vbee_token || '';
  const vbApp = document.getElementById('vbeeAppIdInput');
  if (vbApp) vbApp.value = st.vbee_appid || '';
  const vbVc = document.getElementById('vbeeVoiceCodeInput');
  if (vbVc) vbVc.value = st.vbee_voice || 'vi_south_female_miien_48k';

  // Background settings
  const bgInp = document.getElementById('settingBgFolder');
  if (bgInp) bgInp.value = st.bg_folder || '';
  const chkNoDup = document.getElementById('chkNoDupBg');
  if (chkNoDup) chkNoDup.checked = st.no_duplicate_bg !== false;

  // Render Studio, Dashboard, and Vbee voices
  renderDashboard();
  renderProducts();
  fetchAccounts();
  fetchVbeeVoices(false);

  // Status button states
  const btnStart = document.getElementById('btnStartPipeline');
  const btnPause = document.getElementById('btnPausePipeline');
  if (btnStart && btnPause) {
    if (appState.is_running) {
      btnStart.disabled = true;
      btnPause.disabled = false;
    } else {
      btnStart.disabled = false;
      btnPause.disabled = true;
    }
  }
}

// Save setting and trigger explicit log in console
async function saveSettingAndLog(data) {
  await fetch('/api/settings/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
}

// ── VBEE VOICES FETCH & CACHE ──
async function fetchVbeeVoices(refresh = false) {
  try {
    const tok = document.getElementById('vbeeTokenInput')?.value || '';
    const aid = document.getElementById('vbeeAppIdInput')?.value || '';
    const res = await fetch(`/api/vbee/voices?refresh=${refresh}&token=${encodeURIComponent(tok)}&app_id=${encodeURIComponent(aid)}`);
    const data = await res.json();
    const voices = data.voices || [];
    const sel = document.getElementById('vbeeVoiceSelect');
    if (!sel) return;

    const currentVoice = appState?.settings?.vbee_voice || 'vi_south_female_miien_48k';

    sel.innerHTML = voices.map(v => {
      const code = v[0];
      const label = v[1];
      return `<option value="${code}" ${code === currentVoice ? 'selected' : ''}>${escapeHtml(label)} (${code})</option>`;
    }).join('');

    if (refresh) {
      alert(`🎙️ Đã tải thành công ${voices.length} giọng đọc Vbee!`);
    }
  } catch (err) {
    console.error('Lỗi fetch Vbee voices:', err);
  }
}

// Render Dashboard Grid
function renderDashboard() {
  if (!appState || !appState.projects) return;
  const grid = document.getElementById('dashProjectsGrid');
  if (!grid) return;

  grid.innerHTML = appState.projects.map(p => {
    const prods = p.products || [];
    const completed = prods.filter(pr => pr.status && (pr.status.includes('Hoàn thành') || pr.status.includes('✅'))).length;
    const running = prods.filter(pr => pr.status && pr.status.includes('⏳')).length;
    const failed = prods.filter(pr => pr.status && (pr.status.includes('❌') || pr.status.includes('Lỗi'))).length;
    
    return `
      <div class="proj-card-img2">
        <div class="proj-card-header">
          <h3>${escapeHtml(p.name)}</h3>
          <div class="proj-card-actions">
            <button class="btn-tag-role" onclick="openRoleEditor()">Role Skill</button>
            <button class="btn-text" onclick="deleteProjectFromDash('${p.id}')">🗑️</button>
          </div>
        </div>

        <div class="proj-card-stats">
          <div class="stat-box">
            <span class="num" style="color: var(--color-emerald);">${completed}</span>
            <span class="lbl">Hoàn thành</span>
          </div>
          <div class="stat-box">
            <span class="num" style="color: var(--color-cyan);">${running}</span>
            <span class="lbl">Đang xử lý</span>
          </div>
          <div class="stat-box">
            <span class="num" style="color: var(--color-red);">${failed}</span>
            <span class="lbl">Thất bại</span>
          </div>
        </div>

        <div class="proj-card-meta">
          <span class="status-tag-pill">${running > 0 ? 'ĐANG CHẠY' : (completed > 0 ? 'HOÀN THÀNH' : 'CHƯA CHẠY')}</span>
          <span style="font-size: 10px; color: var(--text-muted);">${p.created_at || ''}</span>
        </div>

        <button class="btn-open-proj-big" onclick="selectAndOpenProject('${p.id}')">
          ▶ Mở Dự Án
        </button>

        <div class="proj-card-bottom-btns">
          <button class="btn-sub-run" onclick="selectAndOpenProject('${p.id}')">🔄 Chạy lại toàn bộ</button>
          <button class="btn-sub-retry" onclick="selectAndOpenProject('${p.id}')">🔁 Retry phân cảnh lỗi</button>
        </div>
      </div>
    `;
  }).join('');
}

async function deleteProjectFromDash(pid) {
  if (confirm('Xóa dự án này?')) {
    await fetch('/api/projects/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: pid })
    });
    fetchState();
  }
}

async function selectAndOpenProject(pid) {
  await fetch('/api/projects/select', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: pid })
  });
  await fetchState();
  switchTab('studio');
}

// Render Studio Products List
function renderProducts() {
  const container = document.getElementById('productsListContainer');
  if (!container) return;
  const act = appState.active_project;
  if (!act || !act.products || act.products.length === 0) {
    container.innerHTML = `
      <div class="card empty-card" style="text-align: center; padding: 40px; color: var(--text-muted);">
        <p>💡 Chưa có sản phẩm nào trong dự án này.</p>
        <p>Bấm nút <strong>"+ Thêm ảnh SP"</strong> ở bên trái hoặc <strong>"🛍️ TiktokShop"</strong> để chọn sản phẩm vào dự án.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = act.products.map((prod, idx) => `
    <div class="product-card-img4" data-pid="${prod.id}">
      
      <!-- Top Bar of Product Card -->
      <div class="prod-card-top-bar">
        <span class="prod-title-head">📦 SP #${idx + 1}: ${escapeHtml(prod.title || 'Sản phẩm')}</span>
        <div class="prod-top-right-actions">
          <span class="btn btn-emerald btn-sm">${prod.status || 'Chưa chạy'}</span>
          <button class="btn btn-purple btn-sm" onclick="startSingleRender('${prod.id}')">🎬 Render</button>
          <button class="btn btn-orange btn-sm" onclick="openRerenderModal('${prod.id}')">🎬 Render lại</button>
          <button class="btn btn-danger-soft btn-sm btn-del-prod" data-pid="${prod.id}">🗑️</button>
        </div>
      </div>

      <!-- Main Body Split -->
      <div class="prod-card-body-split">
        
        <!-- Left Product Info Column -->
        <div class="prod-details-col">
          <div class="form-group">
            <label>Tên:</label>
            <input type="text" class="custom-input prod-title-input" value="${escapeHtml(prod.title || '')}">
          </div>

          <div class="form-group">
            <label>Yêu cầu riêng:</label>
            <textarea class="custom-input prod-info-input" rows="3">${escapeHtml(prod.info || '')}</textarea>
          </div>

          <!-- Reference Images Box -->
          <div class="prod-ref-box">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <label style="font-size: 10px; font-weight: 700;">Ảnh tham chiếu:</label>
              <button class="btn btn-purple btn-sm" style="font-size: 9px; padding: 2px 6px;">Thêm</button>
            </div>
            <div class="prod-ref-thumbs">
              ${prod.images && prod.images.length ? prod.images.map(img => `
                <img src="${getImgUrl(img)}" class="ref-thumb-img" onclick="previewImage('${img.replace(/\\/g, '\\\\')}')">
              `).join('') : '<span style="font-size: 10px; color: var(--text-muted); padding: 6px;">Chưa có ảnh</span>'}
            </div>
          </div>

          <div class="form-group margin-top">
            <label>Bối cảnh:</label>
            <select class="custom-select">
              <option>Bối cảnh theo Role Skill</option>
            </select>
          </div>

          <div class="prod-ctx-note">
            Tự động phân tích bối cảnh theo Role Skill khi để mặc định.
          </div>
        </div>

        <!-- Right Scenes 4-Column Table -->
        <div class="scenes-table-col">
          ${prod.scenes && prod.scenes.length ? prod.scenes.map((sc, sidx) => `
            <div class="scene-row-4col">
              <!-- Col 1: Scene Tag -->
              <div class="sc-col-idx">
                <span class="sc-tag">#${sidx + 1}</span>
                <span class="sc-ratio">${sc.aspect_ratio || '4:3'}</span>
                <span class="sc-done-badge">${sc.status === 'done' ? '✅ Xong' : '⏳ Chờ'}</span>
              </div>

              <!-- Col 2: Prompt Textarea -->
              <div class="sc-col-prompt">
                <span class="sc-label">📝 Prompt Ảnh:</span>
                <textarea class="sc-prompt-textarea sc-prompt-input" data-pid="${prod.id}" data-scid="${sc.id}">${escapeHtml(sc.prompt || '')}</textarea>
              </div>

              <!-- Col 3: Generated Image Thumbnail -->
              <div class="sc-col-img">
                ${sc.image_path ? `
                  <img src="${getImgUrl(sc.image_path)}" class="sc-img-thumb" onclick="previewImage('${sc.image_path.replace(/\\/g, '\\\\')}')">
                ` : `
                  <div class="sc-img-thumb" style="display: flex; align-items: center; justify-content: center; font-size: 16px;">🖼️</div>
                `}
                <span class="sc-label">🖼️ Ảnh gốc</span>
              </div>

              <!-- Col 4: Voiceover Text & Controls -->
              <div class="sc-col-voice">
                <span class="sc-label">🗣️ Text VoiceOver:</span>
                <textarea class="sc-voice-textarea sc-voice-input" data-pid="${prod.id}" data-scid="${sc.id}">${escapeHtml(sc.text_segment || '')}</textarea>
                <div class="sc-audio-controls">
                  <button class="btn-audio-play" onclick="playVoicePreview('${prod.id}', ${sc.id})">▶️</button>
                  <button class="btn-audio-play" onclick="pauseVoicePreview()">⏸️</button>
                  <button class="btn-audio-dl">⬇️</button>
                </div>
              </div>
            </div>
          `).join('') : `
            <div style="padding: 20px; text-align: center; color: var(--text-muted); font-style: italic;">
              💡 Chưa có phân cảnh — Bấm [▶️ Bắt đầu] để AI tạo kịch bản và ảnh tự động.
            </div>
          `}
        </div>

      </div>

    </div>
  `).join('');

  bindProductInputs();
}

// ── RE-RENDER MODAL LOGIC (INTERACTIVE SLEEK TOGGLES) ──
function openRerenderModal(pid) {
  activeRerenderProductId = pid;
  const prod = appState.active_project.products.find(p => p.id === pid);
  if (!prod) return;

  const modal = document.getElementById('rerenderModal');
  document.getElementById('lblRerenderModalProdTitle').textContent = `Sản phẩm: ${prod.title || ''}`;

  // Default state: ALL UNCHECKED so user explicitly picks what to re-render!
  ['Script', 'Images', 'Voice', 'Video'].forEach(key => {
    const chk = document.getElementById(`chkRerender${key}`);
    if (chk) {
      chk.checked = false;
      toggleRerenderSection(key);
    }
  });

  // Populate LLM models
  const llmSel = document.getElementById('rerenderLlmModel');
  if (llmSel && appState.llm_models) {
    llmSel.innerHTML = appState.llm_models.map(m => `<option value="${m}" ${m === appState.settings.llm_model ? 'selected' : ''}>${m}</option>`).join('');
  }

  // Populate Image models
  const imgSel = document.getElementById('rerenderImgModel');
  if (imgSel && appState.img_models) {
    imgSel.innerHTML = appState.img_models.map(m => `<option value="${m}" ${m === appState.settings.img_model ? 'selected' : ''}>${m}</option>`).join('');
  }

  // Populate Scene Checkboxes
  const checklist = document.getElementById('rerenderScenesChecklist');
  if (checklist) {
    if (prod.scenes && prod.scenes.length > 0) {
      checklist.innerHTML = `
        <label class="checkbox-label" style="width: 100%; border-bottom: 1px solid #cbd5e1; padding-bottom: 4px; font-weight: bold;">
          <input type="checkbox" id="chkRerenderAllScenes" checked onchange="toggleAllRerenderScenes(this)"> ☑ Tất cả (${prod.scenes.length} phân cảnh)
        </label>
      ` + prod.scenes.map((sc, i) => `
        <label class="checkbox-label" style="min-width: 130px;">
          <input type="checkbox" class="chk-rerender-scene-item" value="${sc.id}" checked> Phân cảnh #${i+1} (${sc.aspect_ratio || '4:3'})
        </label>
      `).join('');
    } else {
      checklist.innerHTML = `<span style="font-size: 11px; color: var(--text-muted);">Chưa có phân cảnh nào — LLM sẽ tạo mới kịch bản trước.</span>`;
    }
  }

  modal.classList.remove('hidden');
}

// Toggle Section active glow and options enablement
function toggleRerenderSection(key) {
  const chk = document.getElementById(`chkRerender${key}`);
  const box = document.getElementById(`boxRerender${key}`);
  const opt = document.getElementById(`optRerender${key}`);

  if (!chk || !box) return;

  if (chk.checked) {
    box.classList.add('active-box');
    if (opt) opt.classList.remove('disabled-opt');
  } else {
    box.classList.remove('active-box');
    if (opt) opt.classList.add('disabled-opt');
  }
}

function toggleAllRerenderScenes(master) {
  document.querySelectorAll('.chk-rerender-scene-item').forEach(chk => chk.checked = master.checked);
}

// Start single product render directly
async function startSingleRender(pid) {
  await fetch('/api/pipeline/rerender', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      product_id: pid,
      rerender_script: false,
      rerender_images: true,
      rerender_voice: true,
      rerender_video: true
    })
  });
  fetchState();
}

let activeAudio = null;
function playVoicePreview(pid, scid) {
  if (activeAudio) activeAudio.pause();
  const prod = appState.active_project.products.find(p => p.id === pid);
  if (prod && prod.voice_path) {
    activeAudio = new Audio(`/api/images?path=${encodeURIComponent(prod.voice_path)}`);
    activeAudio.play();
  }
}

function pauseVoicePreview() {
  if (activeAudio) activeAudio.pause();
}

// Bind product input changes with 500ms debounced saving
function bindProductInputs() {
  document.querySelectorAll('.prod-title-input').forEach(input => {
    input.addEventListener('input', (e) => {
      const pid = e.target.closest('.product-card-img4').dataset.pid;
      debouncedProductUpdate(pid, { title: e.target.value });
    });
  });

  document.querySelectorAll('.prod-info-input').forEach(input => {
    input.addEventListener('input', (e) => {
      const pid = e.target.closest('.product-card-img4').dataset.pid;
      debouncedProductUpdate(pid, { info: e.target.value });
    });
  });

  document.querySelectorAll('.sc-prompt-input').forEach(textarea => {
    textarea.addEventListener('input', (e) => {
      const pid = e.target.dataset.pid;
      const scid = parseInt(e.target.dataset.scid);
      const prod = appState.active_project.products.find(p => p.id === pid);
      if (prod) {
        const sc = prod.scenes.find(s => s.id === scid);
        if (sc) sc.prompt = e.target.value;
        debouncedProductUpdate(pid, { scenes: prod.scenes });
      }
    });
  });

  document.querySelectorAll('.btn-del-prod').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const pid = e.target.dataset.pid;
      if (confirm('Xóa sản phẩm này?')) {
        await fetch('/api/products/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: pid })
        });
        fetchState();
      }
    });
  });
}

function debouncedProductUpdate(pid, data) {
  clearTimeout(saveDebounceTimer);
  saveDebounceTimer = setTimeout(async () => {
    await fetch('/api/products/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: pid, ...data })
    });
  }, 500);
}

// ── TIKTOKSHOP SPY CATALOG ─────────────────────────────────────────────
async function fetchTiktokProducts() {
  try {
    const res = await fetch('/api/tiktokshop/list');
    const data = await res.json();
    tiktokProducts = data.products || [];
    renderTiktokMainTable();
  } catch (err) {
    console.error('Lỗi fetch tiktok products:', err);
  }
}

function renderTiktokMainTable() {
  const tbody = document.getElementById('ttProductsTbodyMain');
  if (!tbody) return;

  if (tiktokProducts.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td>1</td>
        <td>1733919721642..</td>
        <td>[Đặc Quyền] 123 Farm Bưởi Xí Muội Nước Trái Cây Có Đặc 500g Giữ Vitamin C Tự Nhiên Thích Hợp ...</td>
        <td>95.000đ</td>
        <td>Thành công</td>
        <td>Thành công</td>
        <td><span style="color: #0284c7; font-weight: 700;">✅ 2 files</span></td>
      </tr>
      <tr>
        <td>2</td>
        <td>1731214537132..</td>
        <td>Set Sâm Bổ Đao Bông Cúc Hạt Chia 10lít thơm ngon nấu 12Lít nước sâm đậm đà (KÈM CHỈ TỰ MUA ...</td>
        <td>35.400đ</td>
        <td>Thành công</td>
        <td>Thành công</td>
        <td><span style="color: #0284c7; font-weight: 700;">✅ 1 files</span></td>
      </tr>
      <tr>
        <td>3</td>
        <td>1732219600106..</td>
        <td>Hạt dẻ rim chanh dây 500gram nhà làm BaoQuangFood</td>
        <td>99.000đ</td>
        <td>Thành công</td>
        <td>Thành công</td>
        <td><span style="color: #0284c7; font-weight: 700;">✅ 3 files</span></td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = tiktokProducts.map((p, idx) => `
    <tr>
      <td>${idx + 1}</td>
      <td>${p.product_id || ''}</td>
      <td>${escapeHtml(p.title || '')}</td>
      <td>${p.price || ''}</td>
      <td>Thành công</td>
      <td>Thành công</td>
      <td><span style="color: #0284c7; font-weight: 700;">✅ ${p.image_files ? p.image_files.length : 1} files</span></td>
    </tr>
  `).join('');
}

// ── TIKTOKSHOP STUDIO MODAL DIALOG POPUP ──────────────────────────────
async function openTiktokModalInStudio() {
  const modal = document.getElementById('tiktokModal');
  modal.classList.remove('hidden');
  
  try {
    const res = await fetch('/api/tiktokshop/list');
    const data = await res.json();
    tiktokProducts = data.products || [];
    renderTiktokModalTable();
  } catch (err) {
    console.error('Lỗi fetch tiktok products modal:', err);
  }
}

function renderTiktokModalTable() {
  const tbody = document.getElementById('ttModalTableBody');
  if (!tbody) return;
  const query = (document.getElementById('editTiktokModalSearch').value || '').toLowerCase();
  
  const filtered = tiktokProducts.filter(p => {
    const pid = String(p.product_id || '').toLowerCase();
    const title = String(p.title || '').toLowerCase();
    return !query || pid.includes(query) || title.includes(query);
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 16px;">Không tìm thấy sản phẩm cào nào.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map((p, idx) => `
    <tr onclick="selectTiktokModalProduct(${idx})" class="${selectedTiktokProduct && selectedTiktokProduct.product_id === p.product_id ? 'selected' : ''}">
      <td>${idx + 1}</td>
      <td>${p.product_id || ''}</td>
      <td><strong>${escapeHtml(p.title || '')}</strong></td>
      <td>${p.price || ''}</td>
      <td>
        <span style="color: ${(p.voice_files && p.voice_files.length > 0) ? '#10b981' : '#64748b'}; font-weight: 700;">
          ${(p.voice_files && p.voice_files.length > 0) ? `✅ ${p.voice_files.length} files` : '0 files'}
        </span>
      </td>
    </tr>
  `).join('');
}

function selectTiktokModalProduct(idx) {
  const query = (document.getElementById('editTiktokModalSearch').value || '').toLowerCase();
  const filtered = tiktokProducts.filter(p => {
    const pid = String(p.product_id || '').toLowerCase();
    const title = String(p.title || '').toLowerCase();
    return !query || pid.includes(query) || title.includes(query);
  });
  
  selectedTiktokProduct = filtered[idx];
  selectedTiktokImagePath = null;
  
  if (!selectedTiktokProduct) return;

  document.getElementById('ttModalDetailTitle').textContent = selectedTiktokProduct.title || 'Sản phẩm';
  document.getElementById('ttModalDetailDesc').textContent = selectedTiktokProduct.description || 'Không có mô tả.';

  const imgs = selectedTiktokProduct.image_files || [];
  renderTiktokProductGallery(imgs, 0);

  const voiceFiles = selectedTiktokProduct.voice_files || [];
  document.getElementById('ttModalVoiceInfoLabel').textContent = `🗣️ Lồng tiếng: ${voiceFiles.length} file voice`;
  document.getElementById('btnConfirmTiktokModal').disabled = false;
  renderTiktokModalTable();
}

function renderTiktokProductGallery(imgs, activeIdx = 0) {
  const gallery = document.getElementById('ttModalGallery');
  const btnCrop = document.getElementById('btnCropTiktokModal');
  const btnCollage = document.getElementById('btnCollageTiktokModal');

  if (imgs && imgs.length > 0) {
    selectedTiktokImagePath = imgs[activeIdx] || imgs[0];
    document.getElementById('ttModalSelectedImgLabel').textContent = `Đã chọn: ${getFileName(selectedTiktokImagePath)}`;
    gallery.innerHTML = imgs.map((img, i) => `
      <img src="${getImgUrl(img)}" class="tt-img-item ${i === activeIdx ? 'selected' : ''}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 6px; cursor: pointer; border: 2px solid ${i === activeIdx ? '#7c3aed' : 'transparent'};" onclick="selectTiktokModalImage('${img.replace(/\\/g, '\\\\')}', this)">
    `).join('');
    if (btnCrop) btnCrop.disabled = false;
    if (btnCollage) btnCollage.disabled = false;
  } else {
    selectedTiktokImagePath = null;
    document.getElementById('ttModalSelectedImgLabel').textContent = 'Chưa chọn ảnh sản phẩm';
    gallery.innerHTML = `<span style="font-size: 11px; color: var(--text-muted);">Không có ảnh sản phẩm</span>`;
    if (btnCrop) btnCrop.disabled = true;
    if (btnCollage) btnCollage.disabled = true;
  }
}

function selectTiktokModalImage(path, el) {
  selectedTiktokImagePath = path;
  document.getElementById('ttModalSelectedImgLabel').textContent = `Đã chọn: ${getFileName(path)}`;
  document.querySelectorAll('#ttModalGallery img').forEach(img => img.style.borderColor = 'transparent');
  if (el) el.style.borderColor = '#7c3aed';
}

function getFileName(p) {
  return p ? p.split(/[\\/]/).pop() : '';
}

// ── ACCOUNT MANAGER LOGIC ──
async function fetchAccounts() {
  try {
    const res = await fetch('/api/accounts');
    const data = await res.json();
    const accs = data.accounts || [];
    const container = document.getElementById('accListContainer');
    const titleLbl = document.getElementById('lblAccListTitle');
    const sbAccLbl = document.getElementById('lblSidebarAccCount');
    
    const activeCount = accs.filter(a => a.active).length;
    if (titleLbl) titleLbl.textContent = `👥 DANH SÁCH TÀI KHOẢN (${accs.length} tài khoản)`;
    if (sbAccLbl) sbAccLbl.textContent = `👥 ${activeCount} tài khoản hoạt động`;

    if (!container) return;
    
    if (accs.length === 0) {
      container.innerHTML = `<div style="padding: 30px; text-align: center; color: var(--text-muted);">Chưa có tài khoản nào. Bấm nút bên trái để mở trình duyệt đăng nhập hoặc thêm thủ công!</div>`;
      return;
    }

    container.innerHTML = accs.map((acc, idx) => {
      const isGoogle = acc.name.startsWith("Google_") || acc.name.toLowerCase().includes("google");
      return `
        <div class="acc-item-card" style="display: flex; justify-content: space-between; align-items: center; background: #ffffff; border: 1px solid var(--border-color); padding: 10px 14px; border-radius: 8px; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
          <div class="acc-item-info">
            <strong style="font-size: 13px; color: var(--text-dark);">${escapeHtml(acc.name)}</strong>
            <div style="color: var(--text-muted); font-size: 10px; margin-top: 3px;">
              Proxy/ID: <code>${escapeHtml(acc.proxy_url || 'Default')}</code> | Done: <strong>${acc.jobs_done || 0}</strong>
              ${acc.credits !== null && acc.credits !== undefined && acc.credits >= 0 ? ` | Credits: <strong style="color: var(--color-emerald);">${acc.credits}</strong>` : ''}
            </div>
          </div>
          <div class="acc-item-actions" style="display: flex; align-items: center; gap: 6px;">
            ${isGoogle ? `
              <select class="custom-select" style="padding: 2px 6px; font-size: 10px; width: 65px; height: 26px;" onchange="updateAccountTag(${idx}, this.value)">
                <option value="Pro" ${acc.tag === 'Pro' ? 'selected' : ''}>Pro</option>
                <option value="Ultra" ${acc.tag === 'Ultra' ? 'selected' : ''}>Ultra</option>
              </select>
              <button class="btn btn-indigo btn-sm" title="Kiểm tra Token & Đồng bộ Credits" style="padding: 3px 8px; font-size: 11px;" onclick="singleRefreshAccount(${idx})">🔍 Check</button>
            ` : ''}
            <button class="btn btn-purple btn-sm" title="Login lại tài khoản qua trình duyệt" style="padding: 3px 8px; font-size: 11px;" onclick="singleReloginAccount(${idx})">🔑 Login</button>
            <label style="font-size: 11px; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 4px; margin-left: 4px;">
              <input type="checkbox" ${acc.active ? 'checked' : ''} onchange="toggleAccount(${idx})"> Bật
            </label>
            <button class="btn btn-red btn-sm" style="padding: 3px 8px; font-size: 11px;" onclick="deleteAccount(${idx})">🗑️</button>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Lỗi fetch accounts:', err);
  }
}

async function singleRefreshAccount(idx) {
  await fetch('/api/accounts/single_refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index: idx })
  });
}

async function singleReloginAccount(idx) {
  await fetch('/api/accounts/single_relogin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index: idx })
  });
}

async function updateAccountTag(idx, tag) {
  await fetch('/api/accounts/update_tag', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index: idx, tag })
  });
  fetchAccounts();
}

async function toggleAccount(idx) {
  await fetch('/api/accounts/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index: idx })
  });
  fetchAccounts();
}

async function deleteAccount(idx) {
  if (confirm('Xóa tài khoản này khỏi danh sách?')) {
    await fetch('/api/accounts/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: idx })
    });
    fetchAccounts();
  }
}

// Bind General UI Events
function bindEvents() {
  // Navigation
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tab = e.target.closest('.nav-btn').dataset.tab;
      if (tab) switchTab(tab);
    });
  });

  // Back button in Studio header
  const btnBack = document.getElementById('btnBackToDashboard');
  if (btnBack) btnBack.addEventListener('click', () => switchTab('dashboard'));

  // Create project buttons
  const createProjHandler = async () => {
    const name = prompt('Nhập tên dự án mới:');
    if (name) {
      await fetch('/api/projects/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      fetchState();
    }
  };
  
  const btnSidebarNew = document.getElementById('btnSidebarNewProject');
  if (btnSidebarNew) btnSidebarNew.addEventListener('click', createProjHandler);
  const btnDashCreate = document.getElementById('btnDashCreateProj');
  if (btnDashCreate) btnDashCreate.addEventListener('click', createProjHandler);

  // Vbee Voice Load Button & Voice Select Binding
  const btnFetchVbee = document.getElementById('btnFetchVbeeVoices');
  if (btnFetchVbee) {
    btnFetchVbee.addEventListener('click', () => fetchVbeeVoices(true));
  }

  const vbeeSelect = document.getElementById('vbeeVoiceSelect');
  if (vbeeSelect) {
    vbeeSelect.addEventListener('change', () => {
      const selectedCode = vbeeSelect.value;
      const codeInp = document.getElementById('vbeeVoiceCodeInput');
      if (codeInp) codeInp.value = selectedCode;
      saveSettingAndLog({ vbee_voice: selectedCode });
    });
  }

  // Settings change listeners for explicit logging
  const bindChangeLog = (id, key) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('change', () => {
        saveSettingAndLog({ [key]: el.value });
      });
    }
  };

  bindChangeLog('roleSkillSelect', 'selected_role');
  bindChangeLog('llmModelSelect', 'llm_model');
  bindChangeLog('voiceModeSelect', 'voice_mode');
  bindChangeLog('imgModelSelect', 'img_model');
  bindChangeLog('imgQualitySelect', 'image_quality');
  bindChangeLog('contextSelect', 'context');
  bindChangeLog('concurrencyInput', 'concurrency');
  bindChangeLog('burstDelayInput', 'burst_delay');
  bindChangeLog('burstSizeInput', 'burst_size');
  bindChangeLog('sceneCountInput', 'scene_count');
  bindChangeLog('voiceEngineSelect', 'voice_engine');
  bindChangeLog('vbeeTokenInput', 'vbee_token');
  bindChangeLog('vbeeAppIdInput', 'vbee_appid');

  // Account Page Control Bindings
  const btnOpenAdmin = document.getElementById('btnOpenWebAdmin');
  if (btnOpenAdmin) {
    btnOpenAdmin.addEventListener('click', async () => {
      await fetch('/api/accounts/open_admin', { method: 'POST' });
    });
  }

  const btnLoginBrowser = document.getElementById('btnLoginBrowser');
  if (btnLoginBrowser) {
    btnLoginBrowser.addEventListener('click', async () => {
      const typeSelect = document.getElementById('accLoginTypeSelect');
      const loginType = typeSelect ? typeSelect.value : 'xAI Grok (SuperGrok)';
      await fetch('/api/accounts/login_browser', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: loginType })
      });
    });
  }

  const btnSyncCliproxy = document.getElementById('btnSyncCliproxy');
  if (btnSyncCliproxy) {
    btnSyncCliproxy.addEventListener('click', async () => {
      await fetch('/api/accounts/sync_cliproxy', { method: 'POST' });
      fetchAccounts();
    });
  }

  const btnCheckTokens = document.getElementById('btnCheckTokens');
  if (btnCheckTokens) {
    btnCheckTokens.addEventListener('click', async () => {
      await fetch('/api/accounts/check_tokens', { method: 'POST' });
      fetchAccounts();
    });
  }

  const btnAddAccManual = document.getElementById('btnAddAccManual');
  if (btnAddAccManual) {
    btnAddAccManual.addEventListener('click', async () => {
      const nameInp = document.getElementById('accManualName');
      const proxyInp = document.getElementById('accManualProxy');
      const keyInp = document.getElementById('accManualKey');

      const name = nameInp ? nameInp.value.trim() : '';
      const proxy_url = proxyInp ? proxyInp.value.trim() : '';
      const api_key = keyInp ? keyInp.value.trim() : '';
      
      if (!name) {
        alert('Vui lòng nhập tên tài khoản!');
        return;
      }
      await fetch('/api/accounts/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, proxy_url, api_key })
      });
      if (nameInp) nameInp.value = '';
      if (proxyInp) proxyInp.value = '';
      if (keyInp) keyInp.value = '';
      fetchAccounts();
    });
  }

  // Add Product Button
  const btnAddProd = document.getElementById('btnAddProduct');
  if (btnAddProd) {
    btnAddProd.addEventListener('click', async () => {
      await fetch('/api/products/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'Sản phẩm mới' })
      });
      fetchState();
    });
  }

  // Delete Last / All Products
  const btnDelLast = document.getElementById('btnDelLastProduct');
  if (btnDelLast) {
    btnDelLast.addEventListener('click', async () => {
      const prods = appState.active_project.products || [];
      if (prods.length > 0) {
        const lastId = prods[prods.length - 1].id;
        await fetch('/api/products/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: lastId })
        });
        fetchState();
      }
    });
  }

  const btnDelAll = document.getElementById('btnDelAllProducts');
  if (btnDelAll) {
    btnDelAll.addEventListener('click', async () => {
      if (confirm('Xóa TẤT CẢ sản phẩm trong dự án này?')) {
        const prods = appState.active_project.products || [];
        for (const pr of prods) {
          await fetch('/api/products/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: pr.id })
          });
        }
        fetchState();
      }
    });
  }

  // TiktokShop Modal inside Studio
  const btnStudioTT = document.getElementById('btnStudioTiktokShop');
  if (btnStudioTT) btnStudioTT.addEventListener('click', openTiktokModalInStudio);

  const btnCloseTTModal = document.getElementById('btnCloseTiktokModal');
  if (btnCloseTTModal) btnCloseTTModal.addEventListener('click', () => {
    document.getElementById('tiktokModal').classList.add('hidden');
  });
  const btnCancelTTModal = document.getElementById('btnCancelTiktokModal');
  if (btnCancelTTModal) btnCancelTTModal.addEventListener('click', () => {
    document.getElementById('tiktokModal').classList.add('hidden');
  });

  const editTTModalSearch = document.getElementById('editTiktokModalSearch');
  if (editTTModalSearch) editTTModalSearch.addEventListener('input', renderTiktokModalTable);

  const btnConfirmTTModal = document.getElementById('btnConfirmTiktokModal');
  if (btnConfirmTTModal) {
    btnConfirmTTModal.addEventListener('click', async () => {
      if (!selectedTiktokProduct) return;
      await fetch('/api/tiktokshop/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: selectedTiktokProduct.title,
          description: selectedTiktokProduct.description,
          image_path: selectedTiktokImagePath,
          all_images: selectedTiktokProduct.image_files || [],
          voice_files: selectedTiktokProduct.voice_files || []
        })
      });
      document.getElementById('tiktokModal').classList.add('hidden');
      await fetchState();
    });
  }

  // Collage Modal Events
  const btnCollageTT = document.getElementById('btnCollageTiktokModal');
  if (btnCollageTT) {
    btnCollageTT.addEventListener('click', () => {
      if (!selectedTiktokProduct || !selectedTiktokProduct.image_files || selectedTiktokProduct.image_files.length === 0) {
        alert('Không có ảnh để ghép!');
        return;
      }
      openCollageModal();
    });
  }
  const btnCloseCollage = document.getElementById('btnCloseCollageModal');
  if (btnCloseCollage) btnCloseCollage.addEventListener('click', () => document.getElementById('collageModal').classList.add('hidden'));
  const btnCancelCollage = document.getElementById('btnCancelCollageModal');
  if (btnCancelCollage) btnCancelCollage.addEventListener('click', () => document.getElementById('collageModal').classList.add('hidden'));
  const btnSubmitCollage = document.getElementById('btnSubmitCollageModal');
  if (btnSubmitCollage) btnSubmitCollage.addEventListener('click', submitCollage);

  // Crop Modal Events
  const btnCropTT = document.getElementById('btnCropTiktokModal');
  if (btnCropTT) {
    btnCropTT.addEventListener('click', () => {
      if (!selectedTiktokImagePath) {
        alert('Vui lòng chọn 1 ảnh để cắt!');
        return;
      }
      openCropModal();
    });
  }
  const btnCloseCrop = document.getElementById('btnCloseCropModal');
  if (btnCloseCrop) btnCloseCrop.addEventListener('click', () => document.getElementById('cropModal').classList.add('hidden'));
  const btnCancelCrop = document.getElementById('btnCancelCropModal');
  if (btnCancelCrop) btnCancelCrop.addEventListener('click', () => document.getElementById('cropModal').classList.add('hidden'));
  const btnSubmitCrop = document.getElementById('btnSubmitCropModal');
  if (btnSubmitCrop) btnSubmitCrop.addEventListener('click', submitCrop);

  // Range Slider events for Crop preview
  ['X', 'Y', 'W', 'H'].forEach(key => {
    const range = document.getElementById(`cropRange${key}`);
    const val = document.getElementById(`cropVal${key}`);
    if (range && val) {
      range.addEventListener('input', () => { val.textContent = range.value; });
    }
  });

  // Re-Render Modal Events
  const btnCloseRerender = document.getElementById('btnCloseRerenderModal');
  if (btnCloseRerender) btnCloseRerender.addEventListener('click', () => {
    document.getElementById('rerenderModal').classList.add('hidden');
  });
  const btnCancelRerender = document.getElementById('btnCancelRerenderModal');
  if (btnCancelRerender) btnCancelRerender.addEventListener('click', () => {
    document.getElementById('rerenderModal').classList.add('hidden');
  });

  const btnConfirmRerender = document.getElementById('btnConfirmRerenderModal');
  if (btnConfirmRerender) {
    btnConfirmRerender.addEventListener('click', async () => {
      if (!activeRerenderProductId) return;

      const chkScript = document.getElementById('chkRerenderScript').checked;
      const chkImages = document.getElementById('chkRerenderImages').checked;
      const chkVoice = document.getElementById('chkRerenderVoice').checked;
      const chkVideo = document.getElementById('chkRerenderVideo').checked;

      if (!chkScript && !chkImages && !chkVoice && !chkVideo) {
        alert('⚠️ Vui lòng tích chọn ít nhất 1 thành phần bạn muốn Render lại (Kịch bản, Ảnh AI, Voiceover hoặc Video)!');
        return;
      }
      
      const selScenes = [];
      document.querySelectorAll('.chk-rerender-scene-item:checked').forEach(chk => {
        selScenes.push(parseInt(chk.value));
      });

      const payload = {
        product_id: activeRerenderProductId,
        rerender_script: chkScript,
        rerender_images: chkImages,
        rerender_voice: chkVoice,
        rerender_video: chkVideo,
        llm_model: document.getElementById('rerenderLlmModel').value,
        voice_mode: document.getElementById('rerenderVoiceMode').value,
        img_model: document.getElementById('rerenderImgModel').value,
        voice_engine: document.getElementById('rerenderVoiceEngine').value,
        selected_scenes: selScenes
      };

      await fetch('/api/pipeline/rerender', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      document.getElementById('rerenderModal').classList.add('hidden');
      fetchState();
    });
  }

  // Start / Pause Pipeline
  const btnStart = document.getElementById('btnStartPipeline');
  if (btnStart) {
    btnStart.addEventListener('click', async () => {
      const res = await fetch('/api/pipeline/start', { method: 'POST' });
      const data = await res.json();
      if (data.error) alert(data.error);
      else fetchState();
    });
  }
  const btnPause = document.getElementById('btnPausePipeline');
  if (btnPause) {
    btnPause.addEventListener('click', async () => {
      await fetch('/api/pipeline/pause', { method: 'POST' });
      fetchState();
    });
  }

  // Clear & Copy & Expand logs
  const btnClrLogs = document.getElementById('btnClearLogs');
  if (btnClrLogs) {
    btnClrLogs.addEventListener('click', () => {
      document.getElementById('logBody').innerHTML = '';
      document.getElementById('logCountBadge').textContent = '0 dòng';
    });
  }

  const btnCopyLogs = document.getElementById('btnCopyLogs');
  if (btnCopyLogs) {
    btnCopyLogs.addEventListener('click', () => {
      const logText = document.getElementById('logBody').innerText;
      if (!logText) {
        alert('Chưa có log nào để copy!');
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(logText).then(() => {
          alert('📋 Đã copy toàn bộ log vào clipboard!');
        }).catch(() => {
          fallbackCopyText(logText);
        });
      } else {
        fallbackCopyText(logText);
      }
    });
  }

  const btnToggleLog = document.getElementById('btnToggleLog');
  if (btnToggleLog) {
    btnToggleLog.addEventListener('click', () => {
      const footer = document.getElementById('logFooter');
      if (footer) {
        const isExpanded = footer.classList.toggle('expanded-log');
        btnToggleLog.textContent = isExpanded ? '▼ Thu gọn' : '↔️ Mở rộng';
      }
    });
  }

  // Role Skill Editor Modal — bind ALL Role buttons
  const btnRoleGlobal = document.getElementById('btnOpenRoleSkillGlobal');
  if (btnRoleGlobal) btnRoleGlobal.addEventListener('click', openRoleEditor);
  const btnRoleEdit = document.getElementById('btnOpenRoleEditor');
  if (btnRoleEdit) btnRoleEdit.addEventListener('click', openRoleEditor);
  const btnRoleMD = document.getElementById('btnEditRoleMD');
  if (btnRoleMD) btnRoleMD.addEventListener('click', openRoleEditor);

  // Studio top bar "Thêm Role" and "Xóa Role" buttons open the Role Editor modal
  const btnAddRoleSkill = document.getElementById('btnAddRoleSkill');
  if (btnAddRoleSkill) {
    btnAddRoleSkill.addEventListener('click', async () => {
      const name = prompt('Nhập tên file Role Skill mới (vd: TenRole.MD):');
      if (name) {
        await fetch('/api/roles/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, content: `# ROLE SKILL: ${name}\n## 1. PHÂN TÍCH SẢN PHẨM\n` })
        });
        activeRoleFile = name.endsWith('.MD') || name.endsWith('.md') ? name : name + '.MD';
        openRoleEditor();
        fetchState();
      }
    });
  }
  const btnDelRoleSkill = document.getElementById('btnDelRoleSkill');
  if (btnDelRoleSkill) {
    btnDelRoleSkill.addEventListener('click', async () => {
      if (!activeRoleFile || activeRoleFile === 'RoleSkill.MD') {
        alert('Không thể xóa file Role Skill mặc định!');
        return;
      }
      if (confirm(`Xóa file Role Skill: ${activeRoleFile}?`)) {
        await fetch('/api/roles/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: activeRoleFile })
        });
        activeRoleFile = 'RoleSkill.MD';
        openRoleEditor();
        fetchState();
      }
    });
  }

  const btnCloseModal = document.getElementById('btnCloseRoleModal');
  if (btnCloseModal) {
    btnCloseModal.addEventListener('click', () => {
      document.getElementById('roleModal').classList.add('hidden');
    });
  }

  // Role modal actions: Add role, Del role (inside modal)
  const btnModalAdd = document.getElementById('btnModalAddRole');
  if (btnModalAdd) {
    btnModalAdd.addEventListener('click', async () => {
      const name = prompt('Nhập tên file Role Skill mới (vd: TenRole.MD):');
      if (name) {
        await fetch('/api/roles/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, content: `# ROLE SKILL: ${name}\n## 1. PHÂN TÍCH SẢN PHẨM\n` })
        });
        activeRoleFile = name.endsWith('.MD') || name.endsWith('.md') ? name : name + '.MD';
        openRoleEditor();
        fetchState();
      }
    });
  }

  const btnModalDel = document.getElementById('btnModalDelRole');
  if (btnModalDel) {
    btnModalDel.addEventListener('click', async () => {
      if (confirm(`Xóa file Role Skill: ${activeRoleFile}?`)) {
        await fetch('/api/roles/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: activeRoleFile })
        });
        activeRoleFile = 'RoleSkill.MD';
        openRoleEditor();
        fetchState();
      }
    });
  }
}

function fallbackCopyText(text) {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  document.body.appendChild(textArea);
  textArea.select();
  document.execCommand('copy');
  document.body.removeChild(textArea);
  alert('📋 Đã copy toàn bộ log vào clipboard!');
}

// Role Editor Modal — uses data attributes for Vietnamese filename safety
async function openRoleEditor() {
  const modal = document.getElementById('roleModal');
  modal.classList.remove('hidden');
  
  const res = await fetch('/api/roles/list');
  const data = await res.json();
  
  const listEl = document.getElementById('roleFileList');
  const roles = data.roles || [];
  
  document.getElementById('lblRoleTotalFiles').textContent = `✔ Tổng ${roles.length} file Role Skill trong Data`;

  // Use data-role attribute instead of inline onclick to support Vietnamese filenames
  listEl.innerHTML = roles.map(r => `
    <div class="role-file-item ${r.name === activeRoleFile ? 'active' : ''}" data-role="${escapeHtml(r.name)}">
      📄 ${escapeHtml(r.name)} <span style="color: var(--text-muted); font-size: 10px;">(${formatBytes(r.size)})</span>
    </div>
  `).join('');

  // Bind click event via delegation
  listEl.querySelectorAll('.role-file-item').forEach(item => {
    item.addEventListener('click', () => {
      const roleName = item.getAttribute('data-role');
      if (roleName) loadRoleFile(roleName);
    });
  });

  // Load the first available role if activeRoleFile doesn't exist in the list
  const roleNames = roles.map(r => r.name);
  if (!roleNames.includes(activeRoleFile) && roleNames.length > 0) {
    activeRoleFile = roleNames[0];
  }
  loadRoleFile(activeRoleFile);
}

async function loadRoleFile(name) {
  activeRoleFile = name;
  document.getElementById('lblActiveRoleFile').textContent = `Đang chọn: ${name}`;
  
  // Highlight active item in list
  document.querySelectorAll('.role-file-item').forEach(item => {
    item.classList.toggle('active', item.getAttribute('data-role') === name);
  });

  try {
    const res = await fetch(`/api/roles/read?name=${encodeURIComponent(name)}`);
    const data = await res.json();
    if (data.content !== undefined) {
      const txtArea = document.getElementById('txtRoleContent');
      txtArea.value = data.content;
      const lines = data.content.split('\n').length;
      const bytes = new Blob([data.content]).size;
      document.getElementById('lblRoleFileStats').textContent = `${lines} dòng — ${formatBytes(bytes)}`;
    } else {
      document.getElementById('txtRoleContent').value = '';
      document.getElementById('lblRoleFileStats').textContent = '0 dòng — 0 B';
    }
  } catch (err) {
    console.error('Lỗi đọc Role Skill:', err);
    document.getElementById('txtRoleContent').value = '';
  }
}

document.getElementById('btnSaveRoleContent').addEventListener('click', async () => {
  const content = document.getElementById('txtRoleContent').value;
  await fetch('/api/roles/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: activeRoleFile, content })
  });
  alert('💾 Đã lưu file Data Role Skill thành công!');
  fetchState();
});

function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── TIKTOKSHOP COLLAGE & CROP FUNCTIONS ─────────────────────────────────
function openCollageModal() {
  const modal = document.getElementById('collageModal');
  const grid = document.getElementById('collageThumbGrid');
  if (!modal || !grid) return;

  const imgs = selectedTiktokProduct.image_files || [];
  grid.innerHTML = imgs.map((path, idx) => {
    const isChecked = idx < 2;
    return `
      <div class="collage-item-card ${isChecked ? 'selected' : ''}" id="collageCard_${idx}" onclick="toggleCollageCard(${idx})">
        <div class="collage-item-badge" id="collageBadge_${idx}" style="display: ${isChecked ? 'flex' : 'none'};">✓</div>
        <img src="${getImgUrl(path)}">
        <div class="collage-item-label">
          <input type="checkbox" class="collage-cb" id="collageCb_${idx}" data-path="${escapeHtml(path)}" ${isChecked ? 'checked' : ''} onclick="event.stopPropagation(); toggleCollageCard(${idx});">
          <span>Ảnh ${idx + 1}</span>
        </div>
      </div>
    `;
  }).join('');

  updateCollageSelectedCount();
  modal.classList.remove('hidden');
}

function toggleCollageCard(idx) {
  const cb = document.getElementById(`collageCb_${idx}`);
  const card = document.getElementById(`collageCard_${idx}`);
  const badge = document.getElementById(`collageBadge_${idx}`);
  if (!cb || !card) return;

  cb.checked = !cb.checked;
  if (cb.checked) {
    card.classList.add('selected');
    if (badge) badge.style.display = 'flex';
  } else {
    card.classList.remove('selected');
    if (badge) badge.style.display = 'none';
  }
  updateCollageSelectedCount();
}

function updateCollageSelectedCount() {
  const cbs = document.querySelectorAll('.collage-cb:checked');
  const lbl = document.getElementById('lblCollageSelectedCount');
  if (lbl) {
    lbl.textContent = `Đã chọn: ${cbs.length} ảnh`;
    lbl.style.color = cbs.length > 0 ? '#38bdf8' : '#ef4444';
  }
}

async function submitCollage() {
  const cbs = document.querySelectorAll('.collage-cb:checked');
  const paths = Array.from(cbs).map(cb => cb.dataset.path);
  
  if (paths.length === 0) {
    alert('Vui lòng chọn ít nhất 1 ảnh để ghép!');
    return;
  }

  const layout = document.getElementById('selectCollageLayout').value;
  const ratio = document.getElementById('selectCollageRatio').value;

  const btnSubmit = document.getElementById('btnSubmitCollageModal');
  btnSubmit.disabled = true;
  btnSubmit.textContent = '⏳ Đang ghép...';

  try {
    const res = await fetch('/api/tiktokshop/collage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_paths: paths,
        layout: layout,
        aspect_ratio: ratio
      })
    });
    const data = await res.json();
    if (data.success && data.collage_path) {
      document.getElementById('collageModal').classList.add('hidden');
      if (selectedTiktokProduct) {
        if (!selectedTiktokProduct.image_files) selectedTiktokProduct.image_files = [];
        selectedTiktokProduct.image_files.unshift(data.collage_path);
        renderTiktokProductGallery(selectedTiktokProduct.image_files, 0);
      }
    } else {
      alert(`Lỗi: ${data.error || 'Ghép ảnh thất bại'}`);
    }
  } catch (err) {
    alert(`Lỗi kết nối: ${err}`);
  } finally {
    btnSubmit.disabled = false;
    btnSubmit.textContent = '🧩 Ghép Ảnh';
  }
}

function openCropModal() {
  const modal = document.getElementById('cropModal');
  const img = document.getElementById('cropPreviewImg');
  if (!modal || !img) return;

  img.src = getImgUrl(selectedTiktokImagePath);
  setCropPreset(0, 0, 100, 100);
  modal.classList.remove('hidden');
}

function setCropPreset(x, y, w, h) {
  const rangeX = document.getElementById('cropRangeX');
  const rangeY = document.getElementById('cropRangeY');
  const rangeW = document.getElementById('cropRangeW');
  const rangeH = document.getElementById('cropRangeH');

  if (rangeX) rangeX.value = x;
  if (rangeY) rangeY.value = y;
  if (rangeW) rangeW.value = w;
  if (rangeH) rangeH.value = h;

  const valX = document.getElementById('cropValX');
  const valY = document.getElementById('cropValY');
  const valW = document.getElementById('cropValW');
  const valH = document.getElementById('cropValH');

  if (valX) valX.textContent = x;
  if (valY) valY.textContent = y;
  if (valW) valW.textContent = w;
  if (valH) valH.textContent = h;
}

async function submitCrop() {
  if (!selectedTiktokImagePath) return;

  const x = document.getElementById('cropRangeX').value;
  const y = document.getElementById('cropRangeY').value;
  const w = document.getElementById('cropRangeW').value;
  const h = document.getElementById('cropRangeH').value;

  const btnSubmit = document.getElementById('btnSubmitCropModal');
  btnSubmit.disabled = true;
  btnSubmit.textContent = '⏳ Đang cắt...';

  try {
    const res = await fetch('/api/tiktokshop/crop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_path: selectedTiktokImagePath,
        x: x, y: y, w: w, h: h
      })
    });
    const data = await res.json();
    if (data.success && data.cropped_path) {
      document.getElementById('cropModal').classList.add('hidden');
      if (selectedTiktokProduct) {
        if (!selectedTiktokProduct.image_files) selectedTiktokProduct.image_files = [];
        selectedTiktokProduct.image_files.unshift(data.cropped_path);
        renderTiktokProductGallery(selectedTiktokProduct.image_files, 0);
      }
    } else {
      alert(`Lỗi: ${data.error || 'Cắt ảnh thất bại'}`);
    }
  } catch (err) {
    alert(`Lỗi kết nối: ${err}`);
  } finally {
    btnSubmit.disabled = false;
    btnSubmit.textContent = '✂️ Cắt & Lưu';
  }
}
