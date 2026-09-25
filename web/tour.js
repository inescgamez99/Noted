// web/tour.js — onboarding tour con Driver.js
(function () {
'use strict';

// ── Demo content ──────────────────────────────────────────────────────────────

const _SVG_CLAUDE = `<svg width="16" height="16" viewBox="0 0 248 248" fill="currentColor"><path d="M52.4285 162.873L98.7844 136.879L99.5485 134.602L98.7844 133.334H96.4921L88.7237 132.862L62.2346 132.153L39.3113 131.207L17.0249 130.026L11.4214 128.844L6.2 121.873L6.7094 118.447L11.4214 115.257L18.171 115.847L33.0711 116.911L55.485 118.447L71.6586 119.392L95.728 121.873H99.5485L100.058 120.337L98.7844 119.392L97.7656 118.447L74.5877 102.732L49.4995 86.1905L36.3823 76.62L29.3779 71.7757L25.8121 67.2858L24.2839 57.3608L30.6515 50.2716L39.3113 50.8623L41.4763 51.4531L50.2636 58.1879L68.9842 72.7209L93.4357 90.6804L97.0015 93.6343L98.4374 92.6652L98.6571 91.9801L97.0015 89.2625L83.757 65.2772L69.621 40.8192L63.2534 30.6579L61.5978 24.632C60.9565 22.1032 60.579 20.0111 60.579 17.4246L67.8381 7.49965L71.9133 6.19995L81.7193 7.49965L85.7946 11.0443L91.9074 24.9865L101.714 46.8451L116.996 76.62L121.453 85.4816L123.873 93.6343L124.764 96.1155H126.292V94.6976L127.566 77.9197L129.858 57.3608L132.15 30.8942L132.915 23.4505L136.608 14.4708L143.994 9.62643L149.725 12.344L154.437 19.0788L153.8 23.4505L150.998 41.6463L145.522 70.1215L141.957 89.2625H143.994L146.414 86.7813L156.093 74.0206L172.266 53.698L179.398 45.6635L187.803 36.802L193.152 32.5484H203.34L210.726 43.6549L207.415 55.1159L196.972 68.3492L188.312 79.5739L175.896 96.2095L168.191 109.585L168.882 110.689L170.738 110.53L198.755 104.504L213.91 101.787L231.994 98.7149L240.144 102.496L241.036 106.395L237.852 114.311L218.495 119.037L195.826 123.645L162.07 131.592L161.696 131.893L162.137 132.547L177.36 133.925L183.855 134.279H199.774L229.447 136.524L237.215 141.605L241.8 147.867L241.036 152.711L229.065 158.737L213.019 154.956L175.45 145.977L162.587 142.787H160.805V143.85L171.502 154.366L191.242 172.089L215.82 195.011L217.094 200.682L213.91 205.172L210.599 204.699L188.949 188.394L180.544 181.069L161.696 165.118H160.422V166.772L164.752 173.152L187.803 207.771L188.949 218.405L187.294 221.832L181.308 223.959L174.813 222.777L161.187 203.754L147.305 182.486L136.098 163.345L134.745 164.2L128.075 235.42L125.019 239.082L117.887 241.8L111.902 237.31L108.718 229.984L111.902 215.452L115.722 196.547L118.779 181.541L121.58 162.873L123.291 156.636L123.14 156.219L121.773 156.449L107.699 175.752L86.304 204.699L69.3663 222.777L65.291 224.431L58.2867 220.768L58.9235 214.27L62.8713 208.48L86.304 178.705L100.44 160.155L109.551 149.507L109.462 147.967L108.959 147.924L46.6977 188.512L35.6182 189.93L30.7788 185.44L31.4156 178.115L33.7079 175.752L52.4285 162.873Z"/></svg>`;
const _SVG_EDIT = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
const _SVG_COPY = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
const _SVG_EMAIL = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>`;
const _SVG_STICKY = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M15 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h9l7-7V5a2 2 0 0 0-2-2z"/><path d="M14 21v-6a1 1 0 0 1 1-1h6"/></svg>`;
const _SVG_REGEN = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74"/><path d="M3 3v4h4"/></svg>`;
const _SVG_MORE = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>`;
const _SVG_PDF = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h4"/></svg>`;
const _SVG_HTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>`;

function injectDemoMeeting() {
  const list = document.getElementById('meetings-list');
  if (list) {
    list.innerHTML = `
      <div class="day-group">
        <div class="day-label">Hoy</div>
        <div class="meeting-item pinned active" id="tour-demo-item" style="cursor:default">
          <div class="meeting-time">10:00</div>
          <div class="meeting-info">
            <div class="meeting-title">Q4 Planning — Accenture Technology</div>
          </div>
        </div>
      </div>`;
  }

  const panel = document.getElementById('main-panel');
  if (!panel) return;
  panel.innerHTML = `
    <div class="meeting-detail">
      <div class="detail-header">
        <div>
          <div class="detail-title-row">
            <span class="detail-title" id="detail-title-text">Q4 Planning — Accenture Technology</span>
            <button class="btn-rename-meeting" id="btn-rename-meeting" title="Editar">${_SVG_EDIT}</button>
          </div>
          <div class="detail-meta">
            <span>23 Sep 2026 · 10:00</span>
            <span class="detail-project-wrap">
              <label class="detail-project-label">Proyecto</label>
              <select class="detail-project-select" id="meeting-project-select">
                <option>— Sin proyecto —</option>
                <option selected>Noted</option>
              </select>
            </span>
          </div>
        </div>
        <div class="detail-actions-bar" id="tour-detail-actions-bar">
          <button class="action-icon-btn action-icon-btn--accent" id="btn-claude" title="Claude">${_SVG_CLAUDE}</button>
          <button class="action-icon-btn" id="btn-edit-notes" title="Editar">${_SVG_EDIT}</button>
          <button class="action-icon-btn" id="btn-copy" title="Copiar">${_SVG_COPY}</button>
          <button class="action-icon-btn" id="btn-email" title="Email">${_SVG_EMAIL}</button>
          <button class="action-icon-btn" id="btn-sticky-bar" title="Nota adhesiva">${_SVG_STICKY}</button>
          <button class="action-icon-btn" id="btn-regenerate" title="Regenerar minutas">${_SVG_REGEN}</button>
          <div class="action-more-wrap">
            <button class="action-icon-btn" id="btn-more">${_SVG_MORE}</button>
            <div class="action-menu hidden" id="action-menu">
              <button class="action-menu-item" id="btn-sticky">${_SVG_STICKY}<span>Nota adhesiva</span></button>
              <button class="action-menu-item" id="btn-html">${_SVG_HTML}<span>HTML</span></button>
              <button class="action-menu-item" id="btn-pdf">${_SVG_PDF}<span>PDF</span></button>
            </div>
          </div>
        </div>
      </div>
      <div class="regen-bar hidden" id="regen-bar">
        <textarea id="regen-textarea" rows="2" placeholder="Describe qué cambios quieres en las minutas..."></textarea>
        <div class="regen-bar-btns">
          <button class="btn btn-primary btn-sm" id="btn-regen-confirm">Regenerar</button>
          <button class="btn btn-ghost btn-sm" id="btn-regen-cancel">Cancelar</button>
        </div>
      </div>
      <div class="detail-tabs">
        <button class="detail-tab active" id="tab-notes" data-tab="notes">Notas</button>
        <button class="detail-tab" id="tab-actions" data-tab="actions">Acciones <span class="tab-badge">4</span></button>
        <button class="detail-tab" id="tab-transcript" data-tab="transcript">Transcript</button>
      </div>
      <div class="minutes-section" id="section-notes">
        <div class="minutes-content">
          <h2>Resumen</h2>
          <p>Reunión de planificación del Q4 con el equipo de tecnología de Accenture. Se revisaron los objetivos del trimestre, se asignaron responsabilidades y se acordó el calendario de entregas para los próximos tres meses.</p>
          <h2>Decisiones</h2>
          <ul>
            <li>Usar Noted como herramienta estándar de documentación en el equipo</li>
            <li>Configurar reunión de seguimiento semanal los lunes a las 9:00</li>
            <li>Priorizar la integración con repositorios antes de fin de octubre</li>
          </ul>
          <h2>Acciones</h2>
          <ul>
            <li>📌 <strong>Ines</strong> — Distribuir Noted al resto del equipo antes del viernes</li>
            <li>📌 <strong>Felipe</strong> — Revisar y cerrar los PRs pendientes esta semana</li>
            <li>📌 <strong>Ana y Maria</strong> — Preparar demo de nuevas funcionalidades para el cliente</li>
            <li>📌 <strong>Todo el equipo</strong> — Completar la formación de onboarding antes del 30 de septiembre</li>
          </ul>
        </div>
      </div>
      <div class="actions-section hidden" id="section-actions">
        <div class="actions-section-header">
          <div class="section-label">Acciones</div>
          <div class="actions-count" style="margin-left:auto">4 en total</div>
          <button class="btn btn-ghost btn-sm" style="margin-left:8px">+ Añadir</button>
        </div>
        <div id="meeting-actions">
          <div class="action-card" id="card-0">
            <div class="action-card-row">
              <div class="action-card-main">
                <span class="action-title">Distribuir Noted al resto del equipo</span>
                <div class="action-meta"><span class="action-assignee">Ines</span> · <span style="font-size:11px;color:var(--muted)">viernes</span></div>
              </div>
              <div class="action-card-btns">
                <button class="btn btn-ghost btn-sm" id="tour-move-panel-btn">Mover al panel</button>
                <button class="btn btn-delete btn-sm">×</button>
              </div>
            </div>
          </div>
          <div class="action-card" id="card-1">
            <div class="action-card-row">
              <div class="action-card-main">
                <span class="action-title">Revisar y cerrar los PRs pendientes</span>
                <div class="action-meta"><span class="action-assignee">Felipe</span></div>
              </div>
              <div class="action-card-btns">
                <button class="btn btn-ghost btn-sm">Mover al panel</button>
                <button class="btn btn-delete btn-sm">×</button>
              </div>
            </div>
          </div>
          <div class="action-card" id="card-2">
            <div class="action-card-row">
              <div class="action-card-main">
                <span class="action-title">Preparar demo para el cliente</span>
                <div class="action-meta"><span class="action-assignee">Ana</span> · <span class="action-assignee">Maria</span></div>
              </div>
              <div class="action-card-btns">
                <button class="btn btn-ghost btn-sm">Mover al panel</button>
                <button class="btn btn-delete btn-sm">×</button>
              </div>
            </div>
          </div>
          <div class="action-card" id="card-3">
            <div class="action-card-row">
              <div class="action-card-main">
                <span class="action-title">Completar la formación de onboarding</span>
                <div class="action-meta"><span class="action-assignee">Todo el equipo</span> · <span style="font-size:11px;color:var(--muted)">30 sep</span></div>
              </div>
              <div class="action-card-btns">
                <button class="btn btn-ghost btn-sm">Mover al panel</button>
                <button class="btn btn-delete btn-sm">×</button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="transcript-section hidden" id="section-transcript">
        <div class="transcript-content" style="color:var(--muted);font-style:italic;padding:24px;text-align:center">Transcript de la reunión de demo no disponible.</div>
      </div>
      <div id="sticky-layer" class="sticky-layer"></div>
    </div>`;

  // Wire demo tabs
  panel.querySelectorAll('.detail-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      panel.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      panel.querySelectorAll('.minutes-section').forEach(s => s.classList.add('hidden'));
      const sec = panel.querySelector('#section-' + tab.dataset.tab);
      if (sec) sec.classList.remove('hidden');
    });
  });
}

function injectDemoTasks() {
  const body = document.getElementById('task-board-body');
  if (!body) return;
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:8px;padding:12px">
      <div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding:4px 0">En curso</div>
      <div class="task-item" id="tour-task-1" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer">
        <span style="font-size:13px;font-weight:500">Distribuir Noted al equipo</span>
        <div style="display:flex;gap:8px;align-items:center">
          <span style="font-size:11px;background:var(--accent-soft,#eff6ff);color:var(--accent,#3b82f6);border-radius:4px;padding:2px 7px">Ines</span>
          <span style="font-size:11px;color:var(--muted)">Viernes</span>
        </div>
      </div>
      <div class="task-item" id="tour-task-2" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer">
        <span style="font-size:13px;font-weight:500">Revisar y cerrar PRs pendientes</span>
        <span style="font-size:11px;background:var(--accent-soft,#eff6ff);color:var(--accent,#3b82f6);border-radius:4px;padding:2px 7px">Felipe</span>
      </div>
      <div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding:12px 0 4px">Pendiente</div>
      <div class="task-item" id="tour-task-3" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer">
        <span style="font-size:13px;font-weight:500">Preparar demo para cliente</span>
        <div style="display:flex;gap:8px;align-items:center">
          <span style="font-size:11px;background:var(--accent-soft,#eff6ff);color:var(--accent,#3b82f6);border-radius:4px;padding:2px 7px">Ana</span>
          <span style="font-size:11px;color:var(--muted)">30 Sep</span>
        </div>
      </div>
    </div>`;
}

// ── Tour steps ────────────────────────────────────────────────────────────────

function buildSteps() {
  const T = window.__t || (k => k);
  return [
    // 1 — Bienvenida
    {
      popover: { title: T('tour_s1_title'), description: T('tour_s1_desc') },
    },
    // 2 — Bandeja del sistema
    {
      popover: { title: T('tour_s2_title'), description: T('tour_s2_desc') },
    },
    // 3 — Nav Notas
    {
      element: '#btn-meetings',
      popover: { title: T('tour_s3_title'), description: T('tour_s3_desc'), side: 'right' },
      onHighlightStarted: () => { if (typeof showView === 'function') showView('meetings'); },
    },
    // 4 — Buscador
    {
      element: '#search-input',
      popover: { title: T('tour_s4_title'), description: T('tour_s4_desc'), side: 'bottom' },
    },
    // 5 — Toggle Días/Proyectos
    {
      element: '.sidebar-mode-toggle',
      popover: { title: T('tour_s5_title'), description: T('tour_s5_desc'), side: 'bottom' },
    },
    // 6 — Reunión en sidebar
    {
      element: '#tour-demo-item',
      popover: { title: T('tour_s6_title'), description: T('tour_s6_desc'), side: 'right' },
    },
    // 7 — Import (+)
    {
      element: '.btn-import-transcript',
      popover: { title: T('tour_s7_title'), description: T('tour_s7_desc'), side: 'bottom' },
    },
    // 8 — Título editable
    {
      element: '#detail-title-text',
      popover: { title: T('tour_s8_title'), description: T('tour_s8_desc'), side: 'bottom' },
    },
    // 9 — Tab proyecto
    {
      element: '#meeting-project-select',
      popover: { title: T('tour_s9_title'), description: T('tour_s9_desc'), side: 'bottom' },
    },
    // 10 — Cuerpo de minutas
    {
      element: '#section-notes',
      popover: { title: T('tour_s10_title'), description: T('tour_s10_desc'), side: 'top' },
    },
    // 11 — Regenerar
    {
      element: '#btn-regenerate',
      popover: { title: T('tour_s11_title'), description: T('tour_s11_desc'), side: 'left' },
    },
    // 12 — Post-it
    {
      element: '#btn-sticky-bar',
      popover: { title: T('tour_s12_title'), description: T('tour_s12_desc'), side: 'left' },
    },
    // 13 — Hablar con Claude
    {
      element: '#btn-claude',
      popover: { title: T('tour_s13_title'), description: T('tour_s13_desc'), side: 'left' },
    },
    // 14 — Exportar
    {
      element: '#btn-email',
      popover: { title: T('tour_s14_title'), description: T('tour_s14_desc'), side: 'left' },
    },
    // 15 — Mover al panel
    {
      element: '#tour-move-panel-btn',
      popover: { title: T('tour_s15_title'), description: T('tour_s15_desc'), side: 'top' },
      onHighlightStarted: () => { document.getElementById('tab-actions')?.click(); },
    },
    // 16 — Nav Acciones
    {
      element: '#btn-actions',
      popover: { title: T('tour_s16_title'), description: T('tour_s16_desc'), side: 'right' },
      onHighlightStarted: () => {
        if (typeof showView === 'function') showView('actions');
        injectDemoTasks();
      },
    },
    // 17 — Kanban/Rows
    {
      element: '.task-view-toggle',
      popover: { title: T('tour_s17_title'), description: T('tour_s17_desc'), side: 'bottom' },
    },
    // 18 — Filtros
    {
      element: '#task-filter-bar',
      popover: { title: T('tour_s18_title'), description: T('tour_s18_desc'), side: 'bottom' },
    },
    // 19 — Tarea individual
    {
      element: '#tour-task-1',
      popover: { title: T('tour_s19_title'), description: T('tour_s19_desc'), side: 'bottom' },
    },
    // 20 — Nav Proyectos
    {
      element: '#btn-projects',
      popover: { title: T('tour_s20_title'), description: T('tour_s20_desc'), side: 'right' },
      onHighlightStarted: () => { if (typeof showView === 'function') showView('projects'); },
    },
    // 21 — Nav Trash
    {
      element: '#btn-trash',
      popover: { title: T('tour_s21_title'), description: T('tour_s21_desc'), side: 'right' },
      onHighlightStarted: () => { if (typeof showView === 'function') showView('trash'); },
    },
    // 22 — Nombre (settings)
    {
      element: '#user-name-input',
      popover: { title: T('tour_s22_title'), description: T('tour_s22_desc'), side: 'bottom' },
      onHighlightStarted: () => { if (typeof showView === 'function') showView('settings'); },
    },
    // 23 — Idioma y tema
    {
      element: '#lang-toggle',
      popover: { title: T('tour_s23_title'), description: T('tour_s23_desc'), side: 'bottom' },
    },
    // 24 — Whisper
    {
      element: '#whisper-model-select',
      popover: { title: T('tour_s24_title'), description: T('tour_s24_desc'), side: 'top' },
    },
    // 25 — Career Level
    {
      element: '#toggle-coaching',
      popover: { title: T('tour_s25_title'), description: T('tour_s25_desc'), side: 'left' },
    },
    // 26 — Chat notice
    {
      element: '#toggle-chat-notice',
      popover: { title: T('tour_s26_title'), description: T('tour_s26_desc'), side: 'left' },
    },
    // 27 — Final
    {
      popover: { title: T('tour_s27_title'), description: T('tour_s27_desc') },
    },
  ];
}

// ── API pública ───────────────────────────────────────────────────────────────

window.startTour = function () {
  // Expose t() to buildSteps via global bridge so tour.js can use it without importing app.js internals
  window.__t = typeof t === 'function' ? t : (k => k);

  injectDemoMeeting();
  if (typeof showView === 'function') showView('meetings');

  const driverObj = window.driver.js.driver({
    showProgress: true,
    allowClose: true,
    nextBtnText: window.__t('tour_btn_next'),
    prevBtnText: window.__t('tour_btn_prev'),
    doneBtnText: window.__t('tour_btn_done'),
    steps: buildSteps(),
    onPopoverRender: (popover) => {
      // ── Emoji icon bubble ──
      try {
        const titleEl = popover.title;
        if (titleEl) {
          const raw = titleEl.textContent || '';
          const cp = raw.codePointAt(0) || 0;
          if (cp > 0x1F000) {
            const emoji = String.fromCodePoint(cp);
            const icon = document.createElement('span');
            icon.className = 'driver-title-icon';
            icon.textContent = emoji;
            // strip emoji + optional variation selector + space
            titleEl.textContent = raw.slice(emoji.length).replace(/^[️\s]+/, '');
            if (titleEl.parentElement && !titleEl.parentElement.querySelector('.driver-title-icon')) {
              titleEl.parentElement.insertBefore(icon, titleEl);
            }
          }
        }
      } catch (_) {}

      // ── Footer: progress | skip  ···spacer···  prev  next → ──
      try {
        const footer = popover.footer;
        footer.querySelector('.driver-skip-btn')?.remove();
        footer.querySelector('.driver-footer-sep')?.remove();
        footer.querySelector('.driver-footer-spacer')?.remove();

        const sep = document.createElement('span');
        sep.className = 'driver-footer-sep';
        sep.textContent = '|';

        const skipBtn = document.createElement('button');
        skipBtn.textContent = window.__t('tour_btn_skip');
        skipBtn.className = 'driver-skip-btn';
        skipBtn.addEventListener('click', () => driverObj.destroy());

        const spacer = document.createElement('span');
        spacer.className = 'driver-footer-spacer';

        // Use popover.footerButtons as anchor if available, else fall back
        const anchor = popover.footerButtons || footer.querySelector('.driver-popover-prev-btn');
        if (anchor) {
          footer.insertBefore(spacer, anchor);
          footer.insertBefore(skipBtn, spacer);
          footer.insertBefore(sep, skipBtn);
        }

        const nextBtn = footer.querySelector('.driver-popover-next-btn');
        if (nextBtn && !nextBtn.classList.contains('driver-popover-done-btn') && !nextBtn.textContent.includes('→')) {
          nextBtn.textContent = nextBtn.textContent.trim() + ' →';
        }
      } catch (_) {}
    },
    onDestroyStarted: () => {
      driverObj.destroy();
    },
    onDestroyed: async () => {
      try { await pywebview.api.save_settings({ onboarding_completed: true }); } catch (_) {}
      try { await loadMeetings(); } catch (_) {}
    },
  });

  driverObj.drive();
};

window.resetTour = async function () {
  try { await pywebview.api.save_settings({ onboarding_completed: false }); } catch (_) {}
  window.startTour();
};

})();
