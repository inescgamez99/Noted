// web/tour.js — onboarding tour con Driver.js
(function () {
'use strict';

// ── Demo content ──────────────────────────────────────────────────────────────

function injectDemoMeeting() {
  const list = document.getElementById('meetings-list');
  if (list) {
    list.innerHTML = `
      <div class="day-group">
        <div class="day-label">Hoy</div>
        <div class="meeting-item pinned active" id="tour-demo-item" style="cursor:default">
          <div class="meeting-title">Q4 Planning — Accenture Technology</div>
          <div class="meeting-date">Hoy · 45 min</div>
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
            <button class="btn-rename-meeting" id="btn-rename-meeting" title="Editar">✎</button>
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
          <button class="action-icon-btn action-icon-btn--accent" id="btn-claude" title="Claude">✦</button>
          <button class="action-icon-btn" id="btn-edit-notes" title="Editar">✎</button>
          <button class="action-icon-btn" id="btn-copy" title="Copiar">⎘</button>
          <button class="action-icon-btn" id="btn-email" title="Email">✉</button>
          <button class="action-icon-btn" id="btn-export-transcript" title="Exportar transcript" style="display:none">⬇</button>
          <button class="action-icon-btn" id="btn-sticky-bar" title="Nota adhesiva">📌</button>
          <button class="action-icon-btn" id="btn-regenerate" title="Regenerar minutas">↺</button>
          <div class="action-more-wrap">
            <button class="action-icon-btn" id="btn-more">···</button>
            <div class="action-menu hidden" id="action-menu">
              <button class="action-menu-item" id="btn-sticky">📌 Nota adhesiva</button>
              <button class="action-menu-item" id="btn-html">HTML</button>
              <button class="action-menu-item" id="btn-pdf">PDF</button>
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
        <div class="minutes-html" id="minutes-html-content">
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
            <li>📌 <strong>Ana</strong> — Preparar demo de nuevas funcionalidades para el cliente</li>
            <li>📌 <strong>Todo el equipo</strong> — Completar la formación de onboarding antes del 30 de septiembre</li>
          </ul>
        </div>
        <div class="sticky-layer" id="sticky-layer"></div>
      </div>
      <div class="minutes-section hidden" id="section-actions">
        <div class="actions-list">
          <div class="action-card">
            <div class="action-card-row">
              <div class="action-card-main">
                <span class="action-title">Distribuir Noted al resto del equipo</span>
                <div class="action-meta"><span class="action-assignee-badge">Ines</span><span style="font-size:11px;color:var(--muted);margin-left:6px">viernes</span></div>
              </div>
              <div class="action-card-btns">
                <button class="btn btn-ghost btn-sm" id="tour-move-panel-btn">Mover al panel</button>
              </div>
            </div>
          </div>
          <div class="action-card">
            <div class="action-card-row">
              <div class="action-card-main">
                <span class="action-title">Revisar y cerrar los PRs pendientes</span>
                <div class="action-meta"><span class="action-assignee-badge">Felipe</span></div>
              </div>
              <div class="action-card-btns">
                <button class="btn btn-ghost btn-sm">Mover al panel</button>
              </div>
            </div>
          </div>
          <div class="action-card">
            <div class="action-card-row">
              <div class="action-card-main">
                <span class="action-title">Preparar demo para el cliente</span>
                <div class="action-meta"><span class="action-assignee-badge">Ana</span></div>
              </div>
              <div class="action-card-btns">
                <button class="btn btn-ghost btn-sm">Mover al panel</button>
              </div>
            </div>
          </div>
          <div class="action-card">
            <div class="action-card-row">
              <div class="action-card-main">
                <span class="action-title">Completar la formación de onboarding</span>
                <div class="action-meta"><span class="action-assignee-badge">Todo el equipo</span><span style="font-size:11px;color:var(--muted);margin-left:6px">30 sep</span></div>
              </div>
              <div class="action-card-btns">
                <button class="btn btn-ghost btn-sm">Mover al panel</button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="minutes-section hidden" id="section-transcript">
        <div style="padding:24px;color:var(--subtext);font-style:italic;text-align:center">Transcript de la reunión de demo no disponible.</div>
      </div>
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
  return [
    // 1 — Bienvenida
    {
      popover: {
        title: '👋 Bienvenida a Noted',
        description: 'En los próximos pasos te enseñamos todo lo que puedes hacer. Hemos cargado una reunión de demo para que puedas verlo con datos reales.',
      },
    },
    // 2 — Bandeja del sistema (centrado, no se puede apuntar a icono de Windows)
    {
      popover: {
        title: 'Noted vive en tu bandeja',
        description: 'El icono aparece en la bandeja del sistema (esquina inferior derecha, puede estar oculto bajo la flecha ˄). Desde ahí puedes lanzar una grabación manual, darle contexto antes de grabar, ver el estado actual o cancelar una grabación en curso.',
      },
    },
    // 3 — Nav Notas
    {
      element: '#btn-meetings',
      popover: {
        title: 'Tus notas, siempre aquí',
        description: 'La sección principal donde viven todas tus minutas. Puedes acceder a ellas también haciendo clic en el icono de la bandeja.',
        side: 'right',
      },
      onHighlightStarted: () => { if (typeof showView === 'function') showView('meetings'); },
    },
    // 4 — Buscador
    {
      element: '#search-input',
      popover: {
        title: 'Buscar en todas tus minutas',
        description: 'Busca en el contenido de todas tus minutas a la vez. Atajo rápido: Ctrl+F desde cualquier vista.',
        side: 'bottom',
      },
    },
    // 5 — Toggle Días/Proyectos
    {
      element: '.sidebar-mode-toggle',
      popover: {
        title: 'Días o Proyectos',
        description: 'Agrupa tus reuniones por fecha o por proyecto según lo que necesites en cada momento.',
        side: 'bottom',
      },
    },
    // 6 — Reunión en sidebar
    {
      element: '#tour-demo-item',
      popover: {
        title: 'Tus reuniones',
        description: 'Cada reunión grabada aparece aquí. Haz clic para abrirla. Clic derecho para fijarla arriba o eliminarla.',
        side: 'right',
      },
    },
    // 7 — Import (+)
    {
      element: '.btn-import-transcript',
      popover: {
        title: 'Importar transcript',
        description: '¿Tienes un transcript de un cliente o un compañero? Impórtalo directamente y Noted generará las minutas igual que si lo hubiera grabado él.',
        side: 'bottom',
      },
    },
    // 8 — Título editable
    {
      element: '#detail-title-text',
      popover: {
        title: 'Título editable',
        description: 'El título se genera automáticamente a partir del contenido. Haz clic sobre él para editarlo cuando quieras.',
        side: 'bottom',
      },
    },
    // 9 — Tab proyecto
    {
      element: '#meeting-project-select',
      popover: {
        title: 'Proyecto detectado',
        description: 'Noted intenta detectar automáticamente a qué proyecto pertenece cada reunión. Si se ha equivocado, cámbialo aquí.',
        side: 'bottom',
      },
    },
    // 10 — Cuerpo de minutas
    {
      element: '#section-notes',
      popover: {
        title: 'Minutas generadas por Claude',
        description: 'El cuerpo incluye un resumen de lo hablado, las decisiones tomadas y las tareas detectadas y asignadas automáticamente.',
        side: 'top',
      },
    },
    // 11 — Regenerar (barra de acciones, top-right → side left)
    {
      element: '#btn-regenerate',
      popover: {
        title: 'Regenerar minutas',
        description: 'Si el resultado no te convence, puedes darle contexto adicional — qué corregir, qué tono usar, qué añadir — y Claude regenera las minutas ajustadas a lo que necesitas.',
        side: 'left',
      },
    },
    // 12 — Post-it (barra de acciones, top-right → side left)
    {
      element: '#btn-sticky-bar',
      popover: {
        title: 'Nota adhesiva',
        description: 'Crea un post-it flotante sobre las minutas. Puedes arrastrarlo, minimizarlo o eliminarlo — es tu espacio para anotar lo que quieras sin tocar el contenido generado.',
        side: 'left',
      },
    },
    // 13 — Hablar con Claude (barra de acciones, top-right → side left)
    {
      element: '#btn-claude',
      popover: {
        title: 'Hablar con Claude',
        description: 'Abre las minutas directamente en Claude para hacer preguntas, profundizar en un tema o redactar un follow-up email personalizado.',
        side: 'left',
      },
    },
    // 14 — Exportar (barra de acciones, top-right → side left)
    {
      element: '#btn-email',
      popover: {
        title: 'Compartir y exportar',
        description: 'Envía las minutas por email a los asistentes, copia el contenido al portapapeles, expórtalas como PDF o HTML. Todo en una barra de acciones rápidas.',
        side: 'left',
      },
    },
    // 15 — Mover al panel (tab acciones)
    {
      element: '#tour-move-panel-btn',
      popover: {
        title: 'Mover al panel de Acciones',
        description: 'Cada acción detectada en las minutas tiene este botón. Úsalo para añadirla al panel de Acciones y hacerle seguimiento independiente vinculado a un proyecto.',
        side: 'top',
      },
      onHighlightStarted: () => {
        document.getElementById('tab-actions')?.click();
      },
    },
    // 16 — Nav Acciones
    {
      element: '#btn-actions',
      popover: {
        title: 'Panel de Acciones',
        description: 'Aquí tienes todas las tareas pendientes de todas tus reuniones en un solo sitio, sin tener que abrir cada minuta.',
        side: 'right',
      },
      onHighlightStarted: () => {
        if (typeof showView === 'function') showView('actions');
        injectDemoTasks();
      },
    },
    // 17 — Kanban/Rows
    {
      element: '.task-view-toggle',
      popover: {
        title: 'Kanban o lista',
        description: 'Visualiza las acciones como tablero Kanban o como lista según tu preferencia.',
        side: 'bottom',
      },
    },
    // 18 — Filtros
    {
      element: '#task-filter-bar',
      popover: {
        title: 'Filtros',
        description: 'Filtra las acciones por proyecto, persona asignada o estado para centrarte en lo que importa.',
        side: 'bottom',
      },
    },
    // 19 — Tarea individual
    {
      element: '#tour-task-1',
      popover: {
        title: 'Detalle de tarea',
        description: 'Haz clic en cualquier tarea para ver el detalle completo: de qué reunión salió, quién la tiene asignada y cuál es su estado actual.',
        side: 'bottom',
      },
    },
    // 20 — Nav Proyectos
    {
      element: '#btn-projects',
      popover: {
        title: 'Proyectos',
        description: 'Define tus proyectos para clasificar tus notas y acciones. Por cada proyecto puedes configurar nombre, descripción, stakeholders para email, carpeta de trabajo y color.',
        side: 'right',
      },
      onHighlightStarted: () => { if (typeof showView === 'function') showView('projects'); },
    },
    // 21 — Nav Trash
    {
      element: '#btn-trash',
      popover: {
        title: 'Eliminados recientemente',
        description: 'Las reuniones eliminadas se guardan aquí durante 30 días. Puedes recuperarlas antes de que desaparezcan definitivamente.',
        side: 'right',
      },
      onHighlightStarted: () => { if (typeof showView === 'function') showView('trash'); },
    },
    // 22 — Nombre (settings)
    {
      element: '#user-name-input',
      popover: {
        title: 'Tu nombre',
        description: 'Ponle tu nombre para que Noted sepa qué acciones son tuyas cuando filtras por asignado.',
        side: 'bottom',
      },
      onHighlightStarted: () => { if (typeof showView === 'function') showView('settings'); },
    },
    // 23 — Idioma y tema
    {
      element: '#lang-toggle',
      popover: {
        title: 'Idioma y tema',
        description: 'Elige el idioma de la interfaz y el tema claro u oscuro. Se aplica al instante.',
        side: 'bottom',
      },
    },
    // 24 — Whisper
    {
      element: '#whisper-model-select',
      popover: {
        title: 'Modelo de transcripción',
        description: 'Controla la precisión de la transcripción. Medium es el equilibrio recomendado entre velocidad y calidad.',
        side: 'top',
      },
    },
    // 25 — Career Level (apunta al label visible, no al input oculto)
    {
      element: '#toggle-coaching',
      popover: {
        title: 'Career Level Snapshot',
        description: 'Después de cada llamada, la IA analiza tu intervención y te da un snapshot del nivel de consulting al que estás operando.',
        side: 'left',
      },
    },
    // 26 — Chat notice (apunta al label visible, no al input oculto)
    {
      element: '#toggle-chat-notice',
      popover: {
        title: 'Aviso al chat de Teams',
        description: 'Configura el mensaje que Noted envía automáticamente al chat de Teams cuando empieza a grabar, para avisar a los participantes.',
        side: 'left',
      },
    },
    // 27 — Final
    {
      popover: {
        title: '🎉 ¡Ya lo sabes todo!',
        description: 'Listo. Entra en tu próxima reunión de Teams y Noted hará el resto. El popup aparece en cuanto detecte la llamada — tienes 30 segundos para aceptar.',
      },
    },
  ];
}

// ── API pública ───────────────────────────────────────────────────────────────

window.startTour = function () {
  injectDemoMeeting();
  if (typeof showView === 'function') showView('meetings');

  const driverObj = window.driver.js.driver({
    showProgress: true,
    allowClose: false,
    nextBtnText: 'Siguiente →',
    prevBtnText: '← Anterior',
    doneBtnText: '¡Empezar!',
    steps: buildSteps(),
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
