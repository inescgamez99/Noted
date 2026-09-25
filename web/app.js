// ── Sanitización de HTML de reuniones ────────────────────────────────────────
// Elimina scripts y atributos de evento antes de inyectar contenido de minutas.
function sanitizeHtml(html) {
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  tmp.querySelectorAll('script, object, embed, link[rel="import"]').forEach(el => el.remove());
  tmp.querySelectorAll('*').forEach(el => {
    [...el.attributes].forEach(attr => {
      if (/^on/i.test(attr.name) ||
          (attr.name === 'href' && /^javascript:/i.test(attr.value)) ||
          (attr.name === 'src'  && /^javascript:/i.test(attr.value))) {
        el.removeAttribute(attr.name);
      }
    });
  });
  return tmp.innerHTML;
}

// ── Estado global ────────────────────────────────────────────────────────────
let currentPath          = null;
let _regenPath           = null;   // non-null while confirmRegen is running
let allMeetings          = [];
let allPending           = [];
let allProjects          = [];
let _taskData = { projects: [], tasks: [], buckets: [] };
let _boardView = false;
let _buckets = [];
let _dragTaskId = null;
let _dragBucketId = null;
let _filterProjectId = null;  // null = todos
let _colFilters = {};         // { status: Set, assignee: Set, priority: Set } — missing key = no filter
let _sortBy = null;           // null | 'end_date' | 'priority' | 'title' | 'tag'
let _groupBy = 'status';     // 'status' | 'priority' | 'assignee' | 'due_date'
let _customStatuses     = [];
let _customPriorities   = [];
let _statusColumnOrder  = [];
let currentSidebarMode   = 'days';
let searchTimeout        = null;

// Panel de Claude
let _regenVisible        = false;

// Sticky notes
let _stickies = [];
const _PIN_SVG = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1z"/></svg>`;
let _stickySaveTimer = null;

// ── Internacionalización ──────────────────────────────────────────────────────

let currentLang = localStorage.getItem('lang') || 'es';

const T = {
  es: {
    nav_notes: 'Notas', nav_action_panel: 'Panel Acciones', nav_projects: 'Proyectos', nav_trash: 'Eliminados recientemente', settings_nav: 'Ajustes',
    trash_desc: 'Las reuniones eliminadas se guardan aquí. Puedes recuperarlas o borrarlas definitivamente.',
    trash_empty: 'La papelera está vacía.',
    trash_recover: 'Recuperar', trash_delete_forever: 'Borrar definitivamente',
    trash_recovered: 'Reunión recuperada', trash_purged: 'Eliminada definitivamente',
    trash_deleted_at: 'eliminada', trash_files: 'archivos',
    trash_purge_confirm: title => `¿Borrar definitivamente "${title}"? No se podrá recuperar.`,
    lang_label: 'Idioma de la app', theme_label: 'Tema',
    n_pending: n => `${n} pendiente${n > 1 ? 's' : ''}`,
    n_done:    n => `${n} hecha${n > 1 ? 's' : ''}`,
    light: 'Claro', dark: 'Oscuro',
    manage_actions: 'Acciones pendientes',
    filter_pending: 'Pendientes', filter_done: 'Completadas',
    name_label: 'Tu nombre', name_ph: 'Nombre para filtrar acciones',
    search_ph: 'Buscar en minutas...',
    empty_title: 'Selecciona una reunión',
    empty_sub: 'Elige una reunión de la lista para ver sus minutas y acciones',
    loading: 'Cargando...', loading_meetings: 'Cargando reuniones...',
    loading_actions: 'Cargando acciones...', no_meetings: 'No hay reuniones todavía',
    tab_notes: 'Notas', tab_transcript: 'Transcript', tab_actions: 'Gestionar acciones', section_actions: 'Acciones',
    copy_note: 'Copiar notas', copy_actions: 'Copiar acciones',
    copy_transcript: 'Copiar transcript', copy_transcript_done: 'Copiado', copy_failed: 'Error al copiar',
    email_actions: 'Enviar acciones por email', email_transcript: 'Enviar transcript por email',
    more_actions: 'Más opciones',
    sticky_add: 'Añadir nota adhesiva', sticky_min: 'Minimizar', sticky_del: 'Eliminar', sticky_ph: 'Nota...',
    pin: 'Fijar reunión', unpin: 'Desfijar reunión', pinned: 'Fijadas',
    add_action: 'Añadir acción', toast_action_added: 'Acción añadida',
    regen_actions: 'Regenerar acciones',
    regen_actions_hint: 'Describe qué cambios quieres en las acciones (agrupar, añadir, quitar, reescribir...). Claude las regenerará.',
    regen_actions_ph: 'Ej: agrupa los cambios del HTML en una sola acción y añade una para revisar el email',
    regen_actions_confirm: 'Regenerar', regen_actions_working: 'Regenerando...',
    regen_actions_done: 'Acciones regeneradas', regen_actions_error: 'No se pudieron regenerar las acciones',
    no_owner: 'Sin responsable',
    action_title_ph: 'Descripción de la acción...', action_assignee_ph: 'Responsable (opcional)', action_deadline_ph: 'Fecha límite (opcional)',
    n_total: n => `${n} en total`,
    no_project_label: 'Sin proyecto',
    no_actions: 'No hay acciones en esta reunión',
    btn_chat: 'Chat con Claude', prompt_label: 'Prompt para Claude',
    btn_export_project: 'Exportar a proyecto', btn_export_project_title: 'Guarda transcript, notas HTML y email en la carpeta del proyecto',
    toast_export_ok: 'Exportado a la carpeta del proyecto',
    toast_export_no_project: 'Esta reunión no tiene proyecto asignado',
    toast_export_no_dir: 'El proyecto no tiene carpeta configurada (ve a Ajustes)',
    toast_export_error: 'Error al exportar al proyecto',
    btn_reenrich: 'Re-analizar', btn_reenrich_all: 'Re-analizar todas',
    toast_enriching: 'Analizando acciones...', toast_enriching_all: n => `Analizando ${n} notas...`,
    btn_execute: 'Enviar a Claude', btn_reexecute: 'Reenviar a Claude',
    btn_detail: 'Ver detalle Claude',
    btn_prompt_show: '▼ Ver prompt y editar', btn_prompt_hide: '▲ Ocultar',
    doc_file_label: 'Archivo del documento', doc_file_ph: 'Selecciona el archivo a modificar', doc_browse_file: 'Seleccionar archivo',
    confirm_dir: 'Directorio de trabajo', confirm_browse: 'Explorar',
    btn_launch: 'Lanzar', btn_launch_cancel: 'Cancelar',
    run_running: 'Corriendo', run_done: 'Hecho', run_error: 'Error', run_needs_input: 'Necesita input',
    btn_continue_terminal: 'Continuar en terminal',
    runs_panel_title: 'Ejecuciones',
    done_from_terminal: 'completado desde terminal',
    runs_fab_running: 'corriendo', runs_fab_done: 'runs',
    btn_regenerate: 'Regenerar minutas',
    regen_placeholder: 'Objetivo o contexto adicional para regenerar las minutas... ej: "El objetivo es entender mejor los conceptos de arquitectura agéntica"',
    regen_confirm: 'Regenerar',
    regen_cancel: 'Cancelar',
    toast_regenerating: 'Regenerando minutas con Claude...',
    toast_regen_done: 'Minutas regeneradas.',
    toast_regen_error: 'No se encontró la transcripción original para regenerar.',
    regen_stage_transcript_ok: 'Transcripción encontrada',
    regen_stage_generating: 'Generando minutas con Claude...',
    regen_stage_saving: 'Guardando minutas...',
    regen_stage_html: 'Exportando HTML...',
    regen_stage_actions: 'Analizando acciones...',
    regen_stage_done: '¡Completado!',
    regen_stage_error: 'Error al regenerar',
    panel_running: 'Ejecutando...', panel_done: 'Completado', panel_error: 'Error',
    panel_followup_ph: 'Añade contexto o haz una pregunta...',
    btn_done: 'Marcar hecha', btn_done_state: 'Hecha',
    btn_move_panel: 'Mover al panel', btn_in_panel: 'En panel',
    btn_go_notes: 'Ir a notas', btn_mark_complete: 'Marcar hecha',
    btn_delete: 'Eliminar', toast_deleted: 'Acción eliminada',
    confirm_delete_action_title: 'Eliminar acción',
    confirm_delete_action: title => `¿Eliminar la acción "${title}"? Esta acción no se puede deshacer.`,
    confirm_delete_title: 'Eliminar proyecto',
    confirm_delete_project: name => name
      ? `¿Seguro que quieres eliminar el proyecto "${name}"? Esta acción no se puede deshacer.`
      : '¿Seguro que quieres eliminar este proyecto? Esta acción no se puede deshacer.',
    ctx_delete_meeting: 'Eliminar',
    confirm_delete_meeting_title: 'Eliminar reunión',
    confirm_delete_meeting: title => `¿Enviar la reunión "${title}" a la papelera? Podrás recuperarla desde la Papelera.`,
    confirm_delete_task_title: 'Eliminar tarea',
    confirm_delete_task: title => `¿Eliminar la tarea "${title}"? Esta acción no se puede deshacer.`,
    confirm_delete_column_title: 'Eliminar columna',
    confirm_delete_column: (name, count, target) => count
      ? `¿Eliminar la columna "${name}"? Sus ${count} tarea(s) pasarán a "${target}".`
      : `¿Eliminar la columna "${name}"?`,
    toast_meeting_deleted: 'Reunión eliminada',
    no_match_actions: 'No hay acciones en el panel. Mueve acciones desde "Gestionar acciones".',
    toast_claude: 'Abriendo Claude...', toast_terminal: 'Abriendo terminal...',
    toast_outlook: 'Abriendo Outlook...', toast_done: 'Acción marcada como hecha',
    toast_moved: 'Acción movida al panel',
    pending_actions_title: 'Acciones pendientes',
    type_instruction: 'Instrucción', type_code: 'Código', type_doc: 'Documento',
    who_claude: 'Claude', who_human: 'Manual',
    no_prompt: 'Sin prompt ejecutable',
    owner_label: 'Owner', created_label: 'Creada', deadline_label: 'Deadline',
    sort_label: 'Ordenar', sort_newest: 'Más recientes', sort_oldest: 'Más antiguas',
    sort_deadline_asc: 'Deadline ↑', sort_deadline_desc: 'Deadline ↓',
    es_label: 'Español', en_label: 'English', ca_label: 'Català',
    refresh_meetings: 'Actualizar reuniones',
    name_desc: 'Se usa para identificar tus acciones asignadas',
    projects_desc: 'Noted detecta automáticamente a qué proyecto pertenece cada reunión y permite filtrar acciones por proyecto',
    add_project: '+ Añadir proyecto',
    proj_name_ph: 'Nombre del proyecto', proj_desc_ph: 'Descripción corta', proj_stake_ph: 'emails separados por coma',
    save_btn: 'Guardar',
    recording_title: 'Grabación y Transcripción',
    coaching_label: 'Coach de reuniones',
    coaching_desc: 'Tras cada reunión, la IA analiza tu intervención y te da consejos concretos para comunicarte mejor. Solo evalúa lo que tú dijiste — si no hablaste, no genera feedback.',
    coaching_enabled_label: 'Activar Coach de reuniones',
    chat_notice_label: 'Mensaje de aviso en el chat de Teams',
    chat_notice_enabled_label: 'Enviar aviso al iniciar la grabación',
    chat_notice_desc: 'Personaliza el mensaje que se envía al chat de la reunión cuando empieza la grabación.',
    chat_notice_ph: '🔴 This meeting is being recorded for documentation purposes...',
    whisper_desc: 'Modelo Whisper para transcribir el audio. Más grande = más preciso pero más lento. Se aplica al reiniciar.',
    open_folder_btn: 'Abrir',
    proj_dir_ph: 'Carpeta del proyecto (opcional)',
    proj_stake_label: 'Stakeholder Emails',
    proj_stake_desc: 'Estos contactos se añadirán automáticamente como destinatarios cuando envíes las minutas de una reunión de este proyecto por email. Separa varios emails con comas.',
    proj_dir_label: 'Carpeta del proyecto',
    proj_dir_desc: 'Por defecto, Noted guarda todo en su propia carpeta. Si seleccionas aquí la carpeta raíz de tu proyecto, las acciones de Claude se ejecutarán desde ahí.',
    proj_folder_default: 'Carpeta de Noted (por defecto)',
    proj_folder_browse: 'Seleccionar carpeta',
    proj_folder_clear: 'Restablecer',
    proj_context_label: 'Memoria del proyecto',
    proj_context_desc: 'Carpetas de documentos que Claude usará como contexto al generar minutas (READMEs, PRDs, specs técnicas…). Se sincronizan automáticamente.',
    proj_context_add: '+ Añadir carpeta',
    proj_context_empty: 'Sin carpetas de memoria',
    proj_pdf_label: 'Auto-guardar PDF',
    proj_pdf_desc: 'Guardar un PDF automáticamente en esta carpeta cada vez que se generen minutas.',
    proj_pdf_empty: 'Sin carpeta configurada',
    proj_pdf_browse: 'Seleccionar carpeta',
    pdf_download: 'Descargar PDF',
    semantic_layer: 'Semantic Layer',
    semantic_started: 'Generando capa semántica… puede tardar unos minutos',
    semantic_done: 'Capa semántica generada',
    semantic_error: 'Error al generar la capa semántica',
    semantic_timeout: 'La capa semántica tardó demasiado — inténtalo de nuevo',
    semantic_no_transcript: 'No hay transcripción para esta reunión',
    semantic_no_skill: 'Skill knowledge-capture no encontrada — sincroniza los team skills primero',
    semantic_no_claude: 'Claude CLI no encontrado en el PATH',
    tab_semantic: 'Semantic Layer',
    semantic_tab_empty: 'Aún no se ha generado la capa semántica para esta reunión.',
    semantic_tab_generate: 'Generar capa semántica',
    semantic_tab_regen: 'Regenerar',
    semantic_generated_at: 'Generado el',
    semantic_reveal: 'Mostrar en carpeta',
    semantic_open: 'Abrir',
    semantic_copy: 'Copiar ruta',
    semantic_copied: 'Ruta copiada',
    semantic_copy_fail: 'No se pudo copiar',
    semantic_open_inputs: 'Abrir carpeta de inputs',
    semantic_open_ontoforge: 'Abrir OntoForge',
    semantic_dialog_title: 'Generar capa semántica',
    semantic_dialog_msg: 'Voy a usar la skill de <span style="color:var(--accent);font-weight:600">captura de conocimiento</span> para crear tu CSV de entrada de la capa semántica para OntoForge.<br><br>¿Quieres incluir otros documentos además de la transcripción?',
    semantic_dialog_browse: 'Añadir documentos…',
    semantic_dialog_none: 'Si no, solo se usará la transcripción de la reunión.',
    semantic_dialog_generate: 'Generar',
    semantic_dialog_remove: 'Quitar',
    account_settings_title: 'Cuenta',
    project_settings_title: 'Configuración de proyectos',
    no_projects: 'Sin proyectos',
    sidebar_days: 'Días', sidebar_projects: 'Proyectos',
    all_projects: 'Todos los proyectos',
    toast_model_saved: 'Modelo guardado. Reinicia para aplicar.',
    edit_btn: 'Editar', cancel_btn: 'Cancelar',
    today: 'Hoy', yesterday: 'Ayer', no_date: 'Sin fecha',
    no_minutes: 'Sin minutas disponibles',
    project_label: 'Proyecto',
    btn_detect_projects: 'Re-detectar proyectos',
    toast_detecting: 'Detectando proyectos en segundo plano…',
    toast_project_set: 'Proyecto actualizado',
    export_settings_title: 'Guardar en carpeta de proyecto',
    export_settings_desc: 'Todo se guarda siempre dentro de la app, como siempre. Además, si al crear un proyecto le asignas una carpeta, se guardarán copias de estos archivos también ahí:',
    export_settings_desc_project: 'Todo se guarda siempre en la app. Marca qué copias quieres que se guarden también en la carpeta de ESTE proyecto (requiere carpeta configurada):',
    export_needs_folder: 'Selecciona una carpeta de proyecto (con "Editar") para poder guardar copias.',
    export_html: 'Notas en HTML',
    export_email: 'Notas en formato email',
    export_pdf: 'Notas en PDF',
    export_transcript: 'Transcripción',
    whisper_tiny:   'tiny — Muy rápido, menor precisión',
    whisper_base:   'base — Rápido',
    whisper_small:  'small — Equilibrado',
    whisper_medium: 'medium — Buena precisión (recomendado)',
    whisper_large:  'large — Máxima precisión, más lento',
    // Task board
    task_col_name: 'Nombre de tarea', task_col_status: 'Estado',
    task_col_assignee: 'Responsable', task_col_deadline: 'Fecha límite',
    task_col_start_date: 'Fecha inicio', task_col_end_date: 'Fecha fin',
    task_col_tags: 'Etiquetas', task_col_priority: 'Prioridad', task_col_bucket: 'Columna',
    task_col_dates: 'Fechas', tags_placeholder: 'Añadir etiqueta…',
    task_filter_all: 'Todos',
    view_list: 'Vista lista', view_board: 'Vista tablero', refresh: 'Actualizar',
    discard_job: 'Descartar esta reunión',
    discard_job_confirm: '¿Descartar esta reunión? El audio y lo que se haya generado se moverán a la papelera, de donde puedes recuperarlos.',
    discard_job_done: 'Reunión descartada — está en la papelera',
    status_not_started: 'Sin empezar', status_in_progress: 'En curso', status_done: 'Completado',
    status_blocked: 'Bloqueada', status_paused: 'En pausa', status_pending_feedback: 'Pend. feedback',
    task_col_description: 'Descripción', desc_placeholder: 'Añade una descripción...',
    proj_color_label: 'Color del proyecto',
    priority_none: '— Prioridad', priority_high: 'Alta', priority_medium: 'Media', priority_low: 'Baja',
    task_add_task: '+ Nueva tarea', task_add_subitem: '+ Nuevo subitem',
    task_new_ph: 'Nombre de la tarea…', task_empty: 'Sin tareas. Usa "+ Nueva tarea" para añadir.',
    task_no_project: 'Sin proyecto', bucket_label: 'Columna',
    bucket_add: '+ Columna', bucket_new_name: 'Nueva columna', bucket_name_prompt: 'Nombre de la nueva columna:',
    sort_placeholder: 'Ordenar…', sort_end_date: 'Por fecha límite', sort_priority: 'Por prioridad', sort_title: 'Por título', sort_tag: 'Por etiqueta',
    btn_save_task: 'Guardar', last_edited_label: 'Guardado', last_edited_never: 'Sin cambios guardados',
    modal_move_title: 'Añadir al panel', modal_project_label: 'Proyecto',
    modal_view_label: 'Añadir a', modal_view_list: 'Lista', modal_view_board: 'Kanban',
    modal_parent_label: 'Como sub-tarea de (opcional)',
    modal_parent_none: 'Tarea nueva (nivel raíz)', modal_confirm: 'Añadir al panel',
    task_from_meeting: 'de reunión',
    drawer_meeting_label: 'Reunión asociada',
    drawer_title: 'Detalle de tarea',
    pipeline_title: 'En curso',
    pipeline_idle: 'Sin actividad',
    btn_edit: 'Editar',
    edit_notes_hint: 'Edita las notas directamente y guarda. Los cambios se usarán en HTML y email.',
    notes_saved: 'Notas guardadas',
    import_transcript_tooltip: 'Importar transcript (.txt) y generar notas',
    import_transcript_processing: 'Generando notas del transcript...',
    import_transcript_done: 'Notas generadas del transcript importado',
    import_transcript_error: 'Error al importar el transcript',
    export_transcript_btn: 'Exportar transcript',
    export_transcript_no_file: 'No hay transcript guardado para esta reunión',
    export_transcript_done: 'Transcript exportado',
    export_transcript_error: 'Error al exportar el transcript',
    fmt_bold: 'Negrita', fmt_italic: 'Cursiva', fmt_h2: 'Título', fmt_h3: 'Subtítulo', fmt_text: 'Texto normal', fmt_list: 'Lista',
    fmt_table: 'Tabla', table_need_cursor: 'Pon el cursor dentro de una tabla',
    tbl_insert: 'Insertar tabla', tbl_addrow: 'Añadir fila', tbl_delrow: 'Eliminar fila',
    tbl_addcol: 'Añadir columna', tbl_delcol: 'Eliminar columna', tbl_delete: 'Eliminar tabla',
    save: 'Guardar',
    cancel: 'Cancelar',
    settings_saved: 'Ajustes guardados',
    btn_delete_meeting: 'Eliminar reunión',
    btn_add_action: '+ Añadir acción',
    add_action_title_ph: 'Título de la acción...',
    add_action_assignee_ph: 'Responsable (opcional)',
    add_action_deadline_ph: 'Fecha límite (opcional, YYYY-MM-DD)',
    add_action_save: 'Guardar',
    add_action_cancel: 'Cancelar',
    delete_project_btn: 'Eliminar proyecto',
    send_email: 'Enviar por correo',
    restart_tour: 'Repetir tour de bienvenida',
    lang_pick_title: 'Elige tu idioma',
    lang_pick_subtitle: 'Puedes cambiarlo después en Ajustes',
    tour_btn_next: 'Siguiente', tour_btn_prev: 'Anterior', tour_btn_done: 'Empezar', tour_btn_skip: 'Saltar tour',
    tour_s1_title: '👋 Bienvenida a Noted', tour_s1_desc: 'En los próximos pasos te enseñamos todo lo que puedes hacer. Hemos cargado una reunión de demo para que puedas verlo con datos reales.',
    tour_s2_title: 'Noted vive en tu bandeja', tour_s2_desc: 'El icono aparece en la bandeja del sistema (esquina inferior derecha, puede estar oculto bajo la flecha ˄). Desde ahí puedes lanzar una grabación manual, darle contexto antes de grabar, ver el estado actual o cancelar una grabación en curso.',
    tour_s3_title: 'Tus notas, siempre aquí', tour_s3_desc: 'La sección principal donde viven todas tus minutas. Puedes acceder a ellas también haciendo clic en el icono de la bandeja.',
    tour_s4_title: 'Buscar en todas tus minutas', tour_s4_desc: 'Busca en el contenido de todas tus minutas a la vez. Atajo rápido: Ctrl+F desde cualquier vista.',
    tour_s5_title: 'Días o Proyectos', tour_s5_desc: 'Agrupa tus reuniones por fecha o por proyecto según lo que necesites en cada momento.',
    tour_s6_title: 'Tus reuniones', tour_s6_desc: 'Cada reunión grabada aparece aquí. Haz clic para abrirla. Clic derecho para fijarla arriba o eliminarla.',
    tour_s7_title: 'Importar transcript', tour_s7_desc: '¿Tienes un transcript de un cliente o un compañero? Impórtalo directamente y Noted generará las minutas igual que si lo hubiera grabado él.',
    tour_s8_title: 'Título editable', tour_s8_desc: 'El título se genera automáticamente a partir del contenido. Haz clic sobre él para editarlo cuando quieras.',
    tour_s9_title: 'Proyecto detectado', tour_s9_desc: 'Noted intenta detectar automáticamente a qué proyecto pertenece cada reunión. Si se ha equivocado, cámbialo aquí.',
    tour_s10_title: 'Minutas generadas por Claude', tour_s10_desc: 'El cuerpo incluye un resumen de lo hablado, las decisiones tomadas y las tareas detectadas y asignadas automáticamente.',
    tour_s11_title: 'Regenerar minutas', tour_s11_desc: 'Si el resultado no te convence, puedes darle contexto adicional — qué corregir, qué tono usar, qué añadir — y Claude regenera las minutas ajustadas a lo que necesitas.',
    tour_s12_title: 'Nota adhesiva', tour_s12_desc: 'Crea un post-it flotante sobre las minutas. Puedes arrastrarlo, minimizarlo o eliminarlo — es tu espacio para anotar lo que quieras sin tocar el contenido generado.',
    tour_s13_title: 'Hablar con Claude', tour_s13_desc: 'Abre las minutas directamente en Claude para hacer preguntas, profundizar en un tema o redactar un follow-up email personalizado.',
    tour_s14_title: 'Compartir y exportar', tour_s14_desc: 'Envía las minutas por email a los asistentes, copia el contenido al portapapeles, expórtalas como PDF o HTML. Todo en una barra de acciones rápidas.',
    tour_s15_title: 'Mover al panel de Acciones', tour_s15_desc: 'Cada acción detectada en las minutas tiene este botón. Úsalo para añadirla al panel de Acciones y hacerle seguimiento independiente vinculado a un proyecto.',
    tour_s16_title: 'Panel de Acciones', tour_s16_desc: 'Aquí tienes todas las tareas pendientes de todas tus reuniones en un solo sitio, sin tener que abrir cada minuta.',
    tour_s17_title: 'Kanban o lista', tour_s17_desc: 'Visualiza las acciones como tablero Kanban o como lista según tu preferencia.',
    tour_s18_title: 'Filtros', tour_s18_desc: 'Filtra las acciones por proyecto, persona asignada o estado para centrarte en lo que importa.',
    tour_s19_title: 'Detalle de tarea', tour_s19_desc: 'Haz clic en cualquier tarea para ver el detalle completo: de qué reunión salió, quién la tiene asignada y cuál es su estado actual.',
    tour_s20_title: 'Proyectos', tour_s20_desc: 'Define tus proyectos para clasificar tus notas y acciones. Por cada proyecto puedes configurar nombre, descripción, stakeholders para email, carpeta de trabajo y color.',
    tour_s21_title: 'Eliminados recientemente', tour_s21_desc: 'Las reuniones eliminadas se guardan aquí durante 30 días. Puedes recuperarlas antes de que desaparezcan definitivamente.',
    tour_s22_title: 'Tu nombre', tour_s22_desc: 'Ponle tu nombre para que Noted sepa qué acciones son tuyas cuando filtras por asignado.',
    tour_s23_title: 'Idioma y tema', tour_s23_desc: 'Elige el idioma de la interfaz y el tema claro u oscuro. Se aplica al instante.',
    tour_s24_title: 'Modelo de transcripción', tour_s24_desc: 'Controla la precisión de la transcripción. Medium es el equilibrio recomendado entre velocidad y calidad.',
    tour_s25_title: '🎯 Coach de reuniones', tour_s25_desc: 'Activa el Coach de reuniones para recibir consejos concretos de comunicación después de cada llamada. Solo analiza lo que tú dijiste.',
    tour_s26_title: 'Aviso al chat de Teams', tour_s26_desc: 'Configura el mensaje que Noted envía automáticamente al chat de Teams cuando empieza a grabar, para avisar a los participantes.',
    tour_s27_title: '🎉 ¡Ya lo sabes todo!', tour_s27_desc: 'Listo. Entra en tu próxima reunión de Teams y Noted hará el resto. El popup aparece en cuanto detecte la llamada — tienes 30 segundos para aceptar.',
  },
  en: {
    nav_notes: 'Notes', nav_action_panel: 'Action Panel', nav_projects: 'Projects', nav_trash: 'Recently Deleted', settings_nav: 'Settings',
    trash_desc: 'Deleted meetings are kept here. You can recover them or delete them permanently.',
    trash_empty: 'Trash is empty.',
    trash_recover: 'Recover', trash_delete_forever: 'Delete permanently',
    trash_recovered: 'Meeting recovered', trash_purged: 'Permanently deleted',
    trash_deleted_at: 'deleted', trash_files: 'files',
    trash_purge_confirm: title => `Permanently delete "${title}"? This cannot be undone.`,
    lang_label: 'App language', theme_label: 'Theme',
    n_pending: n => `${n} pending`,
    n_done:    n => `${n} done`,
    light: 'Light', dark: 'Dark',
    manage_actions: 'Pending actions',
    filter_pending: 'Pending', filter_done: 'Done',
    name_label: 'Your name', name_ph: 'Name to filter actions',
    search_ph: 'Search in minutes...',
    empty_title: 'Select a meeting',
    empty_sub: 'Choose a meeting from the list to view its notes and actions',
    loading: 'Loading...', loading_meetings: 'Loading meetings...',
    loading_actions: 'Loading actions...', no_meetings: 'No meetings yet',
    tab_notes: 'Notes', tab_transcript: 'Transcript', tab_actions: 'Manage actions', section_actions: 'Actions',
    copy_note: 'Copy notes', copy_actions: 'Copy actions',
    copy_transcript: 'Copy transcript', copy_transcript_done: 'Copied', copy_failed: 'Copy failed',
    email_actions: 'Send actions email', email_transcript: 'Send transcript email',
    more_actions: 'More options',
    sticky_add: 'Add sticky note', sticky_min: 'Minimize', sticky_del: 'Delete', sticky_ph: 'Note...',
    pin: 'Pin meeting', unpin: 'Unpin meeting', pinned: 'Pinned',
    add_action: 'Add action', toast_action_added: 'Action added',
    regen_actions: 'Regenerate actions',
    regen_actions_hint: 'Describe the changes you want in the actions (group, add, remove, rewrite...). Claude will regenerate them.',
    regen_actions_ph: 'e.g.: group the HTML edits into one action and add one to review the email',
    regen_actions_confirm: 'Regenerate', regen_actions_working: 'Regenerating...',
    regen_actions_done: 'Actions regenerated', regen_actions_error: 'Could not regenerate actions',
    no_owner: 'No owner',
    action_title_ph: 'Action description...', action_assignee_ph: 'Owner (optional)', action_deadline_ph: 'Deadline (optional)',
    n_total: n => `${n} total`,
    no_project_label: 'No project',
    no_actions: 'No actions in this meeting',
    btn_chat: 'Chat with Claude', prompt_label: 'Claude prompt',
    btn_export_project: 'Export to project', btn_export_project_title: 'Save transcript, HTML notes and email to the project folder',
    toast_export_ok: 'Exported to project folder',
    toast_export_no_project: 'This meeting has no project assigned',
    toast_export_no_dir: 'Project has no folder configured (go to Settings)',
    toast_export_error: 'Error exporting to project',
    btn_reenrich: 'Re-analyze', btn_reenrich_all: 'Re-analyze all',
    toast_enriching: 'Analyzing actions...', toast_enriching_all: n => `Analyzing ${n} notes...`,
    btn_execute: 'Send to Claude', btn_reexecute: 'Resend to Claude',
    btn_detail: 'View Claude detail',
    btn_prompt_show: '▼ View and edit prompt', btn_prompt_hide: '▲ Hide',
    doc_file_label: 'Document file', doc_file_ph: 'Select the file to modify', doc_browse_file: 'Select file',
    confirm_dir: 'Working directory', confirm_browse: 'Browse',
    btn_launch: 'Launch', btn_launch_cancel: 'Cancel',
    run_running: 'Running', run_done: 'Done', run_error: 'Error', run_needs_input: 'Needs input',
    btn_continue_terminal: 'Continue in terminal',
    runs_panel_title: 'Runs',
    done_from_terminal: 'completed from terminal',
    runs_fab_running: 'running', runs_fab_done: 'runs',
    btn_regenerate: 'Regenerate notes',
    regen_placeholder: 'Objective or additional context to regenerate notes... e.g. "My goal is to better understand agentic architecture concepts"',
    regen_confirm: 'Regenerate',
    regen_cancel: 'Cancel',
    toast_regenerating: 'Regenerating notes with Claude...',
    toast_regen_done: 'Notes regenerated.',
    toast_regen_error: 'Original transcript not found. Cannot regenerate.',
    regen_stage_transcript_ok: 'Transcript found',
    regen_stage_generating: 'Generating notes with Claude...',
    regen_stage_saving: 'Saving notes...',
    regen_stage_html: 'Exporting HTML...',
    regen_stage_actions: 'Analyzing actions...',
    regen_stage_done: 'Done!',
    regen_stage_error: 'Error regenerating',
    panel_running: 'Running...', panel_done: 'Done', panel_error: 'Error',
    panel_followup_ph: 'Add context or ask a question...',
    btn_done: 'Mark done', btn_done_state: 'Done',
    btn_move_panel: 'Move to panel', btn_in_panel: 'In panel',
    btn_go_notes: 'Go to notes', btn_mark_complete: 'Mark as complete',
    btn_delete: 'Delete', toast_deleted: 'Action deleted',
    confirm_delete_action_title: 'Delete action',
    confirm_delete_action: title => `Delete action "${title}"? This action cannot be undone.`,
    confirm_delete_title: 'Delete project',
    confirm_delete_project: name => name
      ? `Are you sure you want to delete the project "${name}"? This action cannot be undone.`
      : 'Are you sure you want to delete this project? This action cannot be undone.',
    ctx_delete_meeting: 'Delete',
    confirm_delete_meeting_title: 'Delete meeting',
    confirm_delete_meeting: title => `Move the meeting "${title}" to Trash? You can recover it from the Trash.`,
    confirm_delete_task_title: 'Delete task',
    confirm_delete_task: title => `Delete task "${title}"? This action cannot be undone.`,
    confirm_delete_column_title: 'Delete column',
    confirm_delete_column: (name, count, target) => count
      ? `Delete column "${name}"? Its ${count} task(s) will move to "${target}".`
      : `Delete column "${name}"?`,
    toast_meeting_deleted: 'Meeting deleted',
    no_match_actions: 'No actions in the panel. Move actions from "Manage actions".',
    toast_claude: 'Opening Claude...', toast_terminal: 'Opening terminal...',
    toast_outlook: 'Opening Outlook...', toast_done: 'Action marked as done',
    toast_moved: 'Action moved to panel',
    pending_actions_title: 'Pending actions',
    type_instruction: 'Instruction', type_code: 'Code', type_doc: 'Document',
    who_claude: 'Claude', who_human: 'Manual',
    no_prompt: 'No runnable prompt',
    owner_label: 'Owner', created_label: 'Created', deadline_label: 'Deadline',
    sort_label: 'Sort', sort_newest: 'Newest', sort_oldest: 'Oldest',
    sort_deadline_asc: 'Deadline ↑', sort_deadline_desc: 'Deadline ↓',
    es_label: 'Español', en_label: 'English', ca_label: 'Català',
    refresh_meetings: 'Refresh meetings',
    name_desc: 'Used to identify actions assigned to you',
    projects_desc: 'Noted automatically detects which project each meeting belongs to and lets you filter actions by project',
    add_project: '+ Add project',
    proj_name_ph: 'Project name', proj_desc_ph: 'Short description', proj_stake_ph: 'comma-separated emails',
    save_btn: 'Save',
    recording_title: 'Recording & Transcription',
    coaching_label: 'Meeting Coach',
    coaching_desc: 'After each meeting, AI analyses your contributions and gives you concrete tips to communicate more effectively. It only evaluates what you said — if you didn\'t speak, no feedback is generated.',
    coaching_enabled_label: 'Enable Meeting Coach',
    chat_notice_label: 'Teams chat recording notice',
    chat_notice_enabled_label: 'Send notice when recording starts',
    chat_notice_desc: 'Customize the message sent to the meeting chat when recording starts.',
    chat_notice_ph: '🔴 This meeting is being recorded for documentation purposes...',
    whisper_desc: 'Whisper model for audio transcription. Larger = more accurate but slower. Takes effect after restart.',
    open_folder_btn: 'Open',
    proj_dir_ph: 'Project folder (optional)',
    proj_stake_label: 'Stakeholder Emails',
    proj_stake_desc: 'These contacts will be automatically added as recipients when you send meeting minutes for this project by email. Separate multiple emails with commas.',
    proj_dir_label: 'Project folder',
    proj_dir_desc: 'By default, Noted saves everything in its own folder. Select your project\'s root folder here so Claude actions run from the right working directory.',
    proj_folder_default: 'Noted folder (default)',
    proj_folder_browse: 'Browse folder',
    proj_folder_clear: 'Reset',
    proj_context_label: 'Project memory',
    proj_context_desc: 'Document folders Claude will use as context when generating minutes (READMEs, PRDs, technical specs…). Synced automatically.',
    proj_context_add: '+ Add folder',
    proj_context_empty: 'No memory folders',
    proj_pdf_label: 'Auto-save PDF',
    proj_pdf_desc: 'Save a PDF automatically to this folder whenever minutes are generated.',
    proj_pdf_empty: 'No folder configured',
    proj_pdf_browse: 'Select folder',
    pdf_download: 'Download PDF',
    semantic_layer: 'Semantic Layer',
    semantic_started: 'Generating semantic layer… this can take a few minutes',
    semantic_done: 'Semantic layer generated',
    semantic_error: 'Failed to generate the semantic layer',
    semantic_timeout: 'Semantic layer timed out — please try again',
    semantic_no_transcript: 'No transcript available for this meeting',
    semantic_no_skill: 'knowledge-capture skill not found — sync team skills first',
    semantic_no_claude: 'Claude CLI not found in PATH',
    tab_semantic: 'Semantic Layer',
    semantic_tab_empty: 'No semantic layer has been generated for this meeting yet.',
    semantic_tab_generate: 'Generate semantic layer',
    semantic_tab_regen: 'Regenerate',
    semantic_generated_at: 'Generated on',
    semantic_reveal: 'Reveal in folder',
    semantic_open: 'Open',
    semantic_copy: 'Copy path',
    semantic_copied: 'Path copied',
    semantic_copy_fail: 'Could not copy',
    semantic_open_inputs: 'Open inputs folder',
    semantic_open_ontoforge: 'Open OntoForge',
    semantic_dialog_title: 'Generate semantic layer',
    semantic_dialog_msg: 'I\'ll use the <span style="color:var(--accent);font-weight:600">knowledge-capture</span> skill to create your semantic layer CSV input for OntoForge.<br><br>Would you like to include any documents besides the transcript?',
    semantic_dialog_browse: 'Add documents…',
    semantic_dialog_none: 'If not, only the meeting transcript will be used.',
    semantic_dialog_generate: 'Generate',
    semantic_dialog_remove: 'Remove',
    account_settings_title: 'Account',
    project_settings_title: 'Project settings',
    no_projects: 'No projects yet',
    sidebar_days: 'Days', sidebar_projects: 'Projects',
    all_projects: 'All projects',
    toast_model_saved: 'Model saved. Restart to apply.',
    edit_btn: 'Edit', cancel_btn: 'Cancel',
    today: 'Today', yesterday: 'Yesterday', no_date: 'No date',
    no_minutes: 'No minutes available',
    project_label: 'Project',
    btn_detect_projects: 'Re-detect projects',
    toast_detecting: 'Detecting projects in background…',
    toast_project_set: 'Project updated',
    export_settings_title: 'Save to project folder',
    export_settings_desc: 'Everything is always saved inside the app, as usual. In addition, if you assign a folder when creating a project, copies of these files will also be saved there:',
    export_settings_desc_project: 'Everything is always saved in the app. Tick which copies you also want saved to THIS project\'s folder (requires a folder to be set):',
    export_needs_folder: 'Select a project folder (via "Edit") to enable saving copies.',
    export_html: 'HTML notes',
    export_email: 'Email-format notes',
    export_pdf: 'PDF notes',
    export_transcript: 'Transcript',
    whisper_tiny:   'tiny — Very fast, lower accuracy',
    whisper_base:   'base — Fast',
    whisper_small:  'small — Balanced',
    whisper_medium: 'medium — Good accuracy (recommended)',
    whisper_large:  'large — Best accuracy, slower',
    // Task board
    task_col_name: 'Task name', task_col_status: 'Status',
    task_col_assignee: 'Assignee', task_col_deadline: 'Deadline',
    task_col_start_date: 'Start date', task_col_end_date: 'End date',
    task_col_tags: 'Tags', task_col_priority: 'Priority', task_col_bucket: 'Column',
    task_col_dates: 'Dates', tags_placeholder: 'Add tag…',
    task_filter_all: 'All',
    view_list: 'List view', view_board: 'Board view', refresh: 'Refresh',
    discard_job: 'Discard this meeting',
    discard_job_confirm: 'Discard this meeting? The audio and anything already generated will be moved to the bin, where you can still recover them.',
    discard_job_done: 'Meeting discarded — it is in the bin',
    status_not_started: 'Not started', status_in_progress: 'In progress', status_done: 'Done',
    status_blocked: 'Blocked', status_paused: 'Paused', status_pending_feedback: 'Pending feedback',
    task_col_description: 'Description', desc_placeholder: 'Add a description...',
    proj_color_label: 'Project color',
    priority_none: '— Priority', priority_high: 'High', priority_medium: 'Medium', priority_low: 'Low',
    task_add_task: '+ New task', task_add_subitem: '+ New sub-item',
    task_new_ph: 'Task name…', task_empty: 'No tasks. Use "+ New task" to add one.',
    task_no_project: 'No project', bucket_label: 'Column',
    bucket_add: '+ Column', bucket_new_name: 'New column', bucket_name_prompt: 'New column name:',
    sort_placeholder: 'Sort…', sort_end_date: 'By due date', sort_priority: 'By priority', sort_title: 'By title', sort_tag: 'By tag',
    btn_save_task: 'Save', last_edited_label: 'Saved', last_edited_never: 'No changes saved',
    modal_move_title: 'Add to panel', modal_project_label: 'Project',
    modal_view_label: 'Add to', modal_view_list: 'List', modal_view_board: 'Kanban',
    modal_parent_label: 'As sub-item of (optional)',
    modal_parent_none: 'New task (top level)', modal_confirm: 'Add to panel',
    task_from_meeting: 'from meeting',
    drawer_meeting_label: 'Related meeting',
    drawer_title: 'Task detail',
    pipeline_title: 'In progress',
    pipeline_idle: 'No activity',
    btn_edit: 'Edit',
    edit_notes_hint: 'Edit the notes directly and save. Changes are used for HTML and email.',
    notes_saved: 'Notes saved',
    import_transcript_tooltip: 'Import transcript (.txt) and generate notes',
    import_transcript_processing: 'Generating notes from transcript...',
    import_transcript_done: 'Notes generated from imported transcript',
    import_transcript_error: 'Error importing transcript',
    export_transcript_btn: 'Export transcript',
    export_transcript_no_file: 'No transcript saved for this meeting',
    export_transcript_done: 'Transcript exported',
    export_transcript_error: 'Error exporting transcript',
    fmt_bold: 'Bold', fmt_italic: 'Italic', fmt_h2: 'Heading', fmt_h3: 'Subheading', fmt_text: 'Normal text', fmt_list: 'List',
    fmt_table: 'Table', table_need_cursor: 'Place the cursor inside a table',
    tbl_insert: 'Insert table', tbl_addrow: 'Add row', tbl_delrow: 'Delete row',
    tbl_addcol: 'Add column', tbl_delcol: 'Delete column', tbl_delete: 'Delete table',
    save: 'Save',
    cancel: 'Cancel',
    settings_saved: 'Settings saved',
    btn_delete_meeting: 'Delete meeting',
    btn_add_action: '+ Add action',
    add_action_title_ph: 'Action title...',
    add_action_assignee_ph: 'Assignee (optional)',
    add_action_deadline_ph: 'Deadline (optional, YYYY-MM-DD)',
    add_action_save: 'Save',
    add_action_cancel: 'Cancel',
    delete_project_btn: 'Delete project',
    send_email: 'Send by email',
    restart_tour: 'Replay welcome tour',
    lang_pick_title: 'Choose your language',
    lang_pick_subtitle: 'You can change it later in Settings',
    tour_btn_next: 'Next', tour_btn_prev: 'Previous', tour_btn_done: 'Get started', tour_btn_skip: 'Skip tour',
    tour_s1_title: '👋 Welcome to Noted', tour_s1_desc: 'In the next steps we\'ll show you everything you can do. We\'ve loaded a demo meeting so you can see it with real data.',
    tour_s2_title: 'Noted lives in your tray', tour_s2_desc: 'The icon appears in the system tray (bottom right, may be hidden under the ˄ arrow). From there you can start a manual recording, add context before recording, see the current status or cancel an ongoing recording.',
    tour_s3_title: 'Your notes, always here', tour_s3_desc: 'The main section where all your minutes live. You can also access them by clicking the tray icon.',
    tour_s4_title: 'Search all your minutes', tour_s4_desc: 'Search the content of all your minutes at once. Quick shortcut: Ctrl+F from any view.',
    tour_s5_title: 'Days or Projects', tour_s5_desc: 'Group your meetings by date or by project depending on what you need at each moment.',
    tour_s6_title: 'Your meetings', tour_s6_desc: 'Each recorded meeting appears here. Click to open it. Right-click to pin it to the top or delete it.',
    tour_s7_title: 'Import transcript', tour_s7_desc: 'Have a transcript from a client or colleague? Import it directly and Noted will generate the minutes just as if it had recorded it itself.',
    tour_s8_title: 'Editable title', tour_s8_desc: 'The title is automatically generated from the content. Click on it to edit it whenever you want.',
    tour_s9_title: 'Detected project', tour_s9_desc: 'Noted tries to automatically detect which project each meeting belongs to. If it got it wrong, change it here.',
    tour_s10_title: 'Claude-generated minutes', tour_s10_desc: 'The body includes a summary of what was discussed, the decisions made and the automatically detected and assigned tasks.',
    tour_s11_title: 'Regenerate minutes', tour_s11_desc: 'If the result doesn\'t satisfy you, you can give it additional context — what to correct, what tone to use, what to add — and Claude regenerates the minutes adjusted to what you need.',
    tour_s12_title: 'Sticky note', tour_s12_desc: 'Create a floating sticky note on the minutes. You can drag it, minimize it or delete it — it\'s your space to jot down whatever you want without touching the generated content.',
    tour_s13_title: 'Chat with Claude', tour_s13_desc: 'Open the minutes directly in Claude to ask questions, go deeper on a topic or draft a personalised follow-up email.',
    tour_s14_title: 'Share and export', tour_s14_desc: 'Send the minutes by email to attendees, copy the content to the clipboard, export them as PDF or HTML. All in a quick action bar.',
    tour_s15_title: 'Move to Actions panel', tour_s15_desc: 'Each action detected in the minutes has this button. Use it to add it to the Actions panel and track it independently linked to a project.',
    tour_s16_title: 'Actions Panel', tour_s16_desc: 'Here you have all pending tasks from all your meetings in one place, without having to open each minute.',
    tour_s17_title: 'Kanban or list', tour_s17_desc: 'View actions as a Kanban board or as a list depending on your preference.',
    tour_s18_title: 'Filters', tour_s18_desc: 'Filter actions by project, assigned person or status to focus on what matters.',
    tour_s19_title: 'Task detail', tour_s19_desc: 'Click on any task to see the full detail: which meeting it came from, who it\'s assigned to and what its current status is.',
    tour_s20_title: 'Projects', tour_s20_desc: 'Define your projects to classify your notes and actions. For each project you can configure name, description, stakeholders for email, working folder and colour.',
    tour_s21_title: 'Recently deleted', tour_s21_desc: 'Deleted meetings are saved here for 30 days. You can recover them before they disappear permanently.',
    tour_s22_title: 'Your name', tour_s22_desc: 'Add your name so Noted knows which actions are yours when you filter by assignee.',
    tour_s23_title: 'Language and theme', tour_s23_desc: 'Choose the interface language and light or dark theme. Applied instantly.',
    tour_s24_title: 'Transcription model', tour_s24_desc: 'Controls transcription accuracy. Medium is the recommended balance between speed and quality.',
    tour_s25_title: '🎯 Meeting Coach', tour_s25_desc: 'Enable Meeting Coach to receive concrete communication tips after each call. It only analyses what you said.',
    tour_s26_title: 'Teams chat notice', tour_s26_desc: 'Configure the message Noted automatically sends to the Teams chat when it starts recording, to notify participants.',
    tour_s27_title: '🎉 You\'re all set!', tour_s27_desc: 'Ready. Join your next Teams meeting and Noted will do the rest. The popup appears as soon as it detects the call — you have 30 seconds to accept.',
  },
  ca: {
    nav_notes: 'Notes', nav_action_panel: 'Panell d\'accions', nav_projects: 'Projectes', nav_trash: 'Eliminats recentment', settings_nav: 'Configuració',
    trash_desc: 'Les reunions eliminades es guarden aquí. Les pots recuperar o esborrar definitivament.',
    trash_empty: 'La paperera és buida.',
    trash_recover: 'Recuperar', trash_delete_forever: 'Esborrar definitivament',
    trash_recovered: 'Reunió recuperada', trash_purged: 'Eliminada definitivament',
    trash_deleted_at: 'eliminada', trash_files: 'arxius',
    trash_purge_confirm: title => `Esborrar definitivament "${title}"? No es podrà recuperar.`,
    lang_label: 'Idioma de l\'app', theme_label: 'Tema',
    n_pending: n => `${n} pendent${n > 1 ? 's' : ''}`,
    n_done:    n => `${n} feta${n > 1 ? 's' : ''}`,
    light: 'Clar', dark: 'Fosc',
    manage_actions: 'Accions pendents',
    filter_pending: 'Pendents', filter_done: 'Fetes',
    name_label: 'El teu nom', name_ph: 'Nom per filtrar accions',
    search_ph: 'Cerca a les actes...',
    empty_title: 'Selecciona una reunió',
    empty_sub: 'Tria una reunió de la llista per veure les actes i accions',
    loading: 'Carregant...', loading_meetings: 'Carregant reunions...',
    loading_actions: 'Carregant accions...', no_meetings: 'No hi ha reunions encara',
    tab_notes: 'Notes', tab_transcript: 'Transcripció', tab_actions: 'Gestionar accions', section_actions: 'Accions',
    copy_note: 'Copiar notes', copy_actions: 'Copiar accions',
    copy_transcript: 'Copiar transcripció', copy_transcript_done: 'Copiat', copy_failed: 'Error en copiar',
    email_actions: 'Enviar accions per correu', email_transcript: 'Enviar transcripció per correu',
    more_actions: 'Més opcions',
    sticky_add: 'Afegir nota adhesiva', sticky_min: 'Minimitzar', sticky_del: 'Eliminar', sticky_ph: 'Nota...',
    pin: 'Fixar reunió', unpin: 'Desfixar reunió', pinned: 'Fixades',
    add_action: 'Afegir acció', toast_action_added: 'Acció afegida',
    regen_actions: 'Regenerar accions',
    regen_actions_hint: 'Descriu quins canvis vols a les accions (agrupar, afegir, treure, reescriure...). Claude les regenerarà.',
    regen_actions_ph: 'Ex: agrupa els canvis de l\'HTML en una sola acció i afegeix-ne una per revisar l\'email',
    regen_actions_confirm: 'Regenerar', regen_actions_working: 'Regenerant...',
    regen_actions_done: 'Accions regenerades', regen_actions_error: 'No s\'han pogut regenerar les accions',
    no_owner: 'Sense responsable',
    action_title_ph: 'Descripció de l\'acció...', action_assignee_ph: 'Responsable (opcional)', action_deadline_ph: 'Data límit (opcional)',
    n_total: n => `${n} en total`,
    no_project_label: 'Sense projecte',
    no_actions: 'No hi ha accions en aquesta reunió',
    btn_chat: 'Xat amb Claude', prompt_label: 'Prompt per a Claude',
    btn_export_project: 'Exportar al projecte', btn_export_project_title: 'Desa transcripció, notes HTML i correu a la carpeta del projecte',
    toast_export_ok: 'Exportat a la carpeta del projecte',
    toast_export_no_project: 'Aquesta reunió no té cap projecte assignat',
    toast_export_no_dir: 'El projecte no té cap carpeta configurada (ves a Configuració)',
    toast_export_error: 'Error en exportar al projecte',
    btn_reenrich: 'Re-analitzar', btn_reenrich_all: 'Re-analitzar totes',
    toast_enriching: 'Analitzant accions...', toast_enriching_all: n => `Analitzant ${n} notes...`,
    btn_execute: 'Enviar a Claude', btn_reexecute: 'Reenviar a Claude',
    btn_detail: 'Veure detall Claude',
    btn_prompt_show: '▼ Veure prompt i editar', btn_prompt_hide: '▲ Amagar',
    doc_file_label: 'Arxiu del document', doc_file_ph: 'Selecciona l\'arxiu a modificar', doc_browse_file: 'Seleccionar arxiu',
    confirm_dir: 'Directori de treball', confirm_browse: 'Explorar',
    btn_launch: 'Llançar', btn_launch_cancel: 'Cancel·lar',
    run_running: 'Executant', run_done: 'Fet', run_error: 'Error', run_needs_input: 'Necessita input',
    btn_continue_terminal: 'Continuar al terminal',
    runs_panel_title: 'Execucions',
    done_from_terminal: 'completat des del terminal',
    runs_fab_running: 'executant', runs_fab_done: 'runs',
    btn_regenerate: 'Regenerar actes',
    regen_placeholder: 'Objectiu o context addicional per regenerar les actes...',
    regen_confirm: 'Regenerar',
    regen_cancel: 'Cancel·lar',
    toast_regenerating: 'Regenerant actes amb Claude...',
    toast_regen_done: 'Actes regenerades.',
    toast_regen_error: 'No s\'ha trobat la transcripció original per regenerar.',
    regen_stage_transcript_ok: 'Transcripció trobada',
    regen_stage_generating: 'Generant actes amb Claude...',
    regen_stage_saving: 'Desant actes...',
    regen_stage_html: 'Exportant HTML...',
    regen_stage_actions: 'Analitzant accions...',
    regen_stage_done: 'Completat!',
    regen_stage_error: 'Error en regenerar',
    panel_running: 'Executant...', panel_done: 'Completat', panel_error: 'Error',
    panel_followup_ph: 'Afegeix context o fes una pregunta...',
    btn_done: 'Marcar com a feta', btn_done_state: 'Feta',
    btn_move_panel: 'Moure al panell', btn_in_panel: 'Al panell',
    btn_go_notes: 'Anar a notes', btn_mark_complete: 'Marcar com a feta',
    btn_delete: 'Eliminar', toast_deleted: 'Acció eliminada',
    confirm_delete_action_title: 'Eliminar acció',
    confirm_delete_action: title => `Eliminar l'acció "${title}"? Aquesta acció no es pot desfer.`,
    confirm_delete_title: 'Eliminar projecte',
    confirm_delete_project: name => name
      ? `Segur que vols eliminar el projecte "${name}"? Aquesta acció no es pot desfer.`
      : 'Segur que vols eliminar aquest projecte? Aquesta acció no es pot desfer.',
    ctx_delete_meeting: 'Eliminar',
    confirm_delete_meeting_title: 'Eliminar reunió',
    confirm_delete_meeting: title => `Enviar la reunió "${title}" a la paperera? La podràs recuperar des de la Paperera.`,
    confirm_delete_task_title: 'Eliminar tasca',
    confirm_delete_task: title => `Eliminar la tasca "${title}"? Aquesta acció no es pot desfer.`,
    confirm_delete_column_title: 'Eliminar columna',
    confirm_delete_column: (name, count, target) => count
      ? `Eliminar la columna "${name}"? Les seves ${count} tasca(ques) passaran a "${target}".`
      : `Eliminar la columna "${name}"?`,
    toast_meeting_deleted: 'Reunió eliminada',
    no_match_actions: 'No hi ha accions al panell. Mou accions des de "Gestionar accions".',
    toast_claude: 'Obrint Claude...', toast_terminal: 'Obrint terminal...', toast_outlook: 'Obrint Outlook...', toast_done: 'Acció marcada com a feta',
    toast_moved: 'Acció moguda al panell',
    pending_actions_title: 'Accions pendents',
    type_instruction: 'Instrucció', type_code: 'Codi', type_doc: 'Document',
    who_claude: 'Claude', who_human: 'Manual',
    no_prompt: 'Sense prompt executable',
    owner_label: 'Owner', created_label: 'Creada', deadline_label: 'Data límit',
    sort_label: 'Ordenar', sort_newest: 'Més recents', sort_oldest: 'Més antigues',
    sort_deadline_asc: 'Data límit ↑', sort_deadline_desc: 'Data límit ↓',
    es_label: 'Español', en_label: 'English', ca_label: 'Català',
    refresh_meetings: 'Actualitzar reunions',
    name_desc: 'S\'utilitza per identificar les teves accions assignades',
    projects_desc: 'Noted detecta automàticament a quin projecte pertany cada reunió i permet filtrar accions per projecte',
    add_project: '+ Afegir projecte',
    proj_name_ph: 'Nom del projecte', proj_desc_ph: 'Descripció curta', proj_stake_ph: 'correus separats per comes',
    save_btn: 'Desar',
    recording_title: 'Gravació i Transcripció',
    coaching_label: 'Coach de reunions',
    coaching_desc: 'Després de cada reunió, la IA analitza la teva intervenció i et dóna consells concrets per comunicar-te millor. Només avalua el que tu vas dir — si no vas parlar, no genera feedback.',
    coaching_enabled_label: 'Activar Coach de reunions',
    chat_notice_label: 'Missatge d\'avís al xat de Teams',
    chat_notice_enabled_label: 'Enviar avís en iniciar la gravació',
    chat_notice_desc: 'Personalitza el missatge que s\'envia al xat de la reunió quan comença la gravació.',
    chat_notice_ph: '🔴 This meeting is being recorded for documentation purposes...',
    whisper_desc: 'Model Whisper per transcriure l\'àudio. Més gran = més precís però més lent. S\'aplica en reiniciar.',
    open_folder_btn: 'Obrir',
    proj_dir_ph: 'Carpeta del projecte (opcional)',
    proj_stake_label: 'Stakeholder Emails',
    proj_stake_desc: 'Aquests contactes s\'afegiran automàticament com a destinataris quan enviïs les actes d\'una reunió d\'aquest projecte per correu. Separa diversos correus amb comes.',
    proj_dir_label: 'Carpeta del projecte',
    proj_dir_desc: 'Per defecte, Noted desa-ho tot a la seva pròpia carpeta. Si selecciones aquí la carpeta arrel del teu projecte, les accions de Claude s\'executaran des d\'allà.',
    proj_folder_default: 'Carpeta de Noted (per defecte)',
    proj_folder_browse: 'Seleccionar carpeta',
    proj_folder_clear: 'Restablir',
    proj_context_label: 'Memòria del projecte',
    proj_context_desc: 'Carpetes de documents que Claude usarà com a context en generar actes (READMEs, PRDs, specs tècniques…).',
    proj_context_add: '+ Afegir carpeta',
    proj_context_empty: 'Sense carpetes de memòria',
    proj_pdf_label: 'Auto-desar PDF',
    proj_pdf_desc: 'Desar un PDF automàticament en aquesta carpeta cada vegada que es generin actes.',
    proj_pdf_empty: 'Sense carpeta configurada',
    proj_pdf_browse: 'Seleccionar carpeta',
    pdf_download: 'Descarregar PDF',
    semantic_layer: 'Semantic Layer',
    semantic_started: 'Generant la capa semàntica… pot trigar uns minuts',
    semantic_done: 'Capa semàntica generada',
    semantic_error: 'Error en generar la capa semàntica',
    semantic_timeout: 'La capa semàntica ha trigat massa — torna-ho a intentar',
    semantic_no_transcript: 'No hi ha transcripció per a aquesta reunió',
    semantic_no_skill: 'Skill knowledge-capture no trobada — sincronitza els team skills primer',
    semantic_no_claude: 'Claude CLI no trobat al PATH',
    tab_semantic: 'Semantic Layer',
    semantic_tab_empty: 'Encara no s\'ha generat la capa semàntica per a aquesta reunió.',
    semantic_tab_generate: 'Generar capa semàntica',
    semantic_tab_regen: 'Regenerar',
    semantic_generated_at: 'Generat el',
    semantic_reveal: 'Mostrar a la carpeta',
    semantic_open: 'Obrir',
    semantic_copy: 'Copiar ruta',
    semantic_copied: 'Ruta copiada',
    semantic_copy_fail: 'No s\'ha pogut copiar',
    semantic_open_inputs: 'Obrir carpeta d\'inputs',
    semantic_open_ontoforge: 'Obrir OntoForge',
    semantic_dialog_title: 'Generar la capa semàntica',
    semantic_dialog_msg: 'Faré servir la skill de <span style="color:var(--accent);font-weight:600">captura de coneixement</span> per crear el teu CSV d\'entrada de la capa semàntica per a OntoForge.<br><br>Vols incloure altres documents a més de la transcripció?',
    semantic_dialog_browse: 'Afegir documents…',
    semantic_dialog_none: 'Si no, només s\'utilitzarà la transcripció de la reunió.',
    semantic_dialog_generate: 'Generar',
    semantic_dialog_remove: 'Treure',
    account_settings_title: 'Compte',
    project_settings_title: 'Configuració de projectes',
    no_projects: 'Sense projectes',
    sidebar_days: 'Dies', sidebar_projects: 'Projectes',
    all_projects: 'Tots els projectes',
    toast_model_saved: 'Model desat. Reinicia per aplicar.',
    edit_btn: 'Editar', cancel_btn: 'Cancel·lar',
    today: 'Avui', yesterday: 'Ahir', no_date: 'Sense data',
    no_minutes: 'Sense actes disponibles',
    project_label: 'Projecte',
    btn_detect_projects: 'Re-detectar projectes',
    toast_detecting: 'Detectant projectes en segon pla…',
    toast_project_set: 'Projecte actualitzat',
    export_settings_title: 'Desar a carpeta de projecte',
    export_settings_desc: 'Tot es desa sempre dins de l\'app. A més, si al crear un projecte li assignes una carpeta, es desaran còpies d\'aquests arxius també allà:',
    export_settings_desc_project: 'Tot es desa sempre a l\'app. Marca quines còpies vols que es desin també a la carpeta d\'AQUEST projecte (requereix carpeta configurada):',
    export_needs_folder: 'Selecciona una carpeta de projecte (amb "Editar") per poder desar còpies.',
    export_html: 'Notes en HTML',
    export_email: 'Notes en format correu',
    export_pdf: 'Notes en PDF',
    export_transcript: 'Transcripció',
    whisper_tiny:   'tiny — Molt ràpid, menor precisió',
    whisper_base:   'base — Ràpid',
    whisper_small:  'small — Equilibrat',
    whisper_medium: 'medium — Bona precisió (recomanat)',
    whisper_large:  'large — Màxima precisió, més lent',
    task_col_name: 'Nom de tasca', task_col_status: 'Estat',
    task_col_assignee: 'Responsable', task_col_deadline: 'Data límit',
    task_col_start_date: 'Data inici', task_col_end_date: 'Data fi',
    task_col_tags: 'Etiquetes', task_col_priority: 'Prioritat', task_col_bucket: 'Columna',
    task_col_dates: 'Dates', tags_placeholder: 'Afegir etiqueta…',
    task_filter_all: 'Tots',
    view_list: 'Vista llista', view_board: 'Vista tauler', refresh: 'Actualitzar',
    status_not_started: 'Sense començar', status_in_progress: 'En curs', status_done: 'Completat',
    status_blocked: 'Bloquejada', status_paused: 'En pausa', status_pending_feedback: 'Pend. feedback',
    task_col_description: 'Descripció', desc_placeholder: 'Afegeix una descripció...',
    proj_color_label: 'Color del projecte',
    priority_none: '— Prioritat', priority_high: 'Alta', priority_medium: 'Mitjana', priority_low: 'Baixa',
    task_add_task: '+ Nova tasca', task_add_subitem: '+ Nou subitem',
    task_new_ph: 'Nom de la tasca…', task_empty: 'Sense tasques. Usa "+ Nova tasca" per afegir-ne.',
    task_no_project: 'Sense projecte', bucket_label: 'Columna',
    bucket_add: '+ Columna', bucket_new_name: 'Nova columna', bucket_name_prompt: 'Nom de la nova columna:',
    sort_placeholder: 'Ordenar…', sort_end_date: 'Per data límit', sort_priority: 'Per prioritat', sort_title: 'Per títol', sort_tag: 'Per etiqueta',
    btn_save_task: 'Desar', last_edited_label: 'Desat', last_edited_never: 'Sense canvis desats',
    modal_move_title: 'Afegir al panell', modal_project_label: 'Projecte',
    modal_view_label: 'Afegir a', modal_view_list: 'Llista', modal_view_board: 'Kanban',
    modal_parent_label: 'Com a sub-tasca de (opcional)',
    modal_parent_none: 'Tasca nova (nivell arrel)', modal_confirm: 'Afegir al panell',
    task_from_meeting: 'de reunió',
    drawer_meeting_label: 'Reunió associada',
    drawer_title: 'Detall de tasca',
    pipeline_title: 'En curs',
    pipeline_idle: 'Sense activitat',
    btn_edit: 'Editar',
    edit_notes_hint: 'Edita les notes directament i desa. Els canvis s\'usaran en HTML i correu.',
    notes_saved: 'Notes desades',
    import_transcript_tooltip: 'Importar transcripció (.txt) i generar notes',
    import_transcript_processing: 'Generant notes de la transcripció...',
    import_transcript_done: 'Notes generades de la transcripció importada',
    import_transcript_error: 'Error en importar la transcripció',
    export_transcript_btn: 'Exportar transcripció',
    export_transcript_no_file: 'No hi ha transcripció desada per a aquesta reunió',
    export_transcript_done: 'Transcripció exportada',
    export_transcript_error: 'Error en exportar la transcripció',
    fmt_bold: 'Negreta', fmt_italic: 'Cursiva', fmt_h2: 'Títol', fmt_h3: 'Subtítol', fmt_text: 'Text normal', fmt_list: 'Llista',
    fmt_table: 'Taula', table_need_cursor: 'Posa el cursor dins d\'una taula',
    tbl_insert: 'Inserir taula', tbl_addrow: 'Afegir fila', tbl_delrow: 'Eliminar fila',
    tbl_addcol: 'Afegir columna', tbl_delcol: 'Eliminar columna', tbl_delete: 'Eliminar taula',
    save: 'Desar',
    cancel: 'Cancel·lar',
    settings_saved: 'Configuració desada',
    btn_delete_meeting: 'Eliminar reunió',
    btn_add_action: '+ Afegir acció',
    add_action_title_ph: 'Títol de l\'acció...',
    add_action_assignee_ph: 'Responsable (opcional)',
    add_action_deadline_ph: 'Data límit (opcional, YYYY-MM-DD)',
    add_action_save: 'Desar',
    add_action_cancel: 'Cancel·lar',
    send_email: 'Enviar per correu',
    restart_tour: 'Repetir tour de benvinguda',
    lang_pick_title: 'Tria el teu idioma',
    lang_pick_subtitle: 'Pots canviar-lo des dels Ajustos',
    tour_btn_next: 'Següent', tour_btn_prev: 'Anterior', tour_btn_done: 'Comença', tour_btn_skip: 'Saltar tour',
    tour_s1_title: '👋 Benvinguda a Noted', tour_s1_desc: 'En els propers passos t\'ensenyem tot el que pots fer. Hem carregat una reunió de demo perquè puguis veure-ho amb dades reals.',
    tour_s2_title: 'Noted viu a la safata', tour_s2_desc: 'La icona apareix a la safata del sistema (cantonada inferior dreta, pot estar amagada sota la fletxa ˄). Des d\'allà pots llançar una gravació manual, donar-li context abans de gravar, veure l\'estat actual o cancel·lar una gravació en curs.',
    tour_s3_title: 'Les teves notes, sempre aquí', tour_s3_desc: 'La secció principal on viuen totes les teves actes. Hi pots accedir també fent clic a la icona de la safata.',
    tour_s4_title: 'Cercar a totes les actes', tour_s4_desc: 'Cerca al contingut de totes les actes alhora. Drecera ràpida: Ctrl+F des de qualsevol vista.',
    tour_s5_title: 'Dies o Projectes', tour_s5_desc: 'Agrupa les teves reunions per data o per projecte segons el que necessitis en cada moment.',
    tour_s6_title: 'Les teves reunions', tour_s6_desc: 'Cada reunió gravada apareix aquí. Fes clic per obrir-la. Clic dret per fixar-la a dalt o eliminar-la.',
    tour_s7_title: 'Importar transcripció', tour_s7_desc: 'Tens una transcripció d\'un client o company? Importa-la directament i Noted generarà les actes igual que si l\'hagués gravat ell.',
    tour_s8_title: 'Títol editable', tour_s8_desc: 'El títol es genera automàticament a partir del contingut. Fes clic per editar-lo quan vulguis.',
    tour_s9_title: 'Projecte detectat', tour_s9_desc: 'Noted intenta detectar automàticament a quin projecte pertany cada reunió. Si s\'ha equivocat, canvia-ho aquí.',
    tour_s10_title: 'Actes generades per Claude', tour_s10_desc: 'El cos inclou un resum del que s\'ha parlat, les decisions preses i les tasques detectades i assignades automàticament.',
    tour_s11_title: 'Regenerar actes', tour_s11_desc: 'Si el resultat no et convenç, pots donar-li context addicional — què corregir, quin to usar, què afegir — i Claude regenera les actes ajustades al que necessites.',
    tour_s12_title: 'Nota adhesiva', tour_s12_desc: 'Crea un post-it flotant sobre les actes. Pots arrossegar-lo, minimitzar-lo o eliminar-lo — és el teu espai per anotar el que vulguis sense tocar el contingut generat.',
    tour_s13_title: 'Parlar amb Claude', tour_s13_desc: 'Obre les actes directament a Claude per fer preguntes, aprofundir en un tema o redactar un email de seguiment personalitzat.',
    tour_s14_title: 'Compartir i exportar', tour_s14_desc: 'Envia les actes per correu als assistents, copia el contingut al porta-retalls, exporta-les com a PDF o HTML. Tot en una barra d\'accions ràpides.',
    tour_s15_title: 'Moure al panell d\'accions', tour_s15_desc: 'Cada acció detectada a les actes té aquest botó. Usa\'l per afegir-la al panell d\'accions i fer-ne un seguiment independent vinculat a un projecte.',
    tour_s16_title: 'Panell d\'accions', tour_s16_desc: 'Aquí tens totes les tasques pendents de totes les teves reunions en un sol lloc, sense haver d\'obrir cada acta.',
    tour_s17_title: 'Kanban o llista', tour_s17_desc: 'Visualitza les accions com a tauler Kanban o com a llista segons la teva preferència.',
    tour_s18_title: 'Filtres', tour_s18_desc: 'Filtra les accions per projecte, persona assignada o estat per centrar-te en el que importa.',
    tour_s19_title: 'Detall de tasca', tour_s19_desc: 'Fes clic en qualsevol tasca per veure el detall complet: de quina reunió va sortir, qui la té assignada i quin és el seu estat actual.',
    tour_s20_title: 'Projectes', tour_s20_desc: 'Defineix els teus projectes per classificar les teves notes i accions. Per cada projecte pots configurar nom, descripció, stakeholders per correu, carpeta de treball i color.',
    tour_s21_title: 'Eliminats recentment', tour_s21_desc: 'Les reunions eliminades es guarden aquí durant 30 dies. Les pots recuperar abans que desapareguin definitivament.',
    tour_s22_title: 'El teu nom', tour_s22_desc: 'Posa el teu nom perquè Noted sàpiga quines accions són les teves quan filtres per assignat.',
    tour_s23_title: 'Idioma i tema', tour_s23_desc: 'Tria l\'idioma de la interfície i el tema clar o fosc. S\'aplica a l\'instant.',
    tour_s24_title: 'Model de transcripció', tour_s24_desc: 'Controla la precisió de la transcripció. Medium és l\'equilibri recomanat entre velocitat i qualitat.',
    tour_s25_title: '🎯 Coach de reunions', tour_s25_desc: 'Activa el Coach de reunions per rebre consells concrets de comunicació després de cada trucada. Només analitza el que tu vas dir.',
    tour_s26_title: 'Avís al xat de Teams', tour_s26_desc: 'Configura el missatge que Noted envia automàticament al xat de Teams quan comença a gravar, per avisar els participants.',
    tour_s27_title: '🎉 Ja ho saps tot!', tour_s27_desc: 'Llest. Entra a la teva propera reunió de Teams i Noted farà la resta. El popup apareix quan detecta la trucada — tens 30 segons per acceptar.',
  },
};

function t(key, ...args) {
  const v = (T[currentLang] ?? T.es)[key];
  return typeof v === 'function' ? v(...args) : (v ?? key);
}

function applyLang(lang) {
  currentLang = lang;
  window.__lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const v = t(el.dataset.i18n);
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') el.placeholder = v;
    else el.textContent = v;
  });
  const si = document.getElementById('search-input');
  if (si) si.placeholder = t('search_ph');
  const trashBtn = document.getElementById('btn-trash');
  if (trashBtn) trashBtn.title = t('nav_trash');
  const refreshBtn = document.getElementById('btn-refresh-meetings');
  if (refreshBtn) refreshBtn.title = t('refresh_meetings');
  renderWhisperOptions();
  if (!document.getElementById('view-projects').classList.contains('hidden')) {
    loadProjectsSettings();
  }
}

function showLangPicker(onPick) {
  const overlay = document.getElementById('lang-picker-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');
  overlay.querySelectorAll('.lang-pick-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const lang = btn.dataset.lang;
      applyLang(lang);
      try { await pywebview.api.save_settings({ language: lang }); } catch (_) {}
      overlay.classList.add('hidden');
      if (typeof onPick === 'function') setTimeout(onPick, 300);
    }, { once: true });
  });
}

// Mapas de rutas por índice para evitar rutas en atributos HTML
const meetingPaths  = {};   // idx -> path
const actionPaths   = {};   // i -> {path, index}

// ── Bootstrap ────────────────────────────────────────────────────────────────

window.addEventListener('pywebviewready', async () => {
  await initSettings();
  initResize();
  _initMeetingContextMenu();
  allProjects = await pywebview.api.get_projects();
  await loadMeetings();
  await refreshPendingBadge();

  // Onboarding tour: primera vez pedir idioma, luego mostrar tour
  try {
    const s = await pywebview.api.get_settings();
    if (!s.onboarding_completed) {
      if (!s.language) {
        setTimeout(() => showLangPicker(() => window.startTour()), 500);
      } else {
        setTimeout(() => window.startTour(), 500);
      }
    }
  } catch (_) {}

  // Trozo 3: detectar acciones completadas desde terminal externo
  setInterval(async () => {
    try {
      const completions = await pywebview.api.get_terminal_completions();
      for (const c of completions) {
        showToast(`${c.title ? c.title.slice(0, 60) : 'Action'} — ${t('done_from_terminal')}`);
        // Skip panel rebuild if confirmRegen is running for this meeting
        if (c.path && c.path === currentPath && _regenPath !== currentPath) openMeeting(currentPath);
      }
    } catch (_) {}
  }, 3000);

  // Pipeline status footer
  setInterval(updatePipelineFooter, 2000);

  // Teclado: rename modal
  document.getElementById('rename-modal-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); confirmRenameModal(); }
    if (e.key === 'Escape') { e.preventDefault(); closeRenameModal(); }
  });

  // Navegación externa (evita segunda ventana cuando hay notas nuevas)
  setInterval(async () => {
    try {
      const p = await pywebview.api.get_navigate_request();
      if (p && _regenPath !== p) {
        // Red de seguridad: si la minuta no está en la lista, es nueva y hay
        // que traerla antes de abrirla, o se mostraría sin figurar en el panel.
        if (!allMeetings.some(m => _samePath(m.path, p))) {
          try { await refreshMeetingList(); } catch (_) {}
        }
        openMeeting(p);
      }
    } catch (_) {}
  }, 2000);
});

async function loadMeetings() {
  const meetings = await pywebview.api.get_meetings();
  allMeetings = meetings;
  meetingPaths.length = 0;
  meetings.forEach((m, i) => { meetingPaths[i] = m.path; });
  renderSidebar(meetings);
  if (meetings.length > 0) openMeeting(meetings[0].path);
}

async function refreshMeetingList() {
  const meetings = await pywebview.api.get_meetings();
  allMeetings = meetings;
  meetingPaths.length = 0;
  meetings.forEach((m, i) => { meetingPaths[i] = m.path; });
  renderSidebar(meetings);
  // If the currently open meeting no longer exists, clear the right panel
  if (currentPath && !meetings.some(m => _samePath(m.path, currentPath))) {
    currentPath = null;
    document.getElementById('main-panel').innerHTML = '';
  }
}

async function refreshMeetings() {
  const btn = document.getElementById('btn-refresh-meetings');
  if (btn) btn.classList.add('spinning');
  await refreshMeetingList();
  if (btn) btn.classList.remove('spinning');
}

function _samePath(a, b) {
  return a && b && a.replace(/\\/g, '/').toLowerCase() === b.replace(/\\/g, '/').toLowerCase();
}

// ── Vistas ────────────────────────────────────────────────────────────────────

function showView(view) {
  document.getElementById('view-meetings').classList.toggle('hidden', view !== 'meetings');
  document.getElementById('view-actions').classList.toggle('hidden', view !== 'actions');
  document.getElementById('view-projects').classList.toggle('hidden', view !== 'projects');
  document.getElementById('view-trash').classList.toggle('hidden', view !== 'trash');
  document.getElementById('view-settings').classList.toggle('hidden', view !== 'settings');
  document.getElementById('btn-meetings').classList.toggle('active', view === 'meetings');
  document.getElementById('btn-actions').classList.toggle('active', view === 'actions');
  document.getElementById('btn-projects').classList.toggle('active', view === 'projects');
  document.getElementById('btn-trash').classList.toggle('active', view === 'trash');
  document.getElementById('btn-settings').classList.toggle('active', view === 'settings');
  if (view === 'actions') loadTaskBoard();
  if (view === 'projects') loadProjectsSettings();
  if (view === 'trash') loadTrash();
  if (view === 'settings') loadRecordingSettings();
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

function setSidebarMode(mode) {
  currentSidebarMode = mode;
  const btnDays = document.getElementById('mode-btn-days');
  const btnProj = document.getElementById('mode-btn-projects');
  if (btnDays) btnDays.classList.toggle('active', mode === 'days');
  if (btnProj) btnProj.classList.toggle('active', mode === 'projects');
  renderSidebar(allMeetings);
}

function renderSidebar(meetings) {
  if (currentSidebarMode === 'projects') {
    renderSidebarByProject(meetings);
  } else {
    renderSidebarByDays(meetings);
  }
}

function _meetingItemHtml(m) {
  return `
    <div class="meeting-item${m.pinned ? ' pinned' : ''}" data-midx="${m.idx}">
      <div class="meeting-time">${m.time || ''}</div>
      <div class="meeting-info">
        <div class="meeting-title">${escHtml(m.title)}</div>
      </div>
    </div>`;
}

function _wireSidebarItems(list) {
  list.querySelectorAll('.meeting-item').forEach(el => {
    el.addEventListener('click', e => {
      const path = meetingPaths[parseInt(el.dataset.midx)];
      if (path) openMeeting(path);
    });
    el.addEventListener('contextmenu', e => {
      const idx = parseInt(el.dataset.midx);
      const path = meetingPaths[idx];
      const title = allMeetings[idx]?.title || '';
      if (path) _showMeetingContextMenu(e, path, title, el);
    });
  });
}

function renderSidebarByDays(meetings) {
  const list = document.getElementById('meetings-list');
  if (!meetings.length) {
    list.innerHTML = `<div class="loading">${t('no_meetings')}</div>`;
    return;
  }

  const withIdx = meetings.map((m, i) => ({ ...m, idx: allMeetings.indexOf(m) !== -1 ? allMeetings.indexOf(m) : i }));
  const pinned  = withIdx.filter(m => m.pinned);
  const rest    = withIdx.filter(m => !m.pinned);

  const groups = {};
  rest.forEach(m => {
    const label = dayLabel(m.date);
    if (!groups[label]) groups[label] = [];
    groups[label].push(m);
  });

  let html = '';
  if (pinned.length) {
    html += `<div class="day-group">
      <div class="day-label pinned-label">${t('pinned')}</div>
      ${pinned.map(_meetingItemHtml).join('')}
    </div>`;
  }
  html += Object.entries(groups).map(([label, items]) => `
    <div class="day-group">
      <div class="day-label">${label}</div>
      ${items.map(_meetingItemHtml).join('')}
    </div>
  `).join('');

  list.innerHTML = html;
  _wireSidebarItems(list);
}

function renderSidebarByProject(meetings) {
  const list = document.getElementById('meetings-list');
  if (!meetings.length) {
    list.innerHTML = `<div class="loading">${t('no_meetings')}</div>`;
    return;
  }

  const projNames = {};
  const projMap = {};
  allProjects.forEach(p => { projNames[p.id] = p.name; projMap[p.id] = p; });

  const groups = {};
  meetings.forEach((m, idx) => {
    const pid = m.project_id || 'none';
    if (!groups[pid]) groups[pid] = {
      name: pid === 'none' ? t('no_project_label') : (projNames[pid] || pid),
      items: [],
    };
    groups[pid].items.push({ ...m, idx });
  });

  const entries = Object.entries(groups).sort(([a], [b]) => {
    if (a === 'none') return 1;
    if (b === 'none') return -1;
    return (projNames[a] || a).localeCompare(projNames[b] || b);
  });

  list.innerHTML = entries.map(([pid, grp]) => {
    const color = _projectColor(projMap[pid] || { id: pid });
    return `
    <div class="project-sidebar-group">
      <div class="project-sidebar-header" style="border-left:2px solid ${color}" onclick="toggleProjectSidebarGroup('${escHtml(pid)}')">
        <span class="project-sidebar-name" style="color:${color}">${escHtml(grp.name)}</span>
        <span class="project-sidebar-chevron" id="pchev-${escHtml(pid)}" style="color:${color}">▾</span>
      </div>
      <div class="project-sidebar-items" id="pgroup-${escHtml(pid)}">
        ${grp.items.map(m => `
          <div class="meeting-item" data-midx="${m.idx}">
            <div class="meeting-time">${m.time || ''}</div>
            <div class="meeting-info">
              <div class="meeting-title">${escHtml(m.title)}</div>
              <div class="meeting-meta">${m.date || ''}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>`;
  }).join('');

  list.querySelectorAll('.meeting-item').forEach(el => {
    el.addEventListener('click', e => {
      const path = meetingPaths[parseInt(el.dataset.midx)];
      if (path) openMeeting(path);
    });
    el.addEventListener('contextmenu', e => {
      const idx = parseInt(el.dataset.midx);
      const path = meetingPaths[idx];
      const title = allMeetings[idx]?.title || '';
      if (path) _showMeetingContextMenu(e, path, title, el);
    });
  });
}

// ── Papelera (reuniones eliminadas) ──────────────────────────────────────────

async function loadTrash() {
  const list = document.getElementById('trash-list');
  if (!list) return;
  list.innerHTML = `<div class="loading">${t('loading')}</div>`;
  let items = [];
  try { items = await pywebview.api.list_trash(); } catch (_) {}
  if (!items || !items.length) {
    list.innerHTML = `<div style="color:var(--muted);font-size:13px">${t('trash_empty')}</div>`;
    return;
  }
  list.innerHTML = items.map(it => {
    const id = escHtml(it.id);
    const title = escHtml(it.title || it.stem || '');
    const when = (it.deleted_at || '').slice(0, 10);
    return `
    <div class="trash-item" data-trash-id="${id}">
      <div class="trash-item-info">
        <div class="trash-item-title">${title}</div>
        <div class="trash-item-meta">${escHtml(it.date || '')} · ${t('trash_deleted_at')} ${escHtml(when)} · ${it.file_count || 0} ${t('trash_files')}</div>
      </div>
      <div class="trash-item-actions">
        <button class="btn btn-primary btn-sm" onclick="recoverMeeting('${id}')">${t('trash_recover')}</button>
        <button class="btn btn-delete btn-sm" onclick="purgeTrashMeeting('${id}', '${title}')">${t('trash_delete_forever')}</button>
      </div>
    </div>`;
  }).join('');
}

async function recoverMeeting(id) {
  const ok = await pywebview.api.recover_meeting(id);
  if (ok) {
    showToast(t('trash_recovered'));
    await loadTrash();
    try { if (typeof refreshMeetingList === 'function') await refreshMeetingList(); } catch (_) {}
  }
}

function purgeTrashMeeting(id, title) {
  openConfirmModal(
    t('trash_purge_confirm', title || ''),
    async () => {
      const ok = await pywebview.api.purge_trash_meeting(id);
      if (ok) { showToast(t('trash_purged')); await loadTrash(); }
    },
    { title: t('trash_delete_forever'), okLabel: t('btn_delete') }
  );
}

function toggleProjectSidebarGroup(pid) {
  const items = document.getElementById('pgroup-' + pid);
  const chev  = document.getElementById('pchev-' + pid);
  if (!items) return;
  items.classList.toggle('collapsed');
  if (chev) chev.textContent = items.classList.contains('collapsed') ? '▸' : '▾';
}

// ── Meeting context menu (right-click: rename / delete) ──────────────────────

let _ctxPath  = null;
let _ctxTitle = null;
let _ctxEl    = null;

function _initMeetingContextMenu() {
  const menu   = document.getElementById('meeting-ctx-menu');
  const delBtn = document.getElementById('ctx-delete-btn');
  const pinBtn = document.getElementById('ctx-pin-btn');
  if (!menu) return;

  delBtn.onclick = () => { menu.classList.add('hidden'); _confirmDeleteMeeting(_ctxPath, _ctxTitle, _ctxEl); };
  pinBtn.onclick = async () => {
    menu.classList.add('hidden');
    if (!_ctxPath) return;
    await pywebview.api.toggle_pin(_ctxPath);
    await refreshMeetingList();
  };

  document.addEventListener('click', () => menu.classList.add('hidden'));
  document.addEventListener('contextmenu', e => {
    if (!e.target.closest('.meeting-item')) menu.classList.add('hidden');
  });

// Cerrar el menú de tabla del editor al hacer clic fuera
document.addEventListener('click', (e) => {
  const menu = document.getElementById('table-menu');
  if (menu && !menu.classList.contains('hidden')) {
    if (!e.target.closest || !e.target.closest('.notes-tool-dropdown')) {
      menu.classList.add('hidden');
    }
  }
});

// ── Global keyboard shortcuts ─────────────────────────────────────────────────
document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toUpperCase();
  const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || e.target.isContentEditable;

  // Ctrl+F → focus search (always, even from input)
  if (e.ctrlKey && e.key === 'f') {
    const search = document.getElementById('search-input');
    if (search) { e.preventDefault(); showView('meetings'); search.focus(); search.select(); }
    return;
  }

  if (inInput) return; // don't intercept nav shortcuts while typing

  // Ctrl+Shift+R → hard reload (picks up new JS/CSS)
  if (e.ctrlKey && e.shiftKey && e.key === 'R') {
    e.preventDefault();
    window.location.reload();
    return;
  }

  // F5 / Ctrl+R → refresh meeting list + re-open active meeting
  if (e.key === 'F5' || (e.ctrlKey && e.key === 'r')) {
    e.preventDefault();
    (async () => {
      await refreshMeetings();
      if (currentPath) await openMeeting(currentPath);
    })();
    return;
  }

  // Ctrl+1-5 → nav views
  if (e.ctrlKey && !e.shiftKey && !e.altKey) {
    const navMap = { '1': 'meetings', '2': 'actions', '3': 'projects', '4': 'trash', '5': 'settings' };
    if (navMap[e.key]) { e.preventDefault(); showView(navMap[e.key]); return; }
  }
});
}

function _showMeetingContextMenu(e, path, title, el) {
  e.preventDefault();
  e.stopPropagation();
  _ctxPath  = path;
  _ctxTitle = title;
  _ctxEl    = el;
  const isPinned = el && el.classList.contains('pinned');
  const pinLabel = document.getElementById('ctx-pin-label');
  if (pinLabel) pinLabel.textContent = isPinned ? t('unpin') : t('pin');
  const menu = document.getElementById('meeting-ctx-menu');
  menu.classList.remove('hidden');
  const x = Math.min(e.clientX, window.innerWidth  - 175);
  const y = Math.min(e.clientY, window.innerHeight - 90);
  menu.style.left = x + 'px';
  menu.style.top  = y + 'px';
}


function _confirmDeleteMeeting(pathToDelete, titleToDelete, elToRemove) {
  if (!pathToDelete) return;

  openConfirmModal(
    t('confirm_delete_meeting', titleToDelete || ''),
    async () => {
      const ok = await pywebview.api.delete_meeting(pathToDelete);
      if (ok) {
        showToast(t('toast_meeting_deleted'));

        // Clear right panel immediately if this meeting is open
        if (_samePath(currentPath, pathToDelete)) {
          currentPath = null;
          document.getElementById('main-panel').innerHTML = '';
        }

        // Remove sidebar item immediately for instant feedback
        if (elToRemove && elToRemove.isConnected) {
          const dayGroup  = elToRemove.closest('.day-group');
          const projGroup = elToRemove.closest('.project-sidebar-group');
          elToRemove.remove();
          if (dayGroup  && !dayGroup.querySelector('.meeting-item'))  dayGroup.remove();
          if (projGroup && !projGroup.querySelector('.meeting-item')) projGroup.remove();
        }

        // Full refresh — also clears right panel if currentPath is gone
        await refreshMeetingList();
      }
    },
    { title: t('confirm_delete_meeting_title'), okLabel: t('btn_delete') }
  );
}

function dayLabel(dateStr) {
  if (!dateStr) return t('no_date');
  const [y, mo, d] = dateStr.split('-').map(Number);
  const date  = new Date(y, mo - 1, d);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff  = Math.round((today - date) / 86400000);
  const locale = currentLang === 'es' ? 'es-ES' : 'en-US';
  if (diff === 0) return t('today');
  if (diff === 1) return t('yesterday');
  if (diff < 7)   return date.toLocaleDateString(locale, { weekday: 'long' });
  return date.toLocaleDateString(locale, { day: 'numeric', month: 'long' });
}

// ── Apertura de reunión ───────────────────────────────────────────────────────

function _updateActionBar(tab) {
  const show = (id, visible) => { const el = document.getElementById(id); if (el) el.style.display = visible ? '' : 'none'; };

  // Edit: only on notes tab (transcript = read-only, actions = per-card edit)
  const editBtn = document.getElementById('btn-edit-notes');
  if (editBtn) {
    editBtn.style.display = tab === 'notes' ? '' : 'none';
    editBtn.title = t('btn_edit');
  }

  // Copy + Email: always visible, titles change
  const copyBtn = document.getElementById('btn-copy');
  const mailBtn = document.getElementById('btn-email');
  if (copyBtn) copyBtn.title = tab === 'actions' ? t('copy_actions') : tab === 'transcript' ? t('copy_transcript') : t('copy_note');
  if (mailBtn) mailBtn.title = tab === 'actions' ? t('email_actions') : tab === 'transcript' ? t('email_transcript') : t('send_email');

  // Export transcript + Semantic Layer: visible in main bar only on transcript tab
  show('btn-export-transcript', tab === 'transcript');
  show('btn-semantic-bar',      tab === 'transcript');

  // Sticky: in dropdown (notes), in main bar (actions), hidden (transcript)
  show('btn-sticky',     tab === 'notes');      // dropdown item
  show('btn-sticky-bar', tab === 'actions');    // direct bar button

  // Dropdown items (notes only)
  show('btn-regenerate', tab !== 'transcript');
  show('btn-html',       tab === 'notes');
  show('btn-pdf',        tab === 'notes');

  // ··· only on notes (has sticky + regenerate + html); actions and transcript have nothing there
  show('btn-more', tab === 'notes');

  show('btn-claude', tab !== 'transcript');

  // Stickies only visible on notes tab
  const stickyLayer = document.getElementById('sticky-layer');
  if (stickyLayer) stickyLayer.style.display = tab === 'notes' ? '' : 'none';
}

async function openMeeting(path) {
  currentPath = path;
  _regenVisible = false;

  document.querySelectorAll('.meeting-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.meeting-item').forEach(el => {
    const idx = parseInt(el.dataset.midx);
    if (meetingPaths[idx] === path) el.classList.add('active');
  });

  const panel = document.getElementById('main-panel');
  panel.innerHTML = `<div class="loading">${t('loading')}</div>`;

  const [minutesHtml, actions, transcriptText, semanticInfo] = await Promise.all([
    pywebview.api.get_minutes_html(path),
    pywebview.api.get_actions(path),
    pywebview.api.get_transcript_text(path).catch(() => null),
    pywebview.api.get_semantic_info(path).catch(() => ({})),
  ]);
  const hasSemantic = !!(semanticInfo && semanticInfo.generated_at);

  const meeting = allMeetings.find(m => m.path === path) || {};
  const pendingCount = actions ? actions.filter(a => !a.executed).length : 0;
  const curProjId = meeting.project_id || '';
  const projOptions = [
    `<option value="">${t('no_project_label')}</option>`,
    ...allProjects.map(p =>
      `<option value="${escHtml(p.id)}"${curProjId === p.id ? ' selected' : ''}>${escHtml(p.name)}</option>`
    ),
  ].join('');

  panel.innerHTML = `
    <div class="meeting-detail">
      <div class="detail-header">
        <div>
          <div class="detail-title-row">
            <span class="detail-title" id="detail-title-text">${escHtml(meeting.title || t('empty_title'))}</span>
            <button class="btn-rename-meeting" id="btn-rename-meeting" title="${t('btn_edit')}"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
          </div>
          <div class="detail-meta">
            <span>${meeting.date || ''} ${meeting.time || ''}</span>
            <span class="detail-project-wrap">
              <label class="detail-project-label">${t('project_label')}</label>
              <select class="detail-project-select" id="meeting-project-select" onchange="onMeetingProjectChange(this.value)">${projOptions}</select>
            </span>
          </div>
        </div>
        <div class="detail-actions-bar">
          <button class="action-icon-btn action-icon-btn--accent" id="btn-claude" title="Claude"><svg width="16" height="16" viewBox="0 0 248 248" fill="currentColor"><path d="M52.4285 162.873L98.7844 136.879L99.5485 134.602L98.7844 133.334H96.4921L88.7237 132.862L62.2346 132.153L39.3113 131.207L17.0249 130.026L11.4214 128.844L6.2 121.873L6.7094 118.447L11.4214 115.257L18.171 115.847L33.0711 116.911L55.485 118.447L71.6586 119.392L95.728 121.873H99.5485L100.058 120.337L98.7844 119.392L97.7656 118.447L74.5877 102.732L49.4995 86.1905L36.3823 76.62L29.3779 71.7757L25.8121 67.2858L24.2839 57.3608L30.6515 50.2716L39.3113 50.8623L41.4763 51.4531L50.2636 58.1879L68.9842 72.7209L93.4357 90.6804L97.0015 93.6343L98.4374 92.6652L98.6571 91.9801L97.0015 89.2625L83.757 65.2772L69.621 40.8192L63.2534 30.6579L61.5978 24.632C60.9565 22.1032 60.579 20.0111 60.579 17.4246L67.8381 7.49965L71.9133 6.19995L81.7193 7.49965L85.7946 11.0443L91.9074 24.9865L101.714 46.8451L116.996 76.62L121.453 85.4816L123.873 93.6343L124.764 96.1155H126.292V94.6976L127.566 77.9197L129.858 57.3608L132.15 30.8942L132.915 23.4505L136.608 14.4708L143.994 9.62643L149.725 12.344L154.437 19.0788L153.8 23.4505L150.998 41.6463L145.522 70.1215L141.957 89.2625H143.994L146.414 86.7813L156.093 74.0206L172.266 53.698L179.398 45.6635L187.803 36.802L193.152 32.5484H203.34L210.726 43.6549L207.415 55.1159L196.972 68.3492L188.312 79.5739L175.896 96.2095L168.191 109.585L168.882 110.689L170.738 110.53L198.755 104.504L213.91 101.787L231.994 98.7149L240.144 102.496L241.036 106.395L237.852 114.311L218.495 119.037L195.826 123.645L162.07 131.592L161.696 131.893L162.137 132.547L177.36 133.925L183.855 134.279H199.774L229.447 136.524L237.215 141.605L241.8 147.867L241.036 152.711L229.065 158.737L213.019 154.956L175.45 145.977L162.587 142.787H160.805V143.85L171.502 154.366L191.242 172.089L215.82 195.011L217.094 200.682L213.91 205.172L210.599 204.699L188.949 188.394L180.544 181.069L161.696 165.118H160.422V166.772L164.752 173.152L187.803 207.771L188.949 218.405L187.294 221.832L181.308 223.959L174.813 222.777L161.187 203.754L147.305 182.486L136.098 163.345L134.745 164.2L128.075 235.42L125.019 239.082L117.887 241.8L111.902 237.31L108.718 229.984L111.902 215.452L115.722 196.547L118.779 181.541L121.58 162.873L123.291 156.636L123.14 156.219L121.773 156.449L107.699 175.752L86.304 204.699L69.3663 222.777L65.291 224.431L58.2867 220.768L58.9235 214.27L62.8713 208.48L86.304 178.705L100.44 160.155L109.551 149.507L109.462 147.967L108.959 147.924L46.6977 188.512L35.6182 189.93L30.7788 185.44L31.4156 178.115L33.7079 175.752L52.4285 162.873Z"/></svg></button>
          <button class="action-icon-btn" id="btn-edit-notes" title="${t('btn_edit')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
          <button class="action-icon-btn" id="btn-copy" title="${t('copy_note')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
          <button class="action-icon-btn" id="btn-email" title="${t('send_email')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg></button>
          <button class="action-icon-btn" id="btn-export-transcript" title="${t('export_transcript_btn')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>
          <button class="action-icon-btn" id="btn-semantic-bar" title="${t('semantic_layer')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg></button>
          <button class="action-icon-btn" id="btn-sticky-bar" title="${t('sticky_add')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M15 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h9l7-7V5a2 2 0 0 0-2-2z"/><path d="M14 21v-6a1 1 0 0 1 1-1h6"/></svg></button>
          <button class="action-icon-btn" id="btn-regenerate" title="${t('btn_regenerate')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74"/><path d="M3 3v4h4"/></svg></button>
          <div class="action-more-wrap">
            <button class="action-icon-btn" id="btn-more" title="${t('more_actions')}"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg></button>
            <div class="action-menu hidden" id="action-menu">
              <button class="action-menu-item" id="btn-sticky"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M15 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h9l7-7V5a2 2 0 0 0-2-2z"/><path d="M14 21v-6a1 1 0 0 1 1-1h6"/></svg><span>${t('sticky_add')}</span></button>
              <button class="action-menu-item" id="btn-html"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg><span>HTML</span></button>
              <button class="action-menu-item" id="btn-pdf"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h4"/></svg><span>${t('pdf_download')}</span></button>
              <button class="action-menu-item" id="btn-semantic"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg><span>${t('semantic_layer')}</span></button>
            </div>
          </div>
        </div>
      </div>
      <div class="regen-bar hidden" id="regen-bar">
        <textarea id="regen-textarea" rows="2" placeholder="${t('regen_placeholder')}"></textarea>
        <div class="regen-bar-btns">
          <button class="btn btn-primary btn-sm" id="btn-regen-confirm">${t('regen_confirm')}</button>
          <button class="btn btn-ghost btn-sm" id="btn-regen-cancel">${t('regen_cancel')}</button>
        </div>
      </div>
      <div class="detail-tabs">
        <button class="detail-tab active" id="tab-notes" data-tab="notes">${t('tab_notes')}</button>
        <button class="detail-tab" id="tab-actions" data-tab="actions">
          ${t('tab_actions')} ${pendingCount > 0 ? `<span class="tab-badge">${pendingCount}</span>` : ''}
        </button>
        <button class="detail-tab" id="tab-transcript" data-tab="transcript">${t('tab_transcript')}</button>
        ${hasSemantic ? `<button class="detail-tab" id="tab-semantic" data-tab="semantic">${t('tab_semantic')}</button>` : ''}
      </div>
      <div class="minutes-section" id="section-notes">
        <div class="minutes-content">${minutesHtml ? sanitizeHtml(minutesHtml) : `<em>${t('no_minutes')}</em>`}</div>
      </div>
      <div class="actions-section hidden" id="section-actions">
        <div class="actions-section-header">
          <div class="section-label">${t('section_actions')}</div>
          <div class="actions-count" style="margin-left:auto">${t('n_total', actions ? actions.length : 0)}</div>
          <button class="btn btn-ghost btn-sm" id="btn-add-action" style="margin-left:8px">+ ${t('add_action')}</button>
        </div>
        <div id="add-action-form" class="add-action-form" style="display:none"></div>
        <div id="regen-actions-form" class="add-action-form" style="display:none"></div>
        <div id="meeting-actions"></div>
      </div>
      <div class="transcript-section hidden" id="section-transcript">
        <div class="transcript-content">${transcriptText ? escHtml(transcriptText) : `<em style="color:var(--muted)">${t('no_minutes')}</em>`}</div>
      </div>
      ${hasSemantic ? `<div class="semantic-section hidden" id="section-semantic" data-path="${escHtml(path)}"></div>` : ''}
      <div id="sticky-layer" class="sticky-layer"></div>
    </div>`;

  document.getElementById('btn-html').addEventListener('click', () => openHtml(path));
  document.getElementById('btn-pdf').addEventListener('click', async () => {
    document.getElementById('action-menu').classList.add('hidden');
    if (!currentPath) return;
    const r = await pywebview.api.download_pdf(currentPath);
    if (!r.ok) alert('PDF: ' + r.error);
  });
  // Semantic Layer: si ya está generada, salta a su pestaña; si no, abre el diálogo
  // para elegir documentos extra antes de generar.
  const _semanticEntry = () => {
    document.getElementById('action-menu').classList.add('hidden');
    if (hasSemantic) document.getElementById('tab-semantic')?.click();
    else openSemanticDialog(path, meeting.title || '');
  };
  document.getElementById('btn-semantic').addEventListener('click', _semanticEntry);
  document.getElementById('btn-semantic-bar')?.addEventListener('click', _semanticEntry);
  document.getElementById('btn-export-transcript').addEventListener('click', () => exportTranscript(path));
  document.getElementById('btn-claude').addEventListener('click', () => openMinutesInClaude(path));
  document.getElementById('btn-regenerate').addEventListener('click', () => toggleRegenBar());
  document.getElementById('btn-regen-cancel').addEventListener('click', () => toggleRegenBar(false));
  document.getElementById('btn-regen-confirm').addEventListener('click', () => confirmRegen(path));
  document.getElementById('btn-edit-notes').addEventListener('click', () => {
    const activeTab = document.querySelector('.detail-tab.active')?.dataset.tab || 'notes';
    if (activeTab === 'actions') reenrichMeeting(path);
    else toggleEditNotes(path);
  });

  // More dropdown toggle
  document.getElementById('btn-more').addEventListener('click', (e) => {
    e.stopPropagation();
    document.getElementById('action-menu').classList.toggle('hidden');
  });
  document.addEventListener('click', () => document.getElementById('action-menu')?.classList.add('hidden'));

  // Context-aware copy
  document.getElementById('btn-copy').addEventListener('click', async () => {
    const activeTab = document.querySelector('.detail-tab.active')?.dataset.tab || 'notes';
    if (activeTab === 'actions') {
      await _copyActionsTable(path);
    } else if (activeTab === 'transcript') {
      _copyRich('', transcriptText || '');
    } else {
      const d = document.querySelector('.minutes-content');
      _copyRich(d ? _inlineStyles(d.innerHTML) : '', d ? d.innerText : '');
    }
  });

  // Context-aware email
  document.getElementById('btn-email').addEventListener('click', () => {
    const activeTab = document.querySelector('.detail-tab.active')?.dataset.tab || 'notes';
    if (activeTab === 'actions') sendActionsEmail(path);
    else if (activeTab === 'transcript') sendTranscriptEmail(path);
    else sendEmail(path);
  });

  document.getElementById('btn-sticky').addEventListener('click', () => { document.getElementById('action-menu').classList.add('hidden'); addSticky(); });
  document.getElementById('btn-sticky-bar').addEventListener('click', () => addSticky());

  // Load stickies for this meeting
  _loadStickies(path);

  requestAnimationFrame(() => {
    const header = panel.querySelector('.detail-header');
    const tabs   = panel.querySelector('.detail-tabs');
    if (header && tabs) tabs.style.top = header.offsetHeight + 'px';
  });

  _updateActionBar('notes');

  // Tabs: sub-pantallas exclusivas
  document.querySelectorAll('.detail-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const which = tab.dataset.tab;
      document.getElementById('section-notes').classList.toggle('hidden', which !== 'notes');
      document.getElementById('section-actions').classList.toggle('hidden', which !== 'actions');
      document.getElementById('section-transcript').classList.toggle('hidden', which !== 'transcript');
      document.getElementById('section-semantic')?.classList.toggle('hidden', which !== 'semantic');
      if (which === 'semantic') renderSemanticTab(path);
      _updateActionBar(which);
      panel.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });

  document.getElementById('btn-add-action')?.addEventListener('click', () => toggleAddActionForm(path));
  document.getElementById('btn-regen-actions')?.addEventListener('click', () => toggleRegenActionsForm(path));

  document.getElementById('btn-rename-meeting')?.addEventListener('click', () => {
    const titleEl = document.getElementById('detail-title-text');
    const current = titleEl?.textContent || '';
    openRenameModal(current, async (next) => {
      if (next === current.trim()) return;
      const ok = await pywebview.api.rename_meeting(path, next);
      if (!ok) return;
      if (titleEl) titleEl.textContent = next;
      const m = allMeetings.find(x => x.path === path);
      if (m) {
        m.title = next.trim();
        renderSidebar();
      }
    });
  });

  const actionsDiv = document.getElementById('meeting-actions');
  if (actions && actions.length) {
    renderActionCards(actions, path, actionsDiv, meeting.date || '');
    _prefillWorkingDirs(actions, path);
  } else {
    actionsDiv.innerHTML = `<div style="color:var(--muted);font-size:13px;margin-bottom:10px">${t('no_actions')}</div>`;
  }
}

// ── Añadir acción manual a una reunión ────────────────────────────────────────

function toggleAddActionForm(path) {
  const form = document.getElementById('add-action-form');
  if (!form) return;
  if (form.style.display !== 'none') { form.style.display = 'none'; form.innerHTML = ''; return; }
  form.style.display = 'block';
  form.innerHTML = `
    <input type="text" id="new-action-title" class="settings-text-input" placeholder="${t('action_title_ph')}">
    <div class="add-action-row">
      <input type="text" id="new-action-assignee" class="settings-text-input" placeholder="${t('action_assignee_ph')}">
      <input type="text" id="new-action-deadline" class="settings-text-input" placeholder="${t('action_deadline_ph')}">
    </div>
    <div class="add-action-btns">
      <button class="btn btn-primary btn-sm" id="new-action-save">${t('save_btn')}</button>
      <button class="btn btn-ghost btn-sm" id="new-action-cancel">${t('regen_cancel')}</button>
    </div>`;
  const titleEl = document.getElementById('new-action-title');
  titleEl.focus();
  const close = () => { form.style.display = 'none'; form.innerHTML = ''; };
  document.getElementById('new-action-cancel').onclick = close;
  const save = async () => {
    const title = titleEl.value.trim();
    if (!title) { titleEl.focus(); return; }
    const assignee = document.getElementById('new-action-assignee').value.trim();
    const deadline = document.getElementById('new-action-deadline').value.trim();
    const ok = await pywebview.api.add_meeting_action(path, title, deadline, assignee);
    if (ok) {
      close();
      await refreshMeetingActions(path);
      showToast(t('toast_action_added'));
    }
  };
  document.getElementById('new-action-save').onclick = save;
  titleEl.addEventListener('keydown', e => { if (e.key === 'Enter') save(); });
}

function toggleRegenActionsForm(path) {
  const form = document.getElementById('regen-actions-form');
  if (!form) return;
  if (form.style.display !== 'none') { form.style.display = 'none'; form.innerHTML = ''; return; }
  // Cerrar el de añadir si estaba abierto
  const addForm = document.getElementById('add-action-form');
  if (addForm) { addForm.style.display = 'none'; addForm.innerHTML = ''; }
  form.style.display = 'block';
  form.innerHTML = `
    <div class="notes-edit-hint">${t('regen_actions_hint')}</div>
    <textarea id="regen-actions-input" class="settings-text-input" rows="3" placeholder="${t('regen_actions_ph')}"></textarea>
    <div class="add-action-btns">
      <button class="btn btn-primary btn-sm" id="regen-actions-go">✦ ${t('regen_actions_confirm')}</button>
      <button class="btn btn-ghost btn-sm" id="regen-actions-cancel">${t('regen_cancel')}</button>
    </div>`;
  const input = document.getElementById('regen-actions-input');
  input.focus();
  const close = () => { form.style.display = 'none'; form.innerHTML = ''; };
  document.getElementById('regen-actions-cancel').onclick = close;
  document.getElementById('regen-actions-go').onclick = async () => {
    const instruction = input.value.trim();
    if (!instruction) { input.focus(); return; }
    const goBtn = document.getElementById('regen-actions-go');
    goBtn.disabled = true;
    goBtn.textContent = t('regen_actions_working');
    const ok = await pywebview.api.regenerate_actions(path, instruction);
    if (!ok) { showToast(t('regen_actions_error')); goBtn.disabled = false; goBtn.textContent = '✦ ' + t('regen_actions_confirm'); return; }
    // Poll hasta que termine
    const poll = setInterval(async () => {
      let st = { done: false };
      try { st = await pywebview.api.get_actions_regen_status(path); } catch (_) {}
      if (st && st.done) {
        clearInterval(poll);
        close();
        if (st.error) { showToast(t('regen_actions_error')); }
        else { showToast(t('regen_actions_done')); await refreshMeetingActions(path); }
      }
    }, 1500);
  };
}

async function refreshMeetingActions(path) {
  const actions = await pywebview.api.get_actions(path);
  const actionsDiv = document.getElementById('meeting-actions');
  if (!actionsDiv) return;
  const countEl = document.querySelector('#section-actions .actions-count');
  if (countEl) countEl.textContent = t('n_total', actions ? actions.length : 0);
  if (actions && actions.length) {
    renderActionCards(actions, path, actionsDiv, '');
    _prefillWorkingDirs(actions, path);
  } else {
    actionsDiv.innerHTML = `<div style="color:var(--muted);font-size:13px">${t('no_actions')}</div>`;
  }
  try { refreshPendingBadge(); } catch (_) {}
}

// ── Meta row compartida (Owner · Creada · Deadline · badge) ──────────────────

function actionMetaHtml(a, meetingDate, claudeExec) {
  if (claudeExec === undefined) {
    claudeExec = a.claude_executable ?? (a.type && a.type !== 'human');
  }
  const owner    = a.assignee || '—';
  const created  = a.created_at || meetingDate || '—';
  const dlHtml   = a.deadline
    ? `<span class="deadline-badge">${escHtml(a.deadline)}</span>`
    : '<span class="meta-value-empty">—</span>';
  return `<div class="action-meta">
    <span class="meta-label">${t('owner_label')}:</span><span class="action-assignee">${escHtml(owner)}</span>
    <span class="meta-sep">·</span>
    <span class="meta-label">${t('created_label')}:</span><span class="meta-value">${escHtml(created)}</span>
    <span class="meta-sep">·</span>
    <span class="meta-label">${t('deadline_label')}:</span>${dlHtml}
    <span class="${claudeExec ? 'auto-run-badge' : 'manual-badge'}">${claudeExec ? t('who_claude') : t('who_human')}</span>
  </div>`;
}

// ── Tarjetas de acciones (vista Notas) ────────────────────────────────────────

// Normaliza el responsable para agrupar: mismas personas → misma clave,
// sin importar el orden ni el marcador "(Claude)".
function _ownerKey(assignee) {
  const raw = (assignee || '').trim();
  if (!raw) return { key: '__none__', label: t('no_owner') };
  const cleaned = raw.replace(/\((?:claude|manual)\)/ig, '').replace(/\s+/g, ' ').trim();
  const parts = cleaned.split(/\s*[\/,&]\s*|\s+y\s+|\s+and\s+/i).map(p => p.trim()).filter(p => p && p !== '-');
  if (!parts.length) return { key: '__none__', label: t('no_owner') };
  const key = parts.map(p => p.toLowerCase()).sort().join('|');
  return { key, label: parts.join(' / ') };
}

function renderActionCards(actions, path, container, meetingDate) {
  const _card = (a) => {
    const prompt = a.prompt_enriched || a.prompt_original || '';
    const claudeExec = a.claude_executable || (a.type && a.type !== 'human' && prompt.trim().length > 0);
    const inPanel = a.in_panel === true;
    return `
    <div class="action-card ${a.executed ? 'done' : ''}" id="card-${a.index}">
      <div class="action-card-row">
        <div class="action-card-main">
          <span class="action-title ${a.executed ? 'done-text' : ''}">${escHtml(a.title)}</span>
          ${actionMetaHtml(a, meetingDate, claudeExec)}
        </div>
        <div class="action-card-btns">
          <button class="btn btn-ghost btn-sm${inPanel ? ' btn-in-panel' : ''}" data-panel="${a.index}" ${inPanel ? 'disabled' : ''}>
            ${inPanel ? t('btn_in_panel') : t('btn_move_panel')}
          </button>
          ${claudeExec
            ? `<button class="btn btn-ghost btn-sm" data-toggle="${a.index}">${t('btn_prompt_show')}</button>`
            : ''}
          <button class="btn btn-ghost btn-sm" data-edit="${a.index}" title="${t('btn_edit')}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
          <button class="btn btn-delete btn-sm" data-del="${a.index}" title="${t('btn_delete')}">×</button>
        </div>
      </div>
      <div class="action-edit-form hidden" id="edit-form-${a.index}">
        <input class="action-edit-input" id="edit-title-${a.index}" value="${escHtml(a.title)}" placeholder="${t('action_title_ph')}">
        <div class="action-edit-row">
          <input class="action-edit-input" id="edit-assignee-${a.index}" value="${escHtml(a.assignee||'')}" placeholder="${t('add_action_assignee_ph')}">
          <input class="action-edit-input" id="edit-deadline-${a.index}" value="${escHtml(a.deadline||'')}" placeholder="${t('action_deadline_ph')}" style="max-width:130px">
        </div>
        <div class="action-edit-btns">
          <button class="btn btn-primary btn-sm" data-save-edit="${a.index}">${t('save_btn')}</button>
          <button class="btn btn-ghost btn-sm" data-cancel-edit="${a.index}">${t('cancel_btn')}</button>
        </div>
      </div>
      ${claudeExec ? `
      <div class="action-card-body" id="body-${a.index}">
        ${a.type === 'document_change' ? `
        <div class="prompt-label">${t('doc_file_label')}</div>
        <div class="dir-row">
          <input type="text" class="dir-input" id="dir-${a.index}" placeholder="${escHtml(a.archivo || t('doc_file_ph'))}">
          <button class="btn btn-ghost btn-sm" data-browse-file="${a.index}">${t('doc_browse_file')}</button>
        </div>` : `
        <div class="prompt-label">${t('confirm_dir')}</div>
        <div class="dir-row">
          <input type="text" class="dir-input" id="dir-${a.index}" placeholder="${t('confirm_dir')}">
          <button class="btn btn-ghost btn-sm" data-browse="${a.index}">${t('confirm_browse')}</button>
        </div>`}
        <div class="dir-hint" id="dirhint-${a.index}"></div>
        <div class="prompt-label">${t('prompt_label')}</div>
        <textarea class="prompt-box" id="prompt-${a.index}">${escHtml(prompt)}</textarea>
        <div class="action-btn-row">
          <button class="btn btn-primary btn-sm" data-launch="${a.index}" ${a.executed ? 'disabled' : ''}>${t('btn_launch')}</button>
          <button class="btn btn-ghost btn-sm" data-toggleclose="${a.index}">${t('btn_launch_cancel')}</button>
        </div>
      </div>` : ''}
    </div>`;
  };

  // Agrupar por owner (responsable) normalizado: mismas personas = mismo grupo
  // (ignora el orden de los nombres y el marcador "(Claude)"), conservando el orden de aparición
  const groups = {};   // key -> { label, items }
  const order = [];
  actions.forEach(a => {
    const { key, label } = _ownerKey(a.assignee);
    if (!groups[key]) { groups[key] = { label, items: [] }; order.push(key); }
    groups[key].items.push(a);
  });
  container.innerHTML = order.map(key => `
    <div class="action-owner-eyebrow">${escHtml(groups[key].label)}</div>
    ${groups[key].items.map(_card).join('')}
  `).join('');

  container.querySelectorAll('[data-del]').forEach(el => {
    el.addEventListener('click', () => {
      const idx = parseInt(el.dataset.del);
      const card = document.getElementById('card-' + idx);
      const title = card?.querySelector('.action-title')?.textContent?.trim() || '';
      deleteAction(path, idx, title);
    });
  });

  container.querySelectorAll('[data-edit]').forEach(el => {
    el.addEventListener('click', () => {
      const idx = el.dataset.edit;
      document.getElementById(`edit-form-${idx}`)?.classList.toggle('hidden');
    });
  });

  container.querySelectorAll('[data-save-edit]').forEach(el => {
    el.addEventListener('click', async () => {
      const idx = parseInt(el.dataset.saveEdit);
      const title    = document.getElementById(`edit-title-${idx}`)?.value || '';
      const assignee = document.getElementById(`edit-assignee-${idx}`)?.value || '';
      const deadline = document.getElementById(`edit-deadline-${idx}`)?.value || '';
      const ok = await pywebview.api.update_action(path, idx, title, assignee, deadline);
      if (ok) {
        document.getElementById(`edit-form-${idx}`)?.classList.add('hidden');
        // Reload the actions list to reflect changes
        const actions = await pywebview.api.get_actions(path);
        renderActionCards(actions, path, container, '');
      }
    });
  });

  container.querySelectorAll('[data-cancel-edit]').forEach(el => {
    el.addEventListener('click', () => {
      document.getElementById(`edit-form-${el.dataset.cancelEdit}`)?.classList.add('hidden');
    });
  });
  container.querySelectorAll('[data-panel]').forEach(el => {
    el.addEventListener('click', () => moveToPanel(path, parseInt(el.dataset.panel), el));
  });
  container.querySelectorAll('[data-toggle]').forEach(el => {
    el.addEventListener('click', () => toggleCard(parseInt(el.dataset.toggle), path));
  });
  container.querySelectorAll('[data-toggleclose]').forEach(el => {
    el.addEventListener('click', () => toggleCard(parseInt(el.dataset.toggleclose), path));
  });
  container.querySelectorAll('[data-browse]').forEach(el => {
    el.addEventListener('click', () => browseRunDir(parseInt(el.dataset.browse)));
  });
  container.querySelectorAll('[data-browse-file]').forEach(el => {
    el.addEventListener('click', () => browseRunFile(parseInt(el.dataset.browseFile)));
  });
  container.querySelectorAll('[data-launch]').forEach(el => {
    el.addEventListener('click', () => launchRun(path, parseInt(el.dataset.launch)));
  });

  container.querySelectorAll('.prompt-box').forEach(el => {
    el.addEventListener('blur', () => savePrompt(path, parseInt(el.id.replace('prompt-', '')), el.value));
  });
}

function _renderAddActionBtn(path, container) {
  const wrapper = document.createElement('div');
  wrapper.style.cssText = 'margin-top:10px';
  const btn = document.createElement('button');
  btn.className = 'btn btn-ghost btn-sm';
  btn.textContent = t('btn_add_action');
  btn.addEventListener('click', () => showAddActionForm(path, container, wrapper));
  wrapper.appendChild(btn);
  container.appendChild(wrapper);
}

function showAddActionForm(path, container, btnWrapper) {
  btnWrapper.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:6px;padding:10px;background:var(--card);border-radius:8px;margin-top:4px">
      <input type="text" id="new-action-title" class="dir-input" placeholder="${escHtml(t('add_action_title_ph'))}" style="font-size:13px">
      <input type="text" id="new-action-assignee" class="dir-input" placeholder="${escHtml(t('add_action_assignee_ph'))}" style="font-size:13px">
      <input type="text" id="new-action-deadline" class="dir-input" placeholder="${escHtml(t('add_action_deadline_ph'))}" style="font-size:13px">
      <div style="display:flex;gap:8px;margin-top:2px">
        <button class="btn btn-primary btn-sm" id="btn-save-action">${t('add_action_save')}</button>
        <button class="btn btn-ghost btn-sm" id="btn-cancel-action">${t('add_action_cancel')}</button>
      </div>
    </div>`;

  document.getElementById('new-action-title').focus();

  document.getElementById('btn-cancel-action').addEventListener('click', () => {
    btnWrapper.innerHTML = '';
    const btn = document.createElement('button');
    btn.className = 'btn btn-ghost btn-sm';
    btn.textContent = t('btn_add_action');
    btn.addEventListener('click', () => showAddActionForm(path, container, btnWrapper));
    btnWrapper.appendChild(btn);
  });

  document.getElementById('btn-save-action').addEventListener('click', async () => {
    const title = document.getElementById('new-action-title').value.trim();
    if (!title) { document.getElementById('new-action-title').focus(); return; }
    const assignee = document.getElementById('new-action-assignee').value.trim();
    const deadline = document.getElementById('new-action-deadline').value.trim();
    const action = await pywebview.api.create_action(path, title, assignee, deadline);
    if (action && action.index !== undefined) {
      const actions = await pywebview.api.get_actions(path);
      renderActionCards(actions, path, container, '');
      _prefillWorkingDirs(actions, path);
      _renderAddActionBtn(path, container);
    }
  });
}

async function toggleCard(index, path) {
  const body = document.getElementById('body-' + index);
  if (!body) return;
  const wasOpen = body.classList.contains('open');
  body.classList.toggle('open');
  const btn = document.querySelector(`[data-toggle="${index}"]`);
  if (btn) btn.textContent = wasOpen ? t('btn_prompt_show') : t('btn_prompt_hide');
  if (!wasOpen && path) {
    const dirInput = document.getElementById('dir-' + index);
    if (dirInput && !dirInput.value) {
      const suggested = await pywebview.api.get_action_working_dir(path, index);
      if (suggested) {
        dirInput.value = suggested;
        _showDirHint(index, suggested);
      }
    }
  }
}

function _showDirHint(index, path) {
  const el = document.getElementById('dirhint-' + index);
  if (!el) return;
  if (!path) { el.textContent = ''; el.className = 'dir-hint'; return; }
  const isFile = /\.[a-zA-Z0-9]{1,5}$/.test(path.split(/[\\/]/).pop());
  if (isFile) {
    el.textContent = 'Claude modificará este archivo directamente';
    el.className = 'dir-hint dir-hint-ok';
  } else if (path.toLowerCase().includes('Noted')) {
    el.textContent = 'Ajusta la carpeta si el documento está en otro directorio';
    el.className = 'dir-hint dir-hint-warn';
  } else {
    el.textContent = '';
    el.className = 'dir-hint';
  }
}

async function _prefillWorkingDirs(actions, path) {
  for (const a of actions) {
    if (!a.executed && a.claude_executable) {
      const hintEl = document.getElementById('dirhint-' + a.index);
      if (a.type === 'document_change') {
        // Document actions need a specific file — prompt user to select it
        if (hintEl) {
          hintEl.textContent = 'Selecciona el archivo concreto con el botón de la derecha';
          hintEl.className = 'dir-hint dir-hint-warn';
        }
      } else {
        const dirEl = document.getElementById('dir-' + a.index);
        if (dirEl && !dirEl.value) {
          const suggested = await pywebview.api.get_action_working_dir(path, a.index);
          if (suggested) {
            dirEl.value = suggested;
            _showDirHint(a.index, suggested);
          }
        }
      }
    }
  }
}

// ── Task board (Panel de Acciones — Notion style) ────────────────────────────

const PROJECT_COLORS = ['#6366f1','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899','#84cc16','#14b8a6'];

function _projectColor(project) {
  if (!project || project.id === 'none') return 'var(--accent)';
  if (project.color) return project.color;
  const id = project.id || '';
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash + id.charCodeAt(i)) % PROJECT_COLORS.length;
  return PROJECT_COLORS[hash];
}

const STATUS_CYCLE   = ['not_started', 'in_progress', 'blocked', 'paused', 'pending_feedback', 'done'];
const PRIORITY_CYCLE = [null, 'high', 'medium', 'low'];

// Sync status ↔ bucket for default buckets only
const STATUS_TO_BUCKET = {
  not_started: 'pendiente', blocked: 'pendiente', paused: 'pendiente',
  in_progress: 'en-progreso', pending_feedback: 'en-progreso',
  done: 'hecho',
};
const BUCKET_TO_STATUS = {
  pendiente: 'not_started', 'en-progreso': 'in_progress', hecho: 'done',
};

function _syncBucketFromStatus(task, newStatus) {
  const targetBucket = STATUS_TO_BUCKET[newStatus];
  if (!targetBucket) return;
  if (!_buckets.some(b => b.id === targetBucket)) return; // only if bucket exists
  if (task.bucket_id === targetBucket) return;
  task.bucket_id = targetBucket;
  pywebview.api.update_task(task.id, { bucket_id: targetBucket });
}

function _syncStatusFromBucket(task, newBucketId) {
  const targetStatus = BUCKET_TO_STATUS[newBucketId];
  if (!targetStatus) return;
  if (task.status === targetStatus) return;
  task.status = targetStatus;
  pywebview.api.update_task(task.id, { status: targetStatus });
}

function _statusLabel(s) {
  if (!s) return t('status_not_started');
  const key = 'status_' + s;
  const label = t(key);
  if (label !== key) return label;
  const custom = _customStatuses.find(c => c.id === s);
  return custom ? custom.name : s;
}
function _priorityLabel(p) {
  if (!p) return t('priority_none');
  const key = 'priority_' + p;
  const label = t(key);
  if (label !== key) return label;
  const custom = _customPriorities.find(c => c.id === p);
  return custom ? custom.name : p;
}
function _statusCls(s)   { return STATUS_CYCLE.includes(s || 'not_started') ? (s || 'not_started') : 'custom_status'; }
function _priorityCls(p) { return p || 'none_p'; }

async function loadTaskBoard() {
  const body = document.getElementById('task-board-body');
  if (!body) return;
  body.innerHTML = `<div class="loading">${t('loading_actions')}</div>`;
  _taskData = await pywebview.api.get_tasks();
  _buckets = _taskData.buckets || [];
  _customStatuses    = _taskData.custom_statuses    || [];
  _customPriorities  = _taskData.custom_priorities  || [];
  _statusColumnOrder = _taskData.status_column_order || [];
  // Tooltips i18n
  document.getElementById('btn-view-list')?.setAttribute('title', t('view_list'));
  document.getElementById('btn-view-board')?.setAttribute('title', t('view_board'));
  document.querySelector('.task-board-header .btn-ghost')?.setAttribute('title', t('refresh'));
  body.classList.toggle('is-board', _boardView);
  renderTaskBoard();
}

function setTaskView(view) {
  _boardView = view === 'board';
  document.getElementById('btn-view-list')?.classList.toggle('active', !_boardView);
  document.getElementById('btn-view-board')?.classList.toggle('active', _boardView);
  document.getElementById('task-board-body')?.classList.toggle('is-board', _boardView);
  renderTaskBoard();
}

function _setSortBy(val) {
  _sortBy = val || null;
  renderKanbanBoard();
}

function _setGroupBy(val) {
  _groupBy = val || 'status';
  renderKanbanBoard();
}

function _getGroupByColumns(tasks) {
  if (_groupBy === 'status') {
    const defaults = [
      {id: 'not_started',      name: t('status_not_started'),      color: '#6b7280'},
      {id: 'in_progress',      name: t('status_in_progress'),      color: '#3b82f6'},
      {id: 'blocked',          name: t('status_blocked'),          color: '#ef4444'},
      {id: 'paused',           name: t('status_paused'),           color: '#f59e0b'},
      {id: 'pending_feedback', name: t('status_pending_feedback'), color: '#8b5cf6'},
      {id: 'done',             name: t('status_done'),             color: '#10b981'},
    ];
    const all = [...defaults, ..._customStatuses];
    if (!_statusColumnOrder.length) return all;
    const byId = Object.fromEntries(all.map(c => [c.id, c]));
    const ordered = _statusColumnOrder.map(id => byId[id]).filter(Boolean);
    const inOrder = new Set(_statusColumnOrder);
    return [...ordered, ...all.filter(c => !inOrder.has(c.id))];
  }
  if (_groupBy === 'priority') {
    const L = currentLang === 'en';
    return [
      {id:'urgent', name: L?'Urgent':'Urgente',          color:'#ef4444'},
      {id:'high',   name: L?'High':'Alta',               color:'#f97316'},
      {id:'medium', name: L?'Medium':'Media',            color:'#f59e0b'},
      {id:'low',    name: L?'Low':'Baja',                color:'#3b82f6'},
      {id:'none_p', name: L?'No priority':'Sin prioridad', color:'#6b7280'},
    ];
  }
  if (_groupBy === 'assignee') {
    const seen = new Set();
    const cols = [];
    tasks.forEach(t => {
      if (t.assignee && !seen.has(t.assignee)) {
        seen.add(t.assignee);
        cols.push({id: t.assignee, name: t.assignee, color: '#8b5cf6'});
      }
    });
    cols.push({id:'unassigned', name: currentLang==='en'?'Unassigned':'Sin asignar', color:'#6b7280'});
    return cols;
  }
  if (_groupBy === 'due_date') {
    const L = currentLang === 'en';
    return [
      {id:'overdue',   name: L?'Overdue':'Vencidas',      color:'#ef4444'},
      {id:'today',     name: L?'Today':'Hoy',             color:'#f97316'},
      {id:'this_week', name: L?'This week':'Esta semana', color:'#3b82f6'},
      {id:'later',     name: L?'Later':'Más adelante',    color:'#10b981'},
      {id:'no_date',   name: L?'No date':'Sin fecha',     color:'#6b7280'},
    ];
  }
  return [];
}

function _getTaskColId(task, columns) {
  if (_groupBy === 'status') return task.status || 'not_started';
  if (_groupBy === 'priority') return task.priority || 'none_p';
  if (_groupBy === 'assignee') return task.assignee || 'unassigned';
  if (_groupBy === 'due_date') {
    const d = _parseValidDate(task.end_date || task.deadline);
    if (!d) return 'no_date';
    const today = new Date(); today.setHours(0,0,0,0);
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate()+1);
    const nextWeek = new Date(today); nextWeek.setDate(today.getDate()+7);
    if (d < today) return 'overdue';
    if (d < tomorrow) return 'today';
    if (d < nextWeek) return 'this_week';
    return 'later';
  }
  return columns[0]?.id || '';
}

function _sortedColTasks(tasks) {
  if (!_sortBy) return tasks;
  return [...tasks].sort((a, b) => {
    if (_sortBy === 'end_date') {
      const da = _parseValidDate(a.end_date || a.deadline);
      const db = _parseValidDate(b.end_date || b.deadline);
      if (!da && !db) return 0;
      if (!da) return 1;
      if (!db) return -1;
      return da - db;
    }
    if (_sortBy === 'priority') {
      const o = { high: 0, medium: 1, low: 2 };
      return (o[a.priority] ?? 3) - (o[b.priority] ?? 3);
    }
    if (_sortBy === 'title') return (a.title || '').localeCompare(b.title || '');
    if (_sortBy === 'tag') return ((a.tags || [])[0] || '').localeCompare((b.tags || [])[0] || '');
    return 0;
  });
}

function renderFilterBar() {
  const bar = document.getElementById('task-filter-bar');
  if (!bar) return;
  const { projects } = _taskData;
  const L = currentLang === 'en';

  const noProj = L ? 'No project' : 'Sin proyecto';
  const allBtn = `<button class="task-filter-btn${_filterProjectId === null ? ' active' : ''}" onclick="_setFilter(null)">${t('task_filter_all')}</button>`;
  const noProjBtn = `<button class="task-filter-btn${_filterProjectId === 'none' ? ' active' : ''}" onclick="_setFilter('none')">${noProj}</button>`;
  const projBtns = (projects || []).map(p => {
    const color = _projectColor(p);
    const active = _filterProjectId === p.id ? ' active' : '';
    return `<button class="task-filter-btn${active}" style="--f-color:${color}" onclick="_setFilter('${p.id}')">${escHtml(p.name)}</button>`;
  }).join('');

  const boardControls = _boardView ? `
    <div class="task-filter-actions">
      <span class="task-groupby-label">${L ? 'Group by' : 'Agrupar'}</span>
      <select class="task-groupby-select" onchange="_setGroupBy(this.value)">
        <option value="status"${_groupBy==='status'?' selected':''}>${L?'Status':'Estado'}</option>
        <option value="priority"${_groupBy==='priority'?' selected':''}>${L?'Priority':'Prioridad'}</option>
        <option value="assignee"${_groupBy==='assignee'?' selected':''}>${L?'Assignee':'Responsable'}</option>
        <option value="due_date"${_groupBy==='due_date'?' selected':''}>${L?'Due date':'Fecha'}</option>
      </select>
      ${['status', 'priority'].includes(_groupBy) ? `<button class="kanban-add-col-btn" onclick="_addColumn()"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> ${L ? 'Column' : 'Columna'}</button>` : ''}
    </div>` : '';

  bar.innerHTML = `<div class="task-filter-pills">${allBtn}${noProjBtn}${projBtns}</div>${boardControls}`;
}

function _setFilter(projectId) {
  _filterProjectId = projectId;
  renderFilterBar();
  renderTaskBoard();
}

function _filteredTasks() {
  let tasks = _taskData.tasks || [];
  if (_filterProjectId !== null)
    tasks = tasks.filter(t => (t.project_id || 'none') === _filterProjectId);
  if (_colFilters.status?.size)
    tasks = tasks.filter(t => _colFilters.status.has(t.status || 'not_started'));
  if (_colFilters.assignee?.size)
    tasks = tasks.filter(t => _colFilters.assignee.has(t.assignee || '__none__'));
  if (_colFilters.priority?.size)
    tasks = tasks.filter(t => _colFilters.priority.has(t.priority || '__none__'));
  if (_colFilters.start_date?.size)
    tasks = tasks.filter(t => _colFilters.start_date.has(t.start_date || '__none__'));
  if (_colFilters.end_date?.size)
    tasks = tasks.filter(t => _colFilters.end_date.has(t.end_date || t.deadline || '__none__'));
  return tasks;
}

// ── Column header filter (Excel-style) ──────────────────────────────────────
let _activeColFilter = null; // currently open column key

function _colFilterValues(col) {
  const tasks = (() => {
    let t = _taskData.tasks || [];
    if (_filterProjectId !== null) t = t.filter(x => (x.project_id || 'none') === _filterProjectId);
    return t;
  })();
  const vals = new Set();
  tasks.forEach(t => {
    let v;
    if (col === 'status')     v = t.status     || 'not_started';
    else if (col === 'assignee')  v = t.assignee    || '__none__';
    else if (col === 'priority')  v = t.priority    || '__none__';
    else if (col === 'start_date') v = t.start_date || '__none__';
    else if (col === 'end_date')  v = t.end_date || t.deadline || '__none__';
    if (v !== undefined) vals.add(v);
  });
  return [...vals].sort();
}

function _colFilterLabel(col, val) {
  const L = currentLang === 'en';
  if (val === '__none__') return L ? '(empty)' : '(vacío)';
  if (col === 'status')   return _statusLabel(val);
  if (col === 'priority') return _priorityLabel(val);
  return val;
}

function _openColFilter(e, col) {
  e.stopPropagation();
  const existing = document.getElementById('col-filter-panel');
  if (existing && _activeColFilter === col) { _closeColFilter(); return; }
  _closeColFilter();
  _activeColFilter = col;

  const L = currentLang === 'en';
  const selected = _colFilters[col] || null;
  const vals = _colFilterValues(col);
  const allChecked = !selected;

  const rows = vals.map(v => {
    const checked = allChecked || selected.has(v) ? 'checked' : '';
    return `<label class="cf-row"><input type="checkbox" value="${escHtml(v)}" ${checked} data-cf-col="${col}"> ${escHtml(_colFilterLabel(col, v))}</label>`;
  }).join('');

  const panel = document.createElement('div');
  panel.id = 'col-filter-panel';
  panel.className = 'col-filter-panel';
  panel.innerHTML = `
    <label class="cf-row cf-all"><input type="checkbox" id="cf-all-chk" ${allChecked ? 'checked' : ''}> <b>${L ? 'Select all' : 'Seleccionar todo'}</b></label>
    <div class="cf-divider"></div>
    <div class="cf-list">${rows}</div>
    <div class="cf-footer">
      <button class="cf-btn-clear" onclick="_clearColFilter('${col}')">${L ? 'Clear' : 'Limpiar'}</button>
      <button class="cf-btn-apply" onclick="_applyColFilter('${col}')">${L ? 'Apply' : 'Aplicar'}</button>
    </div>`;

  // Position below the clicked button
  document.body.appendChild(panel);
  const btn = e.currentTarget;
  const rect = btn.getBoundingClientRect();
  panel.style.left = Math.min(rect.left, window.innerWidth - 220) + 'px';
  panel.style.top  = (rect.bottom + 4) + 'px';

  // "Select all" toggle
  panel.querySelector('#cf-all-chk').addEventListener('change', function() {
    panel.querySelectorAll('[data-cf-col]').forEach(cb => cb.checked = this.checked);
  });
  // Individual checkbox → update "select all"
  panel.querySelectorAll('[data-cf-col]').forEach(cb => {
    cb.addEventListener('change', () => {
      const all = [...panel.querySelectorAll('[data-cf-col]')].every(c => c.checked);
      panel.querySelector('#cf-all-chk').checked = all;
    });
  });

  setTimeout(() => document.addEventListener('click', _colFilterOutside), 0);
}

function _colFilterOutside(e) {
  const panel = document.getElementById('col-filter-panel');
  if (panel && !panel.contains(e.target)) _closeColFilter();
}

function _closeColFilter() {
  document.getElementById('col-filter-panel')?.remove();
  document.removeEventListener('click', _colFilterOutside);
  _activeColFilter = null;
}

function _applyColFilter(col) {
  const panel = document.getElementById('col-filter-panel');
  if (!panel) return;
  const checked = [...panel.querySelectorAll('[data-cf-col]:checked')].map(c => c.value);
  const all = [...panel.querySelectorAll('[data-cf-col]')].length;
  if (checked.length === 0 || checked.length === all) {
    delete _colFilters[col];
  } else {
    _colFilters[col] = new Set(checked);
  }
  _closeColFilter();
  renderTaskBoard();
}

function _clearColFilter(col) {
  delete _colFilters[col];
  _closeColFilter();
  renderTaskBoard();
}

function renderTaskBoard() {
  const body = document.getElementById('task-board-body');
  if (!body) return;
  renderFilterBar();
  if (_boardView) { renderKanbanBoard(); return; }
  const { projects } = _taskData;
  const tasks = _filteredTasks();

  const byProject = {};
  tasks.forEach(task => {
    const pid = task.project_id || 'none';
    if (!byProject[pid]) byProject[pid] = [];
    byProject[pid].push(task);
  });

  // Latest last_edited per project (for sorting)
  const latestEdit = {};
  (_taskData.tasks || []).forEach(tk => {
    const pid = tk.project_id || 'none';
    const ts = tk.last_edited || tk.created_at || '';
    if (!latestEdit[pid] || ts > latestEdit[pid]) latestEdit[pid] = ts;
  });

  let sections = _filterProjectId
    ? (projects.find(p => p.id === _filterProjectId)
        ? [projects.find(p => p.id === _filterProjectId)]
        : [{ id: _filterProjectId, name: t('task_no_project') }])
    : [{ id: 'none', name: t('task_no_project') }, ...projects];

  // Sort: most recently edited first; projects with no tasks go to bottom
  sections.sort((a, b) => {
    const ta = latestEdit[a.id] || '';
    const tb = latestEdit[b.id] || '';
    if (!ta && !tb) return 0;
    if (!ta) return 1;
    if (!tb) return -1;
    return tb > ta ? 1 : -1;
  });

  body.innerHTML = sections.map(p => {
    const ptasks = byProject[p.id] || [];
    const hasNoTasks = ptasks.filter(tk => !tk.parent_id).length === 0;
    return _renderProjectSection(p, ptasks, hasNoTasks);
  }).join('');
  _bindTaskBoardEvents();
}

function _renderProjectSection(project, projectTasks, startCollapsed = false) {
  const topLevel = projectTasks.filter(t => !t.parent_id);
  const subMap = {};
  projectTasks.forEach(t => {
    if (t.parent_id) { if (!subMap[t.parent_id]) subMap[t.parent_id] = []; subMap[t.parent_id].push(t); }
  });
  const done = topLevel.filter(t => t.status === 'done').length;
  const color = _projectColor(project);

  return `
  <div class="task-project-section" data-project-id="${project.id}">
    <div class="task-project-header" style="border-left-color:${color}" onclick="toggleProjectSection('${project.id}')">
      <span class="task-project-chevron${startCollapsed ? '' : ' open'}" style="color:${color}">▶</span>
      <span class="task-project-name" style="color:${color}">${escHtml(project.name)}</span>
      <span class="task-project-count" style="color:${color};background:${color}1a">${done}/${topLevel.length}</span>
    </div>
    <div class="task-project-body" id="proj-body-${project.id}"${startCollapsed ? ' style="display:none"' : ''}>
      <div class="task-col-headers">
        <span class="task-col-hdr">${t('task_col_name')}</span>
        ${['status','assignee','start_date','end_date','priority'].map((col, i) => {
          const labels = [t('task_col_status'), t('task_col_assignee'), t('task_col_start_date'), t('task_col_end_date'), t('task_col_priority')];
          const active = !!_colFilters[col]?.size;
          return `<span class="task-col-hdr filterable${active?' hdr-filtered':''}">
            ${labels[i]}
            <button class="col-filter-btn${active?' active':''}" onclick="_openColFilter(event,'${col}')" title="Filtrar">
              <svg width="9" height="9" viewBox="0 0 24 24" fill="${active?'currentColor':'none'}" stroke="currentColor" stroke-width="2.5"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
            </button>
          </span>`;
        }).join('')}
        <span></span>
      </div>
      ${topLevel.map(task => _renderTaskRow(task, subMap[task.id] || [], false)).join('')}
      <div class="task-add-row" data-add-project="${project.id}" data-add-parent="">
        <button class="task-add-btn">${t('task_add_task')}</button>
      </div>
    </div>
  </div>`;
}

function _renderTaskRow(task, subitems, isSubitem) {
  const isParent = !isSubitem && subitems.length > 0;
  const cls = [
    'task-row',
    isSubitem ? 'subitem' : '',
    isParent ? 'parent-task' : '',
  ].filter(Boolean).join(' ');
  const doneCls = task.status === 'done' ? ' done' : '';
  const expandBtn = subitems.length
    ? `<button class="task-expand-btn open" data-expand="${task.id}">▶</button>`
    : `<button class="task-expand-btn placeholder" disabled>▶</button>`;
  const claudeBadge = task.claude_executable
    ? `<span class="task-claude-badge">✦ Claude</span>` : '';
  const subRows = subitems.map(s => _renderTaskRow(s, [], true)).join('');
  const subAdd = !isSubitem ? `
    <div class="task-add-row subitem-add" data-add-project="${task.project_id}" data-add-parent="${task.id}">
      <button class="task-add-btn">${t('task_add_subitem')}</button>
    </div>` : '';

  return `
  <div class="task-row-wrap" id="taskwrap-${task.id}">
    <div class="${cls}" data-task-id="${task.id}">
      <div class="task-name-cell">
        ${expandBtn}
        <input class="task-title-input${doneCls}" type="text" value="${escHtml(task.title)}"
               title="${escHtml(task.title)}"
               data-task-field="${task.id}:title" placeholder="${t('task_new_ph')}">
        ${claudeBadge}
        <button class="task-row-detail" data-detail-task="${task.id}">···</button>
      </div>
      <div>
        <select class="task-status-select ${_statusCls(task.status)}" data-status-task="${task.id}">
          ${STATUS_CYCLE.map(s => `<option value="${s}" ${task.status === s ? 'selected' : ''}>${_statusLabel(s)}</option>`).join('')}
          ${_customStatuses.map(s => `<option value="${escHtml(s.id)}" ${task.status === s.id ? 'selected' : ''}>${escHtml(s.name)}</option>`).join('')}
        </select>
      </div>
      <div><input class="task-cell-input" type="text" value="${escHtml(task.assignee || '')}"
           placeholder="—" data-task-field="${task.id}:assignee"></div>
      <div><input class="task-cell-input task-date-input" type="date" value="${_toDateInput(task.start_date)}"
           data-task-field="${task.id}:start_date" title="${t('task_col_start_date')}"></div>
      <div><input class="task-cell-input task-date-input" type="date" value="${_toDateInput(task.end_date || task.deadline)}"
           data-task-field="${task.id}:end_date" title="${t('task_col_end_date')}"></div>
      <div><button class="task-priority-btn ${_priorityCls(task.priority)}"
           data-priority-task="${task.id}">${_priorityLabel(task.priority)}</button></div>
      <button class="task-row-del" data-del-task="${task.id}" title="Eliminar">×</button>
    </div>
    ${subitems.length || !isSubitem ? `<div class="task-subitems" id="subs-${task.id}">${subRows}${subAdd}</div>` : ''}
  </div>`;
}

function toggleProjectSection(projectId) {
  const body = document.getElementById('proj-body-' + projectId);
  const chevron = document.querySelector(`[data-project-id="${projectId}"] .task-project-chevron`);
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  chevron?.classList.toggle('open', !open);
}

function _refreshProjectCount(projectId) {
  const section = document.querySelector(`[data-project-id="${projectId}"]`);
  if (!section) return;
  const top  = _taskData.tasks.filter(t => t.project_id === projectId && !t.parent_id);
  const done = top.filter(t => t.status === 'done').length;
  const el = section.querySelector('.task-project-count');
  if (el) el.textContent = `${done}/${top.length}`;
}

function _bindTaskBoardEvents() {
  const body = document.getElementById('task-board-body');
  if (!body) return;

  body.querySelectorAll('[data-status-task]').forEach(sel => {
    sel.addEventListener('change', async () => {
      const id = sel.dataset.statusTask;
      const task = _taskData.tasks.find(t => t.id === id);
      if (!task) return;
      const next = sel.value;
      task.status = next;
      sel.className = 'task-status-select ' + _statusCls(next);
      document.querySelector(`[data-task-field="${id}:title"]`)?.classList.toggle('done', next === 'done');
      await pywebview.api.update_task(id, { status: next });
      _syncBucketFromStatus(task, next);
      _refreshProjectCount(task.project_id);
      refreshPendingBadge();
    });
  });

  body.querySelectorAll('[data-priority-task]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.priorityTask;
      const task = _taskData.tasks.find(t => t.id === id);
      if (!task) return;
      const next = PRIORITY_CYCLE[(PRIORITY_CYCLE.indexOf(task.priority) + 1) % PRIORITY_CYCLE.length];
      task.priority = next;
      btn.textContent = _priorityLabel(next);
      btn.className = 'task-priority-btn ' + _priorityCls(next);
      await pywebview.api.update_task(id, { priority: next });
    });
  });

  body.querySelectorAll('[data-task-field]').forEach(input => {
    const saveInput = async () => {
      const [id, field] = input.dataset.taskField.split(':');
      const task = _taskData.tasks.find(t => t.id === id);
      const val = input.value || null;
      if (!task || task[field] === val) return;
      task[field] = val;
      await pywebview.api.update_task(id, { [field]: val });
      if (_boardView) renderKanbanBoard();
    };
    input.addEventListener(input.type === 'date' ? 'change' : 'blur', saveInput);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') input.blur(); });
  });

  body.querySelectorAll('[data-expand]').forEach(btn => {
    btn.addEventListener('click', () => {
      const subsEl = document.getElementById('subs-' + btn.dataset.expand);
      if (!subsEl) return;
      const open = btn.classList.contains('open');
      btn.classList.toggle('open', !open);
      subsEl.style.display = open ? 'none' : '';
    });
  });

  body.querySelectorAll('[data-del-task]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.delTask;
      const task = _taskData.tasks.find(t => t.id === id);
      openConfirmModal(
        t('confirm_delete_task', task?.title || ''),
        async () => {
          await pywebview.api.delete_task(id);
          _taskData.tasks = _taskData.tasks.filter(t => t.id !== id && t.parent_id !== id);
          document.getElementById('taskwrap-' + id)?.remove();
          if (task?.project_id) _refreshProjectCount(task.project_id);
          refreshPendingBadge();
          if (task?.meeting_path && currentPath) refreshMeetingActions(currentPath);
        },
        { title: t('confirm_delete_task_title'), okLabel: t('btn_delete') }
      );
    });
  });

  body.querySelectorAll('[data-add-project]').forEach(row => {
    row.addEventListener('click', () => _startAddTask(row));
  });

  body.querySelectorAll('[data-goto-meeting]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const task = _taskData.tasks.find(tk => tk.id === btn.dataset.gotoMeeting);
      if (task?.meeting_path) { showView('meetings'); openMeeting(task.meeting_path); }
    });
  });

  body.querySelectorAll('[data-detail-task]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      openTaskDetail(btn.dataset.detailTask);
    });
  });
}

function _startAddTask(addRow) {
  if (addRow.classList.contains('editing')) return;
  const projectId = addRow.dataset.addProject;
  const parentId  = addRow.dataset.addParent || '';
  addRow.classList.add('editing');
  addRow.innerHTML = `<input class="task-new-input" type="text" placeholder="${t('task_new_ph')}" autofocus>`;
  const input = addRow.querySelector('.task-new-input');
  input.focus();
  let committed = false;
  const commit = async () => {
    if (committed) return; committed = true;
    const title = input.value.trim();
    if (!title) { renderTaskBoard(); return; }
    const task = await pywebview.api.create_task(projectId, title, parentId, '', '', '', '', '', '', [], '');
    _taskData.tasks.push(task);
    renderTaskBoard();
    refreshPendingBadge();
  };
  input.addEventListener('blur', commit);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { input.value = ''; commit(); }
  });
}

// ── Kanban board ──────────────────────────────────────────────────────────────

function renderKanbanBoard() {
  const body = document.getElementById('task-board-body');
  if (!body) return;
  const tasks = _filteredTasks();
  const isVirtual = _groupBy !== 'status';
  const columns = _getGroupByColumns(tasks);
  body.innerHTML = `
    <div class="kanban-board" id="kanban-board">
      ${columns.map(col => _renderKanbanColumn(col, _sortedColTasks(tasks.filter(t => _getTaskColId(t, columns) === col.id)), isVirtual)).join('')}
    </div>`;
  _bindKanbanEvents();
}

function _renderKanbanColumn(col, colTasks, isVirtual = false) {
  const cards = colTasks.map(t => _renderKanbanCard(t)).join('');
  const isStatusMode   = _groupBy === 'status';
  const isCustomStatus = isStatusMode && _customStatuses.some(s => s.id === col.id);

  let colHeader;
  if (isVirtual) {
    // assignee / due_date: read-only plain header
    colHeader = `<div class="kanban-col-header">
      <span class="kanban-col-color-dot" style="background:${col.color};pointer-events:none"></span>
      <span class="kanban-col-title">${escHtml(col.name)}</span>
      <span class="kanban-col-count">${colTasks.length}</span>
    </div>`;
  } else if (isStatusMode) {
    // status: draggable, deletable; rename only for custom statuses
    const titleEl = isCustomStatus
      ? `<span class="kanban-col-title" contenteditable="true" data-title-status="${col.id}" spellcheck="false">${escHtml(col.name)}</span>`
      : `<span class="kanban-col-title">${escHtml(col.name)}</span>`;
    colHeader = `<div class="kanban-col-header" draggable="true"
        ondragstart="_onColDragStart(event,'${col.id}')"
        ondragend="_onColDragEnd()">
      <span class="kanban-col-drag-handle">⠿</span>
      <span class="kanban-col-color-dot" style="background:${col.color};pointer-events:none"></span>
      ${titleEl}
      <span class="kanban-col-count">${colTasks.length}</span>
      <button class="kanban-col-del" data-del-status-col="${col.id}" title="Eliminar columna">×</button>
    </div>`;
  } else {
    // bucket: fully editable with drag-to-reorder
    colHeader = `<div class="kanban-col-header" draggable="true"
        ondragstart="_onColDragStart(event,'${col.id}')"
        ondragend="_onColDragEnd()">
      <span class="kanban-col-drag-handle">⠿</span>
      <span class="kanban-col-color-dot" style="background:${col.color}"
            data-color-bucket="${col.id}" title="Cambiar color"></span>
      <span class="kanban-col-title" contenteditable="true"
            data-title-bucket="${col.id}"
            spellcheck="false">${escHtml(col.name)}</span>
      <span class="kanban-col-count">${colTasks.length}</span>
      <button class="kanban-col-del" data-del-bucket="${col.id}" title="Eliminar columna">×</button>
    </div>`;
  }

  const colAttrs = isVirtual ? '' : isStatusMode
    ? `ondragover="_onColDragOver(event,'${col.id}')" ondragleave="_onColDragLeave(event)" ondrop="_onColDropStatus(event,'${col.id}')"`
    : `ondragover="_onColDragOver(event,'${col.id}')" ondragleave="_onColDragLeave(event)" ondrop="_onColDrop(event,'${col.id}')"` ;

  const canAddTask = !isVirtual; // bucket and status both allow adding tasks
  const addBtn = canAddTask
    ? `<button class="kanban-col-add-btn" data-add-col="${col.id}">+ ${t('task_add_task').replace('+ ','')}</button>`
    : '';

  return `
  <div class="kanban-col" data-bucket-id="${col.id}" ${colAttrs}>
    ${colHeader}
    <div class="kanban-col-body" data-drop-bucket="${col.id}"
         ondragover="if(!_dragBucketId){event.preventDefault();event.currentTarget.classList.add('drag-over');}"
         ondragleave="event.currentTarget.classList.remove('drag-over')"
         ondrop="_onDropCard(event,'${col.id}')">
      ${cards}
    </div>
    ${addBtn}
  </div>`;
}

function _parseValidDate(str) {
  if (!str) return null;
  // Accept YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY
  const d = new Date(str);
  if (!isNaN(d.getTime()) && d.getFullYear() > 1970) return d;
  // Try DD/MM/YYYY
  const m = str.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$/);
  if (m) { const d2 = new Date(`${m[3]}-${m[2].padStart(2,'0')}-${m[1].padStart(2,'0')}`); if (!isNaN(d2.getTime())) return d2; }
  return null;
}

function _fmtLastEdited(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString(currentLang === 'en' ? 'en-GB' : 'es-ES', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
  });
}

function _toDateInput(str) {
  const d = _parseValidDate(str);
  if (!d) return '';
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

function _fmtDate(d) {
  if (!d) return '';
  const now = new Date();
  const opts = d.getFullYear() === now.getFullYear()
    ? { day: 'numeric', month: 'short' }
    : { day: 'numeric', month: 'short', year: 'numeric' };
  return d.toLocaleDateString(currentLang === 'en' ? 'en-GB' : 'es-ES', opts);
}

const _CAL_ICON = `<svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor" style="flex-shrink:0;margin-right:3px;opacity:.7"><rect x="1" y="3" width="14" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5 1v4M11 1v4M1 7h14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;

function _renderCardDate(startStr, endStr) {
  const start = _parseValidDate(startStr);
  const end   = _parseValidDate(endStr);
  if (!start && !end) return '';
  const label = start && end
    ? `${_fmtDate(start)} → ${_fmtDate(end)}`
    : _fmtDate(start || end);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const overdue = end && end < today ? ' overdue' : '';
  return `<span class="kanban-card-deadline${overdue}">${_CAL_ICON}${label}</span>`;
}

const _TAG_COLORS = ['#8b5cf6','#3b82f6','#10b981','#f59e0b','#ef4444','#ec4899','#06b6d4','#6b7280'];
let _tagColorIdx = 0;
function _nextTagColor() { return _TAG_COLORS[_tagColorIdx++ % _TAG_COLORS.length]; }
function _parseTag(tg) {
  if (!tg) return null;
  if (typeof tg === 'string') return {name: tg, color: '#8b5cf6'};
  if (tg.name) return tg;
  return null;
}

function _renderKanbanCard(task) {
  const doneCls = task.status === 'done' ? ' done' : '';

  // Tags — colored pills at top
  const tagPills = (task.tags || []).map(tg => _parseTag(tg)).filter(Boolean).map(({name, color}) =>
    `<span class="kanban-card-tag" style="background:${color}22;color:${color};border-color:${color}40">${escHtml(name)}</span>`
  ).join('');

  // Due date only (Planner only shows end date on card)
  const deadline = _renderCardDate(null, task.end_date || task.deadline);

  // Subtask count
  const subtasks = (_taskData.tasks || []).filter(t => t.parent_id === task.id);
  const doneSubs = subtasks.filter(t => t.status === 'done').length;
  const subtaskBadge = subtasks.length
    ? `<span class="kanban-card-subtasks">${doneSubs}/${subtasks.length}</span>` : '';

  // Priority icon
  const priorityIcon = task.priority === 'high'
    ? `<span class="kanban-card-priority kcp-high">!</span>`
    : task.priority === 'medium'
    ? `<span class="kanban-card-priority kcp-med">!</span>` : '';

  // Assignee initials circle
  const assignee = task.assignee
    ? `<span class="kanban-card-avatar">${escHtml(task.assignee.split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase())}</span>` : '';

  // Quick-done checkmark
  const checked = task.status === 'done' ? ' kcc-checked' : '';
  const checkBtn = `<button class="kanban-card-check${checked}" onclick="event.stopPropagation();_quickDone('${task.id}')" title="Marcar completada">✓</button>`;

  return `
  <div class="kanban-card${doneCls}" draggable="true"
       data-task-id="${task.id}"
       ondragstart="_onDragCard(event,'${task.id}')"
       onclick="_onKanbanCardClick(event,'${task.id}')">
    ${tagPills ? `<div class="kanban-card-tags">${tagPills}</div>` : ''}
    <div class="kanban-card-title-row">
      ${checkBtn}
      <span class="kanban-card-title">${escHtml(task.title)}</span>
    </div>
    <div class="kanban-card-footer">
      <div class="kanban-card-footer-left">${deadline}${subtaskBadge}${priorityIcon}</div>
      <div class="kanban-card-footer-right">${assignee}</div>
    </div>
  </div>`;
}

function _quickDone(taskId) {
  const task = _taskData.tasks.find(t => t.id === taskId);
  if (!task) return;
  const next = task.status === 'done' ? 'not_started' : 'done';
  task.status = next;
  pywebview.api.update_task(taskId, {status: next});
  renderKanbanBoard();
  refreshPendingBadge();
}

function _onDragCard(event, taskId) {
  _dragBucketId = null;
  _dragTaskId = taskId;
  event.dataTransfer.effectAllowed = 'move';
}

function _onDropCard(event, colId) {
  event.preventDefault();
  event.currentTarget.classList.remove('drag-over');
  if (_dragBucketId || !_dragTaskId || !colId) return;
  const task = _taskData.tasks.find(t => t.id === _dragTaskId);
  if (!task) { _dragTaskId = null; return; }

  let updates = {};
  if (_groupBy === 'status') {
    const cur = task.status || 'not_started';
    if (cur === colId) { _dragTaskId = null; return; }
    task.status = colId;
    updates = {status: colId};
    _syncBucketFromStatus(task, colId);
  } else if (_groupBy === 'priority') {
    const prio = colId === 'none_p' ? null : colId;
    if ((task.priority || null) === prio) { _dragTaskId = null; return; }
    task.priority = prio;
    updates = {priority: prio};
  } else if (_groupBy === 'assignee') {
    const assignee = colId === 'unassigned' ? null : colId;
    if ((task.assignee || null) === assignee) { _dragTaskId = null; return; }
    task.assignee = assignee;
    updates = {assignee: assignee};
  } else {
    // due_date: no se puede asignar fecha arrastrando
    _dragTaskId = null;
    return;
  }

  pywebview.api.update_task(_dragTaskId, updates);
  _dragTaskId = null;
  renderKanbanBoard();
}

// ── Column drag-to-reorder ────────────────────────────────────────────────────

function _onColDragStart(event, bucketId) {
  _dragBucketId = bucketId;
  _dragTaskId = null;
  event.dataTransfer.effectAllowed = 'move';
  event.currentTarget.closest('.kanban-col').classList.add('col-dragging');
}

function _onColDragEnd() {
  _dragBucketId = null;
  document.querySelectorAll('.kanban-col').forEach(c =>
    c.classList.remove('col-dragging', 'col-drag-over'));
}

function _onColDragOver(event, bucketId) {
  if (!_dragBucketId || _dragBucketId === bucketId) return;
  event.preventDefault();
  document.querySelectorAll('.kanban-col').forEach(c => c.classList.remove('col-drag-over'));
  event.currentTarget.classList.add('col-drag-over');
}

function _onColDragLeave(event) {
  if (!event.currentTarget.contains(event.relatedTarget))
    event.currentTarget.classList.remove('col-drag-over');
}

function _onColDrop(event, targetBucketId) {
  event.preventDefault();
  if (!_dragBucketId || _dragBucketId === targetBucketId) return;
  event.currentTarget.classList.remove('col-drag-over');
  const fromIdx = _buckets.findIndex(b => b.id === _dragBucketId);
  const toIdx   = _buckets.findIndex(b => b.id === targetBucketId);
  if (fromIdx === -1 || toIdx === -1) return;
  const [moved] = _buckets.splice(fromIdx, 1);
  _buckets.splice(toIdx, 0, moved);
  _buckets.forEach((b, i) => { b.order = i; });
  _dragBucketId = null;
  pywebview.api.save_buckets(_buckets);
  renderKanbanBoard();
}

function _onColDropStatus(event, targetId) {
  event.preventDefault();
  if (!_dragBucketId || _dragBucketId === targetId) { _dragBucketId = null; return; }
  event.currentTarget.classList.remove('col-drag-over');
  const allCols = _getGroupByColumns([]);
  if (!_statusColumnOrder.length) _statusColumnOrder = allCols.map(c => c.id);
  const fromIdx = _statusColumnOrder.indexOf(_dragBucketId);
  const toIdx   = _statusColumnOrder.indexOf(targetId);
  if (fromIdx === -1 || toIdx === -1) { _dragBucketId = null; return; }
  _statusColumnOrder.splice(toIdx, 0, _statusColumnOrder.splice(fromIdx, 1)[0]);
  _dragBucketId = null;
  pywebview.api.save_status_column_order(_statusColumnOrder);
  renderKanbanBoard();
}

function _onKanbanCardClick(event, taskId) {
  if (event.target.closest('[contenteditable]')) return;
  openTaskDetail(taskId);
}

async function _addBucket() {
  const name = prompt(t('bucket_name_prompt'), t('bucket_new_name'));
  if (!name || !name.trim()) return;
  const colors = ['#6b7280','#3b82f6','#8b5cf6','#ec4899','#f59e0b','#10b981','#ef4444','#06b6d4'];
  const bucket = {
    id: 'bucket-' + Date.now(),
    name: name.trim(),
    color: colors[_buckets.length % colors.length],
    order: _buckets.length,
  };
  _buckets.push(bucket);
  await pywebview.api.save_buckets(_buckets);
  renderKanbanBoard();
}

async function _deleteBucket(bucketId) {
  if (_buckets.length <= 1) return;
  const bucket = _buckets.find(b => b.id === bucketId);
  const taskCount = _taskData.tasks.filter(t => t.bucket_id === bucketId).length;
  const remaining = _buckets.filter(b => b.id !== bucketId);
  const fallbackName = remaining[0]?.name || '';
  openConfirmModal(
    t('confirm_delete_column', bucket?.name || '', taskCount, fallbackName),
    async () => {
      const fallback = remaining[0]?.id || 'pendiente';
      for (const tk of _taskData.tasks.filter(t => t.bucket_id === bucketId)) {
        tk.bucket_id = fallback;
        await pywebview.api.update_task(tk.id, {bucket_id: fallback});
      }
      _buckets = remaining.map((b, i) => ({...b, order: i}));
      await pywebview.api.save_buckets(_buckets);
      renderKanbanBoard();
    },
    { title: t('confirm_delete_column_title'), okLabel: t('btn_delete') }
  );
}

async function _saveBucketTitle(bucketId, newName) {
  const b = _buckets.find(b => b.id === bucketId);
  if (!b || !newName.trim() || b.name === newName.trim()) return;
  b.name = newName.trim();
  await pywebview.api.save_buckets(_buckets);
}

async function _saveBucketColor(bucketId, color) {
  const b = _buckets.find(b => b.id === bucketId);
  if (!b) return;
  b.color = color;
  await pywebview.api.save_buckets(_buckets);
  renderKanbanBoard();
}

function _showTagColorPicker(anchorEl, taskId, tagName) {
  document.getElementById('tag-color-picker')?.remove();
  const picker = document.createElement('div');
  picker.id = 'tag-color-picker';
  picker.className = 'kanban-color-picker';
  _TAG_COLORS.forEach(c => {
    const sw = document.createElement('button');
    sw.className = 'kanban-color-swatch';
    sw.style.background = c;
    sw.onclick = async (ev) => {
      ev.stopPropagation();
      picker.remove();
      const tk = _taskData.tasks.find(t => t.id === taskId);
      if (!tk) return;
      tk.tags = (tk.tags || []).map(tg => {
        const parsed = _parseTag(tg);
        if (!parsed) return tg;
        return parsed.name === tagName ? {name: parsed.name, color: c} : tg;
      }).filter(Boolean);
      // Update pill in DOM
      const pill = document.querySelector(`.drawer-tag[data-tag-name="${tagName}"]`);
      if (pill) {
        pill.style.cssText = `background:${c}22;color:${c};border-color:${c}44`;
        const dot = pill.querySelector('.drawer-tag-color-btn');
        if (dot) dot.style.background = c;
      }
      await pywebview.api.update_task(taskId, {tags: tk.tags});
      _updateLastEditedUI?.();
      if (typeof _boardView !== 'undefined' && _boardView) renderKanbanBoard();
    };
    picker.appendChild(sw);
  });
  document.body.appendChild(picker);
  const rect = anchorEl.getBoundingClientRect();
  picker.style.cssText = `position:fixed;top:${rect.bottom+4}px;left:${rect.left}px;z-index:9999`;
  setTimeout(() => document.addEventListener('click', () => picker.remove(), {once:true}), 0);
}

function _showColorPicker(anchorEl, bucketId) {
  document.getElementById('kanban-color-picker')?.remove();
  const colors = ['#6b7280','#3b82f6','#8b5cf6','#ec4899','#f59e0b','#10b981','#ef4444','#06b6d4'];
  const picker = document.createElement('div');
  picker.id = 'kanban-color-picker';
  picker.className = 'kanban-color-picker';
  picker.innerHTML = colors.map(c =>
    `<span class="kanban-color-swatch" style="background:${c}" data-color="${c}"></span>`
  ).join('');
  document.body.appendChild(picker);
  const rect = anchorEl.getBoundingClientRect();
  picker.style.cssText = `position:fixed;top:${rect.bottom+4}px;left:${rect.left}px;z-index:9999`;
  picker.querySelectorAll('[data-color]').forEach(sw => {
    sw.addEventListener('click', e => {
      e.stopPropagation();
      _saveBucketColor(bucketId, sw.dataset.color);
      picker.remove();
    });
  });
  setTimeout(() => document.addEventListener('click', () => picker.remove(), {once: true}), 0);
}

function _bindKanbanEvents() {
  const board = document.getElementById('kanban-board');
  if (!board) return;
  board.querySelectorAll('[data-title-bucket]').forEach(el => {
    el.addEventListener('blur', () => _saveBucketTitle(el.dataset.titleBucket, el.textContent.trim()));
    el.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); el.blur(); }});
  });
  board.querySelectorAll('[data-color-bucket]').forEach(dot => {
    dot.addEventListener('click', e => {
      e.stopPropagation();
      _showColorPicker(dot, dot.dataset.colorBucket);
    });
  });
  board.querySelectorAll('[data-del-bucket]').forEach(btn => {
    btn.addEventListener('click', () => _deleteBucket(btn.dataset.delBucket));
  });
  board.querySelectorAll('[data-add-col]').forEach(btn => {
    btn.addEventListener('click', () => _startAddTaskInColumn(btn.dataset.addCol));
  });

  board.querySelectorAll('[data-del-status-col]').forEach(btn => {
    btn.addEventListener('click', () => _deleteStatusColumn(btn.dataset.delStatusCol));
  });

  board.querySelectorAll('[data-title-status]').forEach(el => {
    el.addEventListener('blur', () => {
      const id = el.dataset.titleStatus;
      const st = _customStatuses.find(s => s.id === id);
      if (st && el.textContent.trim()) { st.name = el.textContent.trim(); pywebview.api.save_custom_statuses(_customStatuses); }
    });
    el.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); el.blur(); } });
  });
}

function _startAddTaskInColumn(colId) {
  const colEl = document.querySelector(`[data-bucket-id="${colId}"] .kanban-col-body`);
  if (!colEl) return;

  const projects = _taskData.projects || [];
  const L = currentLang === 'en';
  const projOpts = `<option value="">${L ? 'No project' : 'Sin proyecto'}</option>` +
    projects.map(p => `<option value="${p.id}">${escHtml(p.name)}</option>`).join('');

  const card = document.createElement('div');
  card.className = 'kanban-card-new-form';
  card.innerHTML = `
    <input class="kanban-card-new-input" type="text" placeholder="${t('task_new_ph') || 'Nueva tarea...'}">
    <select class="kanban-card-new-proj">${projOpts}</select>`;
  colEl.appendChild(card);

  const inp = card.querySelector('.kanban-card-new-input');
  const sel = card.querySelector('.kanban-card-new-proj');
  inp.focus();

  let committed = false;
  const commit = async () => {
    if (committed) return; committed = true;
    const title = inp.value.trim();
    card.remove();
    if (!title) return;
    const projectId = sel.value || 'none';

    let task;
    if (_groupBy === 'status') {
      task = await pywebview.api.create_task(projectId, title, '', '', '', '', '', '', '', [], '', colId);
      task.status = colId;
    } else {
      task = await pywebview.api.create_task(projectId, title, '', '', '', '', colId, '', '', [], '');
      task.bucket_id = colId;
    }
    _taskData.tasks.push(task);
    renderKanbanBoard();
    refreshPendingBadge();
  };

  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { inp.value = ''; commit(); }
  });
  sel.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { sel.value = ''; commit(); }
  });
  card.addEventListener('focusout', e => {
    if (!card.contains(e.relatedTarget)) commit();
  });
}

async function _addColumn() {
  const L = currentLang === 'en';
  const label = _groupBy === 'status'
    ? (L ? 'status' : 'estado')
    : (L ? 'priority' : 'nivel de prioridad');
  const name = prompt(L ? `New ${label} name:` : `Nombre del nuevo ${label}:`, '');
  if (!name || !name.trim()) return;
  const colors = ['#6b7280','#3b82f6','#8b5cf6','#ec4899','#f59e0b','#10b981','#ef4444','#06b6d4'];
  const id = `custom-${_groupBy}-` + Date.now();
  const entry = { id, name: name.trim(), color: colors[(_customStatuses.length + _customPriorities.length) % colors.length] };
  if (_groupBy === 'status') {
    _customStatuses.push(entry);
    await pywebview.api.save_custom_statuses(_customStatuses);
  } else if (_groupBy === 'priority') {
    _customPriorities.push(entry);
    await pywebview.api.save_custom_priorities(_customPriorities);
  }
  renderKanbanBoard();
}

function _deleteStatusColumn(id) {
  const allCols = _getGroupByColumns([]);
  const col = allCols.find(c => c.id === id);
  const remaining = allCols.filter(c => c.id !== id);
  if (!remaining.length) return;
  const fallback = remaining[0].id;
  const taskCount = _taskData.tasks.filter(t => (t.status || 'not_started') === id).length;
  openConfirmModal(
    t('confirm_delete_column', col?.name || id, taskCount, _statusLabel(fallback)),
    async () => {
      for (const tk of _taskData.tasks.filter(t => (t.status || 'not_started') === id)) {
        tk.status = fallback;
        await pywebview.api.update_task(tk.id, {status: fallback});
      }
      if (_statusColumnOrder.length) _statusColumnOrder = _statusColumnOrder.filter(sid => sid !== id);
      await pywebview.api.save_status_column_order(_statusColumnOrder);
      if (_customStatuses.some(s => s.id === id)) {
        _customStatuses = _customStatuses.filter(s => s.id !== id);
        await pywebview.api.save_custom_statuses(_customStatuses);
      }
      renderKanbanBoard();
    },
    { title: t('confirm_delete_column_title'), okLabel: t('btn_delete') }
  );
}

// ── Task detail drawer ────────────────────────────────────────────────────────

let _drawerTaskId = null;

async function openTaskDetail(taskId) {
  const task = _taskData.tasks.find(tk => tk.id === taskId);
  if (!task) return;
  _drawerTaskId = taskId;

  const drawer = document.getElementById('task-detail-drawer');
  const backdrop = document.getElementById('task-drawer-backdrop');
  const body = document.getElementById('task-detail-body');

  body.innerHTML = `<div class="loading">${t('loading')}</div>`;
  drawer.classList.add('open');
  backdrop.classList.add('open');

  // Fetch action data if it comes from a meeting
  let action = null;
  if (task.meeting_path && task.meeting_action_index != null) {
    try { action = await pywebview.api.get_action(task.meeting_path, task.meeting_action_index); } catch (_) {}
  }

  const isClaudeExec = action?.claude_executable;
  const prompt = action?.prompt_enriched || action?.prompt_original || '';
  const statusOpts = STATUS_CYCLE.map(s =>
    `<option value="${s}" ${task.status === s ? 'selected' : ''}>${_statusLabel(s)}</option>`).join('');
  const priorityOpts = PRIORITY_CYCLE.map(p =>
    `<option value="${p ?? ''}" ${(task.priority ?? '') === (p ?? '') ? 'selected' : ''}>${_priorityLabel(p)}</option>`).join('');
  const bucketOpts = _buckets.map(b =>
    `<option value="${b.id}" ${(task.bucket_id || 'pendiente') === b.id ? 'selected' : ''}>${escHtml(b.name)}</option>`
  ).join('');
  const meetingMeta = task.meeting_path ? _meetingMetaFromPath(task.meeting_path) : null;

  // Parent task candidates: top-level tasks in same project, excluding self and own subitems
  const ownDescendants = new Set([taskId]);
  const gatherDesc = (id) => (_taskData.tasks || []).filter(x => x.parent_id === id).forEach(x => { ownDescendants.add(x.id); gatherDesc(x.id); });
  gatherDesc(taskId);
  const parentCandidates = (_taskData.tasks || []).filter(x =>
    !x.parent_id && x.project_id === task.project_id && !ownDescendants.has(x.id)
  );
  const parentOpts = `<option value="">${currentLang === 'en' ? '(top-level)' : '(nivel raíz)'}</option>` +
    parentCandidates.map(x => `<option value="${x.id}"${task.parent_id === x.id ? ' selected' : ''}>${escHtml(x.title)}</option>`).join('');

  body.innerHTML = `
    <div class="drawer-field">
      <div class="drawer-field-label">${t('task_col_name')}</div>
      <textarea class="drawer-title-input" id="drawer-title" rows="1">${escHtml(task.title)}</textarea>
    </div>
    <div class="drawer-fields-row">
      <div class="drawer-field">
        <div class="drawer-field-label">${t('task_col_status')}</div>
        <select class="drawer-cell-input" id="drawer-status">${statusOpts}</select>
      </div>
      <div class="drawer-field">
        <div class="drawer-field-label">${t('task_col_priority')}</div>
        <select class="drawer-cell-input" id="drawer-priority">${priorityOpts}</select>
      </div>
      ${_buckets.length ? `<div class="drawer-field">
        <div class="drawer-field-label">${t('bucket_label')}</div>
        <select class="drawer-cell-input" id="drawer-bucket">${bucketOpts}</select>
      </div>` : ''}
    </div>
    <div class="drawer-fields-row">
      <div class="drawer-field">
        <div class="drawer-field-label">${t('task_col_assignee')}</div>
        <input class="drawer-cell-input" id="drawer-assignee" type="text" value="${escHtml(task.assignee || '')}" placeholder="—">
      </div>
      <div class="drawer-field">
        <div class="drawer-field-label">${t('task_col_start_date')}</div>
        <input class="drawer-cell-input drawer-date-input" id="drawer-start-date" type="date" value="${_toDateInput(task.start_date)}">
      </div>
      <div class="drawer-field">
        <div class="drawer-field-label">${t('task_col_end_date')}</div>
        <input class="drawer-cell-input drawer-date-input" id="drawer-end-date" type="date" value="${_toDateInput(task.end_date || task.deadline)}">
      </div>
    </div>
    <div class="drawer-field">
      <div class="drawer-field-label">${t('task_col_tags')}</div>
      <div class="drawer-tags-area" id="drawer-tags-area">
        ${(task.tags || []).map(tg => _parseTag(tg)).filter(Boolean).map(({name,color}) =>
          `<span class="drawer-tag" data-tag-name="${escHtml(name)}" style="background:${color}22;color:${color};border-color:${color}44">
            <button class="drawer-tag-color-btn" style="background:${color}" onclick="_showTagColorPicker(this,'${taskId}','${escHtml(name)}')" title="Cambiar color"></button>
            ${escHtml(name)}
            <button class="drawer-tag-del" data-tag="${escHtml(name)}">×</button>
          </span>`
        ).join('')}
        <input class="drawer-tag-input" id="drawer-tag-input" type="text" placeholder="${t('tags_placeholder')}">
      </div>
    </div>
    ${!_boardView && parentCandidates.length > 0 ? `
    <div class="drawer-field">
      <div class="drawer-field-label">${currentLang === 'en' ? 'Parent task' : 'Tarea padre'}</div>
      <select class="drawer-cell-input" id="drawer-parent-task">${parentOpts}</select>
    </div>` : ''}
    <div class="drawer-field">
      <div class="drawer-field-label">${t('task_col_description')}</div>
      <textarea class="drawer-desc-textarea" id="drawer-description" placeholder="${t('desc_placeholder')}">${escHtml(task.description || '')}</textarea>
    </div>
    ${task.meeting_path ? `
    <div class="drawer-field">
      <div class="drawer-field-label">${t('drawer_meeting_label')}</div>
      <button class="drawer-meeting-card" onclick="_openMeetingFromDrawer()">
        <span class="drawer-meeting-card-icon">📄</span>
        <span class="drawer-meeting-card-info">
          <span class="drawer-meeting-card-title">${escHtml(meetingMeta.title || t('task_from_meeting'))}</span>
          ${meetingMeta.date ? `<span class="drawer-meeting-card-date">${escHtml(meetingMeta.date)}</span>` : ''}
        </span>
        <span class="drawer-meeting-card-arrow">↗</span>
      </button>
    </div>` : ''}
    ${isClaudeExec ? `
    ${action.type === 'document_change' ? `
    <div class="drawer-field">
      <div class="drawer-field-label">${t('doc_file_label')}</div>
      <div class="drawer-dir-row">
        <input class="drawer-cell-input" id="drawer-doc-file" type="text"
          value="${escHtml(action.archivo || '')}" placeholder="${t('doc_file_ph')}">
        <button class="btn btn-ghost btn-sm" id="drawer-browse-file-btn">${t('doc_browse_file')}</button>
      </div>
    </div>` : ''}
    <div class="drawer-field" style="flex:1">
      <div class="drawer-prompt-label">${t('prompt_label')}</div>
      <textarea class="drawer-prompt-textarea" id="drawer-prompt">${escHtml(prompt)}</textarea>
    </div>
    <div class="drawer-claude-bar">
      <button class="btn btn-primary btn-sm" id="drawer-launch-btn" onclick="_launchFromDrawer()">${t('btn_launch')}</button>
    </div>` : ''}
    <div class="drawer-save-row">
      <button class="btn btn-ghost btn-sm" id="drawer-save-btn">${t('btn_save_task')}</button>
      <span class="drawer-last-edited" id="drawer-last-edited">
        ${task.last_edited ? `${t('last_edited_label')}: ${_fmtLastEdited(task.last_edited)}` : t('last_edited_never')}
      </span>
    </div>`;

  const _updateLastEditedUI = () => {
    const el = document.getElementById('drawer-last-edited');
    if (!el) return;
    const now = new Date().toISOString();
    el.textContent = `${t('last_edited_label')}: ${_fmtLastEdited(now)}`;
  };

  // Auto-save fields on change
  const saveField = async (field, getValue) => {
    const rawVal = getValue();
    const val = (rawVal === '' || rawVal === undefined) ? null : rawVal;
    const tk0 = _taskData.tasks.find(t => t.id === taskId);
    // Skip if value hasn't changed
    if (tk0 && (tk0[field] ?? null) === val) return;
    const update = { [field]: val };
    await pywebview.api.update_task(taskId, update);
    const tk = _taskData.tasks.find(t => t.id === taskId);
    if (tk) { tk[field] = val; tk.last_edited = new Date().toISOString(); }
    _refreshProjectCount(tk?.project_id);
    _updateLastEditedUI();
    // Sync back to table row
    const titleInput = document.querySelector(`[data-task-field="${taskId}:title"]`);
    if (field === 'title' && titleInput) { titleInput.value = val; titleInput.title = val; }
    if (field === 'status') {
      const statusSel = document.querySelector(`[data-status-task="${taskId}"]`);
      if (statusSel) { statusSel.value = val; statusSel.className = 'task-status-select ' + _statusCls(val); }
      _syncBucketFromStatus(tk, val);
      if (_boardView) renderKanbanBoard();
      refreshPendingBadge();
    }
  };

  const _drawerTitle = document.getElementById('drawer-title');
  if (_drawerTitle) {
    _drawerTitle.style.height = 'auto';
    _drawerTitle.style.height = _drawerTitle.scrollHeight + 'px';
    _drawerTitle.addEventListener('input', () => {
      _drawerTitle.style.height = 'auto';
      _drawerTitle.style.height = _drawerTitle.scrollHeight + 'px';
    });
    _drawerTitle.addEventListener('blur', () =>
      saveField('title', () => _drawerTitle.value.trim()));
  }
  document.getElementById('drawer-status')?.addEventListener('change', () =>
    saveField('status', () => document.getElementById('drawer-status').value));
  document.getElementById('drawer-priority')?.addEventListener('change', () =>
    saveField('priority', () => document.getElementById('drawer-priority').value || null));
  document.getElementById('drawer-assignee')?.addEventListener('blur', () =>
    saveField('assignee', () => document.getElementById('drawer-assignee').value.trim()));
  document.getElementById('drawer-start-date')?.addEventListener('change', () =>
    saveField('start_date', () => document.getElementById('drawer-start-date').value || null));
  document.getElementById('drawer-end-date')?.addEventListener('change', () =>
    saveField('end_date', () => document.getElementById('drawer-end-date').value || null));
  document.getElementById('drawer-description')?.addEventListener('blur', () =>
    saveField('description', () => document.getElementById('drawer-description').value.trim()));
  document.getElementById('drawer-save-btn')?.addEventListener('click', async () => {
    const btn = document.getElementById('drawer-save-btn');
    if (btn) btn.disabled = true;
    const titleVal = document.getElementById('drawer-title')?.value.trim();
    if (titleVal) await saveField('title', () => titleVal);
    await saveField('assignee', () => document.getElementById('drawer-assignee')?.value.trim());
    await saveField('description', () => document.getElementById('drawer-description')?.value.trim());
    if (btn) btn.disabled = false;
    if (_boardView) renderKanbanBoard();
  });
  document.getElementById('drawer-browse-file-btn')?.addEventListener('click', async () => {
    const filePath = await pywebview.api.pick_file();
    if (!filePath) return;
    const input = document.getElementById('drawer-doc-file');
    if (input) input.value = filePath;
  });
  document.getElementById('drawer-bucket')?.addEventListener('change', () => {
    const val = document.getElementById('drawer-bucket').value;
    saveField('bucket_id', () => val);
    if (_boardView) renderKanbanBoard();
  });
  document.getElementById('drawer-parent-task')?.addEventListener('change', async () => {
    const newParentId = document.getElementById('drawer-parent-task').value || null;
    const tk = _taskData.tasks.find(x => x.id === taskId);
    if (!tk) return;
    await pywebview.api.update_task(taskId, { parent_id: newParentId });
    tk.parent_id = newParentId;
    tk.last_edited = new Date().toISOString();
    renderTaskBoard();
    // Reopen drawer so it stays visible with updated data
    openTaskDetail(taskId);
  });

  // Tags
  const _saveTags = async () => {
    const tk = _taskData.tasks.find(t => t.id === taskId);
    if (!tk) return;
    await pywebview.api.update_task(taskId, { tags: tk.tags || [] });
    _updateLastEditedUI();
    if (_boardView) renderKanbanBoard();
  };
  document.getElementById('drawer-tags-area')?.addEventListener('click', async e => {
    const btn = e.target.closest('[data-tag]');
    if (!btn) return;
    const nameToRemove = btn.dataset.tag;
    const tk = _taskData.tasks.find(t => t.id === taskId);
    if (!tk) return;
    tk.tags = (tk.tags || []).filter(tg => { const p = _parseTag(tg); return p && p.name !== nameToRemove; });
    btn.closest('.drawer-tag')?.remove();
    await _saveTags();
  });
  document.getElementById('drawer-tag-input')?.addEventListener('keydown', async e => {
    if (e.key !== 'Enter' && e.key !== ',') return;
    e.preventDefault();
    const input = e.target;
    const newName = input.value.trim().replace(/,$/, '');
    if (!newName) return;
    const tk = _taskData.tasks.find(t => t.id === taskId);
    if (!tk) return;
    tk.tags = tk.tags || [];
    const exists = tk.tags.some(tg => { const p = _parseTag(tg); return p && p.name === newName; });
    if (!exists) {
      const color = _nextTagColor();
      const newTag = {name: newName, color};
      tk.tags.push(newTag);
      const area = document.getElementById('drawer-tags-area');
      const pill = document.createElement('span');
      pill.className = 'drawer-tag';
      pill.dataset.tagName = newName;
      pill.style.cssText = `background:${color}22;color:${color};border-color:${color}44`;
      pill.innerHTML = `<button class="drawer-tag-color-btn" style="background:${color}" onclick="_showTagColorPicker(this,'${taskId}','${escHtml(newName)}')" title="Cambiar color"></button>${escHtml(newName)}<button class="drawer-tag-del" data-tag="${escHtml(newName)}">×</button>`;
      area.insertBefore(pill, input);
      await _saveTags();
    }
    input.value = '';
  });

  // Save prompt to meeting JSON on blur
  if (isClaudeExec && task.meeting_path) {
    document.getElementById('drawer-prompt')?.addEventListener('blur', () => {
      const val = document.getElementById('drawer-prompt')?.value || '';
      savePrompt(task.meeting_path, task.meeting_action_index, val);
    });
  }
}

async function _launchFromDrawer() {
  const task = _taskData.tasks.find(tk => tk.id === _drawerTaskId);
  if (!task?.meeting_path) return;
  const prompt = document.getElementById('drawer-prompt')?.value || '';
  await savePrompt(task.meeting_path, task.meeting_action_index, prompt);
  const docFileInput = document.getElementById('drawer-doc-file');
  let workingDir;
  if (docFileInput && docFileInput.value.trim()) {
    workingDir = docFileInput.value.trim();
  } else {
    workingDir = await pywebview.api.get_action_working_dir(task.meeting_path, task.meeting_action_index);
  }
  closeTaskDetail();
  openRunsPanel();
  await globalLaunchRun(task.meeting_path, task.meeting_action_index, workingDir, prompt);
}

function closeTaskDetail() {
  document.getElementById('task-detail-drawer')?.classList.remove('open');
  document.getElementById('task-drawer-backdrop')?.classList.remove('open');
  _drawerTaskId = null;
}

// Devuelve {title, date} de la reunión asociada a partir de su ruta .md
function _meetingMetaFromPath(path) {
  const m = (allMeetings || []).find(mm => mm.path === path);
  if (m) return { title: m.title, date: m.date };
  const base = String(path).split(/[\\/]/).pop().replace(/\.md$/i, '');
  const mm = base.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_(.*)$/);
  if (mm) return { title: mm[6].replace(/_/g, ' '), date: `${mm[1]}-${mm[2]}-${mm[3]}` };
  return { title: base, date: '' };
}

// Abre la reunión asociada a la tarea del drawer (sin inyectar la ruta en el HTML)
function _openMeetingFromDrawer() {
  const task = _taskData.tasks.find(tk => tk.id === _drawerTaskId);
  if (!task?.meeting_path) return;
  closeTaskDetail();
  showView('meetings');
  openMeeting(task.meeting_path);
}

// ── Recording settings ────────────────────────────────────────────────────────

function renderWhisperOptions(selectedModel) {
  const sel = document.getElementById('whisper-model-select');
  if (!sel) return;
  const models = [
    { value: 'tiny',     key: 'whisper_tiny' },
    { value: 'base',     key: 'whisper_base' },
    { value: 'small',    key: 'whisper_small' },
    { value: 'medium',   key: 'whisper_medium' },
    { value: 'large-v3', key: 'whisper_large' },
  ];
  const cur = selectedModel || sel.value || 'medium';
  sel.innerHTML = models.map(m =>
    `<option value="${m.value}"${cur === m.value ? ' selected' : ''}>${t(m.key)}</option>`
  ).join('');
}

async function loadRecordingSettings() {
  try {
    const s = await pywebview.api.get_settings();
    renderWhisperOptions(s.whisper_model || 'medium');
    const enabled = s.teams_chat_notice_enabled === true;
    const cb = document.getElementById('chat-notice-enabled');
    const ta = document.getElementById('chat-notice-input');
    if (cb) cb.checked = enabled;
    if (ta) {
      ta.value = s.teams_chat_message ?? '';
      ta.disabled = !enabled;
    }
  } catch (e) {
    renderWhisperOptions('medium');
  }
}

async function saveCoachingEnabled(enabled) {
  await pywebview.api.save_settings({ coaching_enabled: enabled });
  showToast(t('settings_saved'));
}


async function saveChatNoticeEnabled(enabled) {
  const ta = document.getElementById('chat-notice-input');
  if (ta) ta.disabled = !enabled;
  await pywebview.api.save_settings({ teams_chat_notice_enabled: enabled });
  showToast(t('settings_saved'));
}

async function saveChatNoticeSettings() {
  const cb = document.getElementById('chat-notice-enabled');
  const ta = document.getElementById('chat-notice-input');
  await pywebview.api.save_settings({
    teams_chat_notice_enabled: cb?.checked ?? false,
    teams_chat_message: ta?.value ?? '',
  });
  showToast(t('settings_saved'));
}

async function saveWhisperModel(model) {
  await pywebview.api.save_settings({ whisper_model: model });
  showToast(t('toast_model_saved'));
}

// ── Per-project field save ────────────────────────────────────────────────────

const _projectSaveTimers = {};

function scheduleProjectSave(pid) {
  clearTimeout(_projectSaveTimers[pid]);
  _projectSaveTimers[pid] = setTimeout(() => saveProjectFields(pid), 800);
}

async function saveProjectFields(pid) {
  const card = document.querySelector(`.project-settings-item[data-proj-id="${pid}"]`);
  if (!card) return;
  const projects = await pywebview.api.get_projects();
  const proj = projects.find(p => p.id === pid);
  if (!proj) return;
  card.querySelectorAll('[data-proj-field]').forEach(input => {
    const field = input.dataset.projField;
    if (field === 'stakeholders') {
      proj.stakeholders = input.value.split(',').map(s => s.trim()).filter(Boolean);
    } else {
      proj[field] = input.value.trim();
    }
  });
  await pywebview.api.save_project(proj);
}

async function openProjectDir(dir) {
  if (!dir || !dir.trim()) return;
  await pywebview.api.open_project_dir(dir.trim());
}

// ── Project settings management ───────────────────────────────────────────────

let _newProjectFolder = '';
let _newProjectContextDirs = [];
let _newProjectColor = '';
let _editingProjectId = null;         // proyecto actualmente en modo edición (o null)
const _expandedProjects = new Set();  // ids de proyectos con el detalle desplegado

function toggleColorPicker(pid) {
  const picker = document.getElementById(`color-picker-${pid}`);
  if (!picker) return;
  picker.style.display = picker.style.display === 'none' ? 'flex' : 'none';
}

function selectProjectColor(swatch, pid) {
  const picker = document.getElementById(`color-picker-${pid}`);
  if (picker) {
    picker.querySelectorAll('.proj-color-swatch').forEach(s => s.classList.remove('selected'));
    swatch.classList.add('selected');
    picker.style.display = 'none';
  }
  const trigger = document.querySelector(`[data-color-trigger="${pid}"]`);
  if (trigger) { trigger.style.background = swatch.dataset.color; trigger.dataset.color = swatch.dataset.color; }
}

function toggleProjectDetail(pid) {
  const card = document.querySelector(`.project-settings-item[data-proj-id="${pid}"]`);
  if (!card) return;
  const detail = card.querySelector('.project-settings-detail');
  const chev = card.querySelector('.project-chevron');
  const willExpand = !_expandedProjects.has(pid);
  if (willExpand) _expandedProjects.add(pid); else _expandedProjects.delete(pid);
  if (detail) detail.style.display = willExpand ? '' : 'none';
  if (chev) chev.textContent = willExpand ? '▾' : '▸';
  card.classList.toggle('expanded', willExpand);
}

async function loadProjectsSettings(editingId = null) {
  _editingProjectId = editingId;
  const projects = await pywebview.api.get_projects();
  const list = document.getElementById('projects-list-settings');
  if (!list) return;
  if (!projects.length) {
    list.innerHTML = `<div style="font-size:12px;color:var(--muted);padding:4px 0">${t('no_projects')}</div>`;
    return;
  }
  list.innerHTML = projects.map(p => {
    const pid = escHtml(p.id);
    const hasFolder = !!(p.output_dir && p.output_dir.trim());
    const isEditing = editingId === pid;
    if (isEditing) _expandedProjects.add(p.id);  // mantener abierto al editar
    const isExpanded = isEditing || _expandedProjects.has(p.id);

    const color = _projectColor(p);
    const colorSwatches = PROJECT_COLORS.map(c =>
      `<button class="proj-color-swatch${c === color ? ' selected' : ''}" data-color="${c}" style="background:${c}" onclick="selectProjectColor(this,'${pid}')" title="${c}"></button>`
    ).join('');

    const headerRow = isEditing ? `
      <div style="display:flex;flex-direction:column;gap:6px">
        <div style="display:flex;align-items:center;gap:6px">
          <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:6px">
            <input class="settings-text-input" id="edit-proj-name-${pid}" value="${escHtml(p.name)}" style="font-size:13px;font-weight:600">
            <textarea class="settings-text-input proj-desc-textarea" id="edit-proj-desc-${pid}" placeholder="${t('proj_desc_ph')}" rows="2" oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'">${escHtml(p.description || '')}</textarea>
          </div>
          <div style="display:flex;flex-direction:column;gap:4px;flex-shrink:0">
            <button class="btn btn-primary btn-sm" onclick="saveEditProject('${pid}')">${t('save_btn')}</button>
            <button class="btn btn-ghost btn-sm" onclick="loadProjectsSettings()">${t('cancel_btn')}</button>
          </div>
        </div>
      </div>
    ` : `
      <div class="project-settings-row" onclick="toggleProjectDetail('${pid}')">
        <div style="min-width:0;flex:1">
          <div class="project-settings-name">${escHtml(p.name)}</div>
          ${p.description ? `<div class="project-settings-desc">${escHtml(p.description)}</div>` : ''}
        </div>
        <div class="proj-header-actions">
          <button class="proj-edit-btn btn btn-ghost btn-sm" onclick="event.stopPropagation();loadProjectsSettings('${pid}')">${t('edit_btn')}</button>
          <button class="proj-del-btn btn btn-ghost btn-sm" onclick="event.stopPropagation();deleteProject('${pid}')">${t('delete_project_btn') || 'Delete'}</button>
        </div>
        <span class="project-chevron">${isExpanded ? '▾' : '▸'}</span>
      </div>
    `;

    const detailActions = '';

    const fieldsDisabled = isEditing ? '' : 'disabled';
    const canEditExport = isEditing && hasFolder;
    const boxChecked = f => (hasFolder && p['export_save_' + f] !== false) ? 'checked' : '';

    return `
    <div class="project-settings-item${isExpanded ? ' expanded' : ''}" data-proj-id="${pid}" style="border-left-color:${color}">
      ${headerRow}
      <div class="project-settings-detail"${isExpanded ? '' : ' style="display:none"'}>
        ${isEditing ? `
        <div style="margin-top:4px;margin-bottom:4px">
          <div class="proj-field-label" style="margin-bottom:6px">${t('proj_color_label')}</div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <button class="proj-color-swatch" data-color-trigger="${pid}" data-color="${color}" style="background:${color}" onclick="toggleColorPicker('${pid}')" title="${t('proj_color_label')}"></button>
            <div class="proj-color-picker" id="color-picker-${pid}" style="display:none">${colorSwatches}</div>
          </div>
        </div>` : ''}
        <div style="margin-top:8px">
          <div class="proj-field-label">${t('proj_stake_label')}</div>
          <div class="settings-card-desc" style="margin:3px 0 5px">${t('proj_stake_desc')}</div>
          <input class="settings-text-input" value="${escHtml((p.stakeholders||[]).join(', '))}"
            data-proj-field="stakeholders" ${fieldsDisabled}>
        </div>
        <div style="margin-top:12px">
          <div class="proj-field-label">${t('proj_dir_label')}</div>
          <div class="settings-card-desc" style="margin:3px 0 5px">${t('proj_dir_desc')}</div>
          <div class="proj-folder-row">
            <span class="proj-folder-path ${hasFolder ? '' : 'empty'}" id="proj-folder-display-${pid}">${hasFolder ? escHtml(p.output_dir) : t('proj_folder_default')}</span>
            ${isEditing ? `<button class="btn btn-ghost btn-sm" onclick="browseProjectFolder('${pid}')">${t('proj_folder_browse')}</button>` : ''}
            ${isEditing && hasFolder ? `<button class="btn btn-ghost btn-sm" onclick="clearProjectFolder('${pid}')" title="${t('proj_folder_clear')}">✕</button>` : ''}
          </div>
        </div>
        <div style="margin-top:12px">
          <div class="proj-field-label">${t('export_settings_title')}</div>
          <div class="settings-card-desc" style="margin:3px 0 8px">${t('export_settings_desc_project')}</div>
          <div style="display:flex;flex-direction:column;gap:8px">
            <label class="proj-export-check${canEditExport ? '' : ' disabled'}">
              <input type="checkbox" data-export-field="html" ${boxChecked('html')} ${canEditExport ? '' : 'disabled'}>
              <span data-i18n="export_html">${t('export_html')}</span>
            </label>
            <label class="proj-export-check${canEditExport ? '' : ' disabled'}">
              <input type="checkbox" data-export-field="email" ${boxChecked('email')} ${canEditExport ? '' : 'disabled'}>
              <span data-i18n="export_email">${t('export_email')}</span>
            </label>
            <label class="proj-export-check${canEditExport ? '' : ' disabled'}">
              <input type="checkbox" data-export-field="transcript" ${boxChecked('transcript')} ${canEditExport ? '' : 'disabled'}>
              <span data-i18n="export_transcript">${t('export_transcript')}</span>
            </label>
            <label class="proj-export-check${canEditExport ? '' : ' disabled'}">
              <input type="checkbox" data-export-field="pdf" ${boxChecked('pdf')} ${canEditExport ? '' : 'disabled'}>
              <span data-i18n="export_pdf">${t('export_pdf')}</span>
            </label>
          </div>
          ${!hasFolder ? `<div class="settings-card-desc" style="margin-top:6px;color:var(--muted)">${t('export_needs_folder')}</div>` : ''}
        </div>
        <div style="margin-top:12px">
          <div class="proj-field-label">${t('proj_context_label')}</div>
          <div class="settings-card-desc" style="margin:3px 0 7px">${t('proj_context_desc')}</div>
          <div class="proj-context-dirs" id="context-dirs-${pid}">
            ${(p.context_dirs || []).length
              ? (p.context_dirs || []).map((d, i) => `
                <div class="proj-context-dir-row">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:.5"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/></svg>
                  <span class="proj-context-dir-path" title="${escHtml(d)}">${escHtml(d)}</span>
                  ${isEditing ? `<button class="proj-context-dir-del" onclick="removeContextDir('${pid}',${i})" title="Quitar">✕</button>` : ''}
                </div>`).join('')
              : `<div class="proj-context-dir-empty">${t('proj_context_empty')}</div>`}
          </div>
          ${isEditing ? `<button class="btn btn-ghost btn-sm" style="margin-top:6px" onclick="browseContextDir('${pid}')">${t('proj_context_add')}</button>` : ''}
        </div>
        ${detailActions}
      </div>
    </div>`;
  }).join('');
}

async function saveEditProject(pid) {
  const nameInput = document.getElementById(`edit-proj-name-${pid}`);
  const descInput = document.getElementById(`edit-proj-desc-${pid}`);
  if (!nameInput) return;
  const name = nameInput.value.trim();
  if (!name) return;
  const projects = await pywebview.api.get_projects();
  const proj = projects.find(p => p.id === pid);
  if (!proj) return;
  proj.name = name;
  proj.description = descInput ? descInput.value.trim() : '';

  const card = document.querySelector(`.project-settings-item[data-proj-id="${pid}"]`);
  if (card) {
    // Color
    const trigger = card.querySelector('[data-color-trigger]');
    if (trigger) proj.color = trigger.dataset.color;
    // Stakeholders
    const stakeInput = card.querySelector('[data-proj-field="stakeholders"]');
    if (stakeInput) {
      proj.stakeholders = stakeInput.value.split(',').map(s => s.trim()).filter(Boolean);
    }
    // Export flags: solo si hay carpeta configurada (si no, se preservan)
    const hasFolder = !!(proj.output_dir && proj.output_dir.trim());
    if (hasFolder) {
      const get = f => card.querySelector(`[data-export-field="${f}"]`);
      if (get('html'))       proj.export_save_html       = get('html').checked;
      if (get('email'))      proj.export_save_email      = get('email').checked;
      if (get('transcript')) proj.export_save_transcript = get('transcript').checked;
      if (get('pdf'))        proj.export_save_pdf        = get('pdf').checked;
    }
  }

  await pywebview.api.save_project(proj);
  await loadProjectsSettings();
}

function showAddProjectForm() {
  _newProjectContextDirs = [];
  _newProjectColor = PROJECT_COLORS[0];
  // Render color swatches
  const picker = document.getElementById('add-proj-color-picker');
  const trigger = document.getElementById('add-proj-color-trigger');
  if (picker) picker.innerHTML = PROJECT_COLORS.map(c =>
    `<button class="proj-color-swatch${c === _newProjectColor ? ' selected' : ''}" data-color="${c}" style="background:${c}" onclick="selectNewProjectColor(this,'${c}')" type="button" title="${c}"></button>`
  ).join('');
  if (trigger) { trigger.style.background = _newProjectColor; }
  _renderNewContextDirs();
  document.getElementById('add-project-form').style.display = 'block';
  document.getElementById('proj-name').focus();
}

function hideAddProjectForm() {
  document.getElementById('add-project-form').style.display = 'none';
  document.getElementById('proj-name').value = '';
  document.getElementById('proj-desc').value = '';
  document.getElementById('proj-stakeholders').value = '';
  _newProjectFolder = '';
  _newProjectContextDirs = [];
  _newProjectColor = '';
  const display = document.getElementById('add-proj-folder-display');
  if (display) { display.textContent = t('proj_folder_default'); display.classList.add('empty'); }
  const ctxList = document.getElementById('add-proj-context-dirs');
  if (ctxList) ctxList.innerHTML = '';
  const picker = document.getElementById('add-proj-color-picker');
  if (picker) { picker.innerHTML = ''; picker.style.display = 'none'; }
}

async function saveNewProject() {
  const name = document.getElementById('proj-name').value.trim();
  if (!name) return;
  const description = document.getElementById('proj-desc').value.trim();
  const stakeholders = document.getElementById('proj-stakeholders').value
    .split(',').map(s => s.trim()).filter(Boolean);
  await pywebview.api.save_project({
    name, description, stakeholders,
    output_dir: _newProjectFolder,
    color: _newProjectColor || PROJECT_COLORS[0],
    context_dirs: [..._newProjectContextDirs],
  });
  hideAddProjectForm();
  await loadProjectsSettings();
}

// ── Modal renombrar reunión ───────────────────────────────────────────────────

let _renameCallback = null;

function openRenameModal(currentName, onConfirm) {
  _renameCallback = onConfirm;
  const input = document.getElementById('rename-modal-input');
  if (input) { input.value = currentName; }
  document.getElementById('rename-modal').classList.remove('hidden');
  setTimeout(() => { input?.select(); }, 60);
}

function closeRenameModal() {
  document.getElementById('rename-modal').classList.add('hidden');
  _renameCallback = null;
}

async function confirmRenameModal() {
  const input = document.getElementById('rename-modal-input');
  const val = input?.value?.trim();
  if (!val) return;
  closeRenameModal();
  if (_renameCallback) await _renameCallback(val);
}

// ── Modal de confirmación genérico ────────────────────────────────────────────

let _confirmCallback = null;

function openConfirmModal(message, onConfirm, { title = '', okLabel = '' } = {}) {
  _confirmCallback = onConfirm;
  const msgEl = document.getElementById('confirm-modal-msg');
  if (msgEl) msgEl.textContent = message;
  const titleEl = document.getElementById('confirm-modal-title');
  if (titleEl) titleEl.textContent = title || t('confirm_delete_title');
  const okBtn = document.getElementById('confirm-modal-ok');
  if (okBtn) {
    okBtn.textContent = okLabel || t('btn_delete');
    okBtn.onclick = () => {
      const cb = _confirmCallback;
      closeConfirmModal();
      if (cb) cb();
    };
  }
  document.getElementById('confirm-modal').classList.remove('hidden');
}

function closeConfirmModal() {
  _confirmCallback = null;
  document.getElementById('confirm-modal').classList.add('hidden');
}

async function deleteProject(id) {
  let name = '';
  try {
    const projects = await pywebview.api.get_projects();
    const proj = projects.find(p => p.id === id);
    if (proj) name = proj.name;
  } catch (_) {}
  openConfirmModal(
    t('confirm_delete_project', name),
    async () => {
      await pywebview.api.delete_project(id);
      allProjects = await pywebview.api.get_projects();
      await loadProjectsSettings();
    },
    { title: t('confirm_delete_title') },
  );
}

async function browseProjectFolder(pid) {
  const path = await pywebview.api.browse_project_folder();
  if (!path) return;
  const projects = await pywebview.api.get_projects();
  const proj = projects.find(p => p.id === pid);
  if (!proj) return;
  proj.output_dir = path;
  await pywebview.api.save_project(proj);
  await loadProjectsSettings(_editingProjectId);
}

async function browseContextDir(pid) {
  const path = await pywebview.api.browse_project_folder();
  if (!path) return;
  const projects = await pywebview.api.get_projects();
  const proj = projects.find(p => p.id === pid);
  if (!proj) return;
  proj.context_dirs = proj.context_dirs || [];
  if (!proj.context_dirs.includes(path)) {
    proj.context_dirs.push(path);
    await pywebview.api.save_project(proj);
    await loadProjectsSettings(_editingProjectId);
  }
}

async function removeContextDir(pid, idx) {
  const projects = await pywebview.api.get_projects();
  const proj = projects.find(p => p.id === pid);
  if (!proj) return;
  proj.context_dirs = (proj.context_dirs || []).filter((_, i) => i !== idx);
  await pywebview.api.save_project(proj);
  await loadProjectsSettings(_editingProjectId);
}


async function clearProjectFolder(pid) {
  const projects = await pywebview.api.get_projects();
  const proj = projects.find(p => p.id === pid);
  if (!proj) return;
  proj.output_dir = '';
  await pywebview.api.save_project(proj);
  await loadProjectsSettings(_editingProjectId);  // mantener el modo edición si estaba activo
}

async function browseNewProjectFolder() {
  const path = await pywebview.api.browse_project_folder();
  if (!path) return;
  _newProjectFolder = path;
  const display = document.getElementById('add-proj-folder-display');
  if (display) { display.textContent = path; display.classList.remove('empty'); }
}

async function browseNewContextDir() {
  const path = await pywebview.api.browse_project_folder();
  if (!path || _newProjectContextDirs.includes(path)) return;
  _newProjectContextDirs.push(path);
  _renderNewContextDirs();
}

function removeNewContextDir(idx) {
  _newProjectContextDirs.splice(idx, 1);
  _renderNewContextDirs();
}

function _renderNewContextDirs() {
  const el = document.getElementById('add-proj-context-dirs');
  if (!el) return;
  if (!_newProjectContextDirs.length) {
    el.innerHTML = `<div class="proj-context-dir-empty">${t('proj_context_empty')}</div>`;
    return;
  }
  el.innerHTML = _newProjectContextDirs.map((d, i) => `
    <div class="proj-context-dir-row">
      <span class="proj-context-dir-path" title="${escHtml(d)}">${escHtml(d)}</span>
      <button class="proj-context-dir-del" onclick="removeNewContextDir(${i})" type="button" title="Quitar">✕</button>
    </div>`).join('');
}

function selectNewProjectColor(swatch, color) {
  _newProjectColor = color;
  document.querySelectorAll('#add-proj-color-picker .proj-color-swatch').forEach(s => s.classList.remove('selected'));
  swatch.classList.add('selected');
  const trigger = document.getElementById('add-proj-color-trigger');
  if (trigger) trigger.style.background = color;
  document.getElementById('add-proj-color-picker').style.display = 'none';
}

function toggleNewProjectColorPicker() {
  const picker = document.getElementById('add-proj-color-picker');
  if (picker) picker.style.display = picker.style.display === 'none' ? 'flex' : 'none';
}

// ── Parseo de fechas de deadline ──────────────────────────────────────────────

const _MONTHS = {
  jan:1, january:1, ene:1, enero:1,
  feb:2, february:2, febrero:2,
  mar:3, march:3, marzo:3,
  apr:4, april:4, abr:4, abril:4,
  may:5, mayo:5,
  jun:6, june:6, junio:6,
  jul:7, july:7, julio:7,
  aug:8, august:8, ago:8, agosto:8,
  sep:9, sept:9, september:9, septiembre:9,
  oct:10, october:10, octubre:10,
  nov:11, november:11, noviembre:11,
  dec:12, december:12, dic:12, diciembre:12,
};

// ── Grupos de deadline ────────────────────────────────────────────────────────
// Grupo 1 → '0000-00-00'  ASAP / inmediato (siempre primero)
// Grupo 2 → 'YYYY-MM-DD'  Fecha real parseada
// Grupo 3 → '8888-88-88'  Relativo sin fecha concreta (esta semana, antes de X…)
// Grupo 4 → '9999-99-99'  Indefinido / sin fecha (TBD, por definir, ongoing…)

const _ASAP_TERMS = [
  'lo antes posible', 'cuanto antes', 'asap', 'as soon as possible',
  'urgente', 'urgent', 'inmediato', 'inmediatamente', 'immediate', 'immediately',
  'same session', 'prioritario', 'priority',
  'hoy', 'today', '~1 day', '1 day',
];

const _RELATIVE_TERMS = [
  'esta semana', 'this week', 'next week', 'la semana que viene',
  'antes de reunión', 'antes de la reunión', 'before the meeting',
  'after meeting', 'after speaking', 'after the call', 'en call', 'en la call',
  'with quick wins', 'proactively', '~3 weeks', '~2 weeks', 'weeks', 'semanas',
];

const _UNDEFINED_TERMS = [
  'tbd', 'por definir', 'en curso', 'ongoing', 'no deadline', 'none',
  'no date', 'sin fecha', 'pendiente', 'internal discussion',
  'structured approval', 'no hard date',
];

function parseDeadlineSortKey(str) {
  if (!str) return '9999-99-99';
  const s = str.toLowerCase().trim();

  // Grupo 1: ASAP
  if (_ASAP_TERMS.some(term => s.includes(term))) return '0000-00-00';

  // ISO directo
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;

  // Grupo 2: intentar extraer fecha real
  const yearM = s.match(/\b(20\d{2})\b/);
  const year  = yearM ? yearM[1] : String(new Date().getFullYear());

  const monthKeys = Object.keys(_MONTHS).sort((a, b) => b.length - a.length);
  let month = null;
  for (const key of monthKeys) {
    if (s.includes(key)) { month = _MONTHS[key]; break; }
  }

  if (month) {
    const sNoYear = s.replace(/\b20\d{2}\b/g, '');
    const dayM    = sNoYear.match(/\b([12]?\d|3[01])\b/);
    let day = dayM ? parseInt(dayM[1]) : 1;
    if (day < 1 || day > 31) day = 1;
    if (/\bend\b/.test(s)) day = 28;
    return `${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
  }

  // Grupo 3: relativo sin fecha
  if (_RELATIVE_TERMS.some(term => s.includes(term))) return '8888-88-88';

  // Grupo 4: indefinido (default)
  return '9999-99-99';
}

// ── Acciones ──────────────────────────────────────────────────────────────────

let _pendingPanelAction = null;

async function moveToPanel(path, index, btn) {
  const data = await pywebview.api.get_tasks();
  const projSelect = document.getElementById('modal-project-select');
  projSelect.innerHTML = (data.projects || []).map(p =>
    `<option value="${escHtml(p.id)}">${escHtml(p.name)}</option>`
  ).join('') + `<option value="none">${t('task_no_project')}</option>`;
  const meeting = allMeetings.find(m => m.path === path);
  if (meeting?.project_id) projSelect.value = meeting.project_id;
  _populateParentSelect(projSelect.value, data.tasks || []);
  projSelect.onchange = () => _populateParentSelect(projSelect.value, data.tasks || []);
  _pendingPanelAction = { path, index, btn };
  document.getElementById('panel-modal').classList.remove('hidden');
}

function _populateParentSelect(projectId, tasks) {
  const sel = document.getElementById('modal-parent-select');
  const top = tasks.filter(tk => tk.project_id === projectId && !tk.parent_id);
  sel.innerHTML = `<option value="">${t('modal_parent_none')}</option>` +
    top.map(tk => `<option value="${escHtml(tk.id)}">${escHtml(tk.title)}</option>`).join('');
}

function closePanelModal() {
  document.getElementById('panel-modal').classList.add('hidden');
  _pendingPanelAction = null;
}

async function confirmMoveToPanel() {
  if (!_pendingPanelAction) return;
  const { path, index, btn } = _pendingPanelAction;
  const projectId = document.getElementById('modal-project-select').value;
  const parentId  = document.getElementById('modal-parent-select').value;
  closePanelModal();
  const taskId = await pywebview.api.move_action_to_panel(path, index, projectId, parentId || null, null);
  if (taskId) {
    btn.textContent = t('btn_in_panel');
    btn.classList.add('btn-in-panel');
    btn.disabled = true;
    await refreshPendingBadge();
    showToast(t('toast_moved'));
    showView('actions');
    await loadTaskBoard();
  }
}

function deleteAction(path, index, title) {
  openConfirmModal(
    t('confirm_delete_action', title || ''),
    async () => {
      await pywebview.api.delete_action(path, index);
      const card = document.getElementById('card-' + index);
      if (card) card.remove();
      showToast(t('toast_deleted'));
    },
    { title: t('confirm_delete_action_title'), okLabel: t('btn_delete') }
  );
}

async function browseRunDir(index) {
  const folder = await pywebview.api.pick_folder();
  if (folder) {
    const el = document.getElementById('dir-' + index);
    if (el) { el.value = folder; _showDirHint(index, folder); }
  }
}

async function browseRunFile(index) {
  const filePath = await pywebview.api.pick_file();
  if (!filePath) return;
  const dirEl = document.getElementById('dir-' + index);
  if (dirEl) dirEl.value = filePath;
  _showDirHint(index, filePath);
  // Inject the concrete file path at the top of the prompt so Claude knows exactly where the file is
  const promptEl = document.getElementById('prompt-' + index);
  if (promptEl && !promptEl.value.includes(filePath)) {
    promptEl.value = `Archivo: ${filePath}\n\n${promptEl.value}`;
    if (currentPath) savePrompt(currentPath, index, promptEl.value);
  }
}

async function launchRun(path, index) {
  const dirInput  = document.getElementById('dir-' + index);
  const promptEl  = document.getElementById('prompt-' + index);
  const workingDir = dirInput  ? dirInput.value.trim()  : '';
  let prompt       = promptEl  ? promptEl.value.trim()  : '';
  // For document_change: if user typed a file path directly (not via browse button which already injects it),
  // prepend the path so Claude knows exactly which file to work on
  if (workingDir && document.querySelector(`[data-browse-file="${index}"]`) && !prompt.includes(workingDir)) {
    prompt = `File: ${workingDir}\n\n${prompt}`;
  }
  const runId = await pywebview.api.execute_action_panel(path, index, workingDir, prompt);
  if (runId) {
    addRunCard(runId);
    openRunsPanel();
    // Close card body
    document.getElementById('body-' + index)?.classList.remove('open');
  } else {
    showToast(t('toast_terminal'));
    await pywebview.api.execute_action(path, index);
  }
}


async function globalLaunchRun(path, index, workingDir, prompt) {
  const runId = await pywebview.api.execute_action_panel(path, index, workingDir, prompt);
  if (runId) {
    addRunCard(runId);
    openRunsPanel();
  } else {
    showToast(t('toast_terminal'));
    await pywebview.api.execute_action(path, index);
  }
}

async function markDone(path, index) {
  await pywebview.api.mark_done(path, index);
  const card = document.getElementById('card-' + index);
  if (card) {
    card.classList.add('done');
    card.querySelector('.action-title')?.classList.add('done-text');
    const execBtn = card.querySelector('[data-exec]');
    if (execBtn) { execBtn.textContent = t('btn_reexecute'); execBtn.className = 'btn btn-ghost btn-sm'; }
    const doneBtn = card.querySelector('[data-done]');
    if (doneBtn) { doneBtn.textContent = t('btn_done_state'); doneBtn.style.opacity = '0.5'; }
  }
  await refreshPendingBadge();
  showToast(t('toast_done'));
}

async function globalMarkDone(path, index, gi) {
  await pywebview.api.mark_done(path, index);

  // Sync in-memory state so filter re-renders (pending↔done) stay consistent
  const action = allPending.find(a => a.minutes_path === path && a.index === index);
  if (action) action.executed = true;

  const item = document.getElementById('gitem-' + gi);
  if (item) {
    item.classList.add('done');
    item.querySelector('.global-action-title')?.classList.add('done-text');
    const btn = item.querySelector('[data-gcheck]');
    if (btn) { btn.textContent = t('btn_done_state'); btn.style.opacity = '0.5'; }
  }
  await refreshPendingBadge();
  showToast(t('toast_done'));
}

async function savePrompt(path, index, value) {
  await pywebview.api.update_prompt(path, index, value);
}

async function reenrichMeeting(path) {
  const btn = document.getElementById('btn-reenrich');
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  const actionsDiv = document.getElementById('meeting-actions');
  if (actionsDiv) actionsDiv.innerHTML = `<div style="color:var(--muted);font-size:13px">${t('toast_enriching')}</div>`;
  showToast(t('toast_enriching'));
  await pywebview.api.enrich_actions(path);
  // Espera hasta 60s a que Claude termine el análisis
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const actions = await pywebview.api.get_actions(path);
    if (actions && actions.length > 0) {
      const mtg = allMeetings.find(m => m.path === path) || {};
      renderActionCards(actions, path, actionsDiv, mtg.date || '');
      _renderAddActionBtn(path, actionsDiv);
      const count = document.querySelector('.actions-count');
      if (count) count.textContent = t('n_total', actions.length);
      if (btn) { btn.disabled = false; btn.innerHTML = `↺ ${t('btn_reenrich')}`; }
      return;
    }
  }
  await openMeeting(path);
}

async function sendEmail(path) {
  showToast(t('toast_outlook'));
  await pywebview.api.send_email(path);
}

async function sendActionsEmail(path) {
  try {
    const meeting = allMeetings.find(m => m.path === path) || {};
    const actions = await pywebview.api.get_actions(path);
    showToast(t('toast_outlook'));
    await pywebview.api.send_actions_email(path, meeting.title || '', meeting.date || '', actions || []);
  } catch (e) { showToast('Error: ' + e); }
}

async function sendTranscriptEmail(path) {
  try {
    const meeting = allMeetings.find(m => m.path === path) || {};
    showToast(t('toast_outlook'));
    await pywebview.api.send_transcript_email(path, meeting.title || '', meeting.date || '');
  } catch (e) { showToast('Error: ' + e); }
}

async function onMeetingProjectChange(projectId) {
  if (!currentPath) return;
  await pywebview.api.set_meeting_project(currentPath, projectId);
  const m = allMeetings.find(m => m.path === currentPath);
  if (m) m.project_id = projectId;
  renderSidebar(allMeetings);
  showToast(t('toast_project_set'));

  if (projectId && projectId !== 'none') {
    const result = await pywebview.api.export_to_project(currentPath);
    if (result === '') showToast(t('toast_export_ok'));
    else if (result === 'no_directory') showToast(t('toast_export_no_dir'));
  }
}

async function detectProjectsForAll() {
  showToast(t('toast_detecting'));
  await pywebview.api.detect_projects_for_all();
  // Reload meetings after a short delay to pick up new assignments
  setTimeout(async () => {
    const meetings = await pywebview.api.get_meetings();
    allMeetings = meetings;
    meetings.forEach((m, i) => { meetingPaths[i] = m.path; });
    renderSidebar(meetings);
  }, 3000);
}

async function openHtml(path) {
  await pywebview.api.open_html(path);
}

// ── Semantic Layer (skill externa ontoforge-meetings, vía claude -p) ────────────

async function runSemanticLayer(path, title, extraDocs) {
  let result;
  try {
    result = await pywebview.api.run_semantic_layer(path, title || '', extraDocs || []);
  } catch (e) {
    showToast(t('semantic_error'));
    return;
  }
  if (!result || !result.ok) {
    const err = result && result.error;
    if (err === 'no_transcript')   showToast(t('semantic_no_transcript'));
    else if (err === 'no_skill')   showToast(t('semantic_no_skill'));
    else if (err === 'no_claude')  showToast(t('semantic_no_claude'));
    else                           showToast(t('semantic_error'));
    return;
  }

  showToast(t('semantic_started'));

  const runId = result.run_id;
  const poll = setInterval(async () => {
    let st;
    try { st = await pywebview.api.get_semantic_status(runId); } catch (_) { return; }
    if (!st || !st.done) return;

    clearInterval(poll);
    if (st.error) {
      showToast(st.error === 'timeout' ? t('semantic_timeout') : t('semantic_error'));
      return;
    }

    showToast(t('semantic_done'));
    // La pestaña Semantic Layer solo se renderiza si existe el sidecar. Tras generar,
    // si la reunión sigue abierta, re-renderiza el detalle (para que aparezca la pestaña)
    // y salta a ella.
    if (typeof currentPath !== 'undefined' && currentPath === path) {
      try {
        await openMeeting(path);
        document.getElementById('tab-semantic')?.click();
      } catch (_) {}
    }
  }, 3000);
}

async function renderSemanticTab(path) {
  const sec = document.getElementById('section-semantic');
  if (!sec || sec.dataset.path !== path) return;

  const L = currentLang === 'en';
  let info = {};
  try { info = (await pywebview.api.get_semantic_info(path)) || {}; } catch (_) {}

  // Aún no generado → estado vacío + botón generar
  if (!info || !info.generated_at) {
    sec.innerHTML = `
      <div style="text-align:center;padding:44px 20px;color:var(--muted)">
        <div style="font-size:13px;margin-bottom:16px;line-height:1.5">${t('semantic_tab_empty')}</div>
        <button class="btn btn-primary btn-sm" id="btn-semantic-generate">${t('semantic_tab_generate')}</button>
      </div>`;
    document.getElementById('btn-semantic-generate')?.addEventListener('click', () => {
      const m = allMeetings.find(x => x.path === path);
      openSemanticDialog(path, (m && m.title) || '');
    });
    return;
  }

  const fileRow = (label, fpath, exists, badge) => {
    if (!fpath) return '';
    const name = fpath.split(/[\\/]/).pop();
    return `
      <div style="display:flex;align-items:center;gap:8px;padding:10px 12px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px">
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(name)}${badge ? `<span style="font-size:10px;background:#8b5cf6;color:#fff;padding:1px 7px;border-radius:10px;flex:none">${escHtml(badge)}</span>` : ''}</div>
          <div style="font-size:11px;color:var(--muted)">${escHtml(label)}${exists ? '' : ' — ' + (L ? 'file missing' : 'no encontrado')}</div>
        </div>
        <button class="btn btn-ghost btn-sm" data-sem-reveal="${escHtml(fpath)}" ${exists ? '' : 'disabled'}>${t('semantic_reveal')}</button>
        <button class="btn btn-ghost btn-sm" data-sem-open="${escHtml(fpath)}" ${exists ? '' : 'disabled'}>${t('semantic_open')}</button>
        <button class="btn btn-ghost btn-sm" data-sem-copy="${escHtml(fpath)}">${t('semantic_copy')}</button>
      </div>`;
  };

  const csvPath   = info.csv_local || info.csv_source || '';
  const csvExists = info.csv_local_exists || info.csv_source_exists;
  const genDate   = escHtml((info.generated_at || '').replace('T', ' '));

  sec.innerHTML = `
    <div style="padding:4px 2px 14px">
      <div style="font-size:12px;color:var(--muted);margin-bottom:12px">${t('semantic_generated_at')} ${genDate}</div>
      ${fileRow(L ? 'Upload this file to OntoForge' : 'Sube este archivo a OntoForge', csvPath, csvExists, 'OntoForge')}
      ${fileRow(L ? 'Readable record' : 'Registro legible', info.record_local, info.record_local_exists, '')}
      <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
        ${info.inputs_dir ? `<button class="btn btn-ghost btn-sm" data-sem-reveal="${escHtml(info.inputs_dir)}" ${info.inputs_dir_exists ? '' : 'disabled'}>${t('semantic_open_inputs')}</button>` : ''}
        <button class="btn btn-ghost btn-sm" id="btn-semantic-regen">${t('semantic_tab_regen')}</button>
        <button class="btn btn-primary btn-sm" id="btn-semantic-ontoforge">${t('semantic_open_ontoforge')}</button>
      </div>
    </div>`;

  sec.querySelectorAll('[data-sem-reveal]').forEach(b =>
    b.addEventListener('click', () => pywebview.api.reveal_file(b.dataset.semReveal)));
  sec.querySelectorAll('[data-sem-open]').forEach(b =>
    b.addEventListener('click', () => pywebview.api.open_file(b.dataset.semOpen)));
  sec.querySelectorAll('[data-sem-copy]').forEach(b =>
    b.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(b.dataset.semCopy); showToast(t('semantic_copied')); }
      catch (_) { showToast(t('semantic_copy_fail')); }
    }));
  document.getElementById('btn-semantic-regen')?.addEventListener('click', () => {
    const m = allMeetings.find(x => x.path === path);
    openSemanticDialog(path, (m && m.title) || '');
  });
  document.getElementById('btn-semantic-ontoforge')?.addEventListener('click', () =>
    pywebview.api.open_url('https://knowledge-staging.accenture.com/ontoforge/ontology'));
}

// ── Diálogo "Generar capa semántica" con documentos extra ───────────────────────
let _semanticDialogDocs = [];

function openSemanticDialog(path, title) {
  _semanticDialogDocs = [];
  const msgEl = document.getElementById('semantic-modal-msg');
  if (msgEl) msgEl.innerHTML = t('semantic_dialog_msg');
  _renderSemanticDialogDocs();

  const go = document.getElementById('semantic-modal-go');
  if (go) go.onclick = () => {
    const docs = _semanticDialogDocs.slice();
    closeSemanticModal();
    runSemanticLayer(path, title || '', docs);
  };

  const browse = document.getElementById('semantic-modal-browse');
  if (browse) browse.onclick = async () => {
    let files = [];
    try { files = await pywebview.api.pick_files(); } catch (_) {}
    if (Array.isArray(files)) {
      for (const f of files) if (f && !_semanticDialogDocs.includes(f)) _semanticDialogDocs.push(f);
      _renderSemanticDialogDocs();
    }
  };

  document.getElementById('semantic-modal')?.classList.remove('hidden');
}

function _renderSemanticDialogDocs() {
  const list = document.getElementById('semantic-modal-docs');
  if (!list) return;
  if (!_semanticDialogDocs.length) {
    list.innerHTML = `<div class="semantic-doc-empty">${t('semantic_dialog_none')}</div>`;
    return;
  }
  list.innerHTML = _semanticDialogDocs.map((f, i) => {
    const name = f.split(/[\\/]/).pop();
    return `<div class="semantic-doc-item"><span class="semantic-doc-name" title="${escHtml(f)}">${escHtml(name)}</span><button class="semantic-doc-x" data-doc-i="${i}" title="${t('semantic_dialog_remove')}">✕</button></div>`;
  }).join('');
  list.querySelectorAll('[data-doc-i]').forEach(b =>
    b.addEventListener('click', () => {
      _semanticDialogDocs.splice(parseInt(b.dataset.docI, 10), 1);
      _renderSemanticDialogDocs();
    }));
}

function closeSemanticModal() {
  document.getElementById('semantic-modal')?.classList.add('hidden');
}

// ── Badge ─────────────────────────────────────────────────────────────────────

async function refreshPendingBadge() {
  const data  = await pywebview.api.get_tasks();
  const tasks = (data && data.tasks) || [];
  const count = tasks.filter(t => t.status !== 'done').length;
  const badge = document.getElementById('pending-badge');
  if (!badge) return;
  badge.style.display = count > 0 ? 'flex' : 'none';
  badge.textContent   = count > 9 ? '9+' : String(count);
}

// ── Export / Import transcript ────────────────────────────────────────────────

async function exportTranscript(path) {
  let result;
  try { result = await pywebview.api.export_transcript_file(path); } catch (e) {
    showToast(t('export_transcript_error')); return;
  }
  if (result === 'no_transcript') { showToast(t('export_transcript_no_file')); return; }
  if (result === 'cancelled') return;
  if (result === 'ok') { showToast(t('export_transcript_done')); return; }
  showToast(t('export_transcript_error'));
}

async function importTranscript() {
  const btn = document.querySelector('.btn-import-transcript');
  if (btn) btn.classList.add('loading');

  let result;
  try {
    result = await pywebview.api.import_transcript();
  } catch (e) {
    if (btn) btn.classList.remove('loading');
    showToast(t('import_transcript_error'));
    return;
  }

  if (!result || !result.ok) {
    if (btn) btn.classList.remove('loading');
    return; // usuario canceló el selector
  }

  showToast(t('import_transcript_processing'));

  const runId = result.run_id;
  const pollInterval = setInterval(async () => {
    let status;
    try { status = await pywebview.api.get_import_status(runId); } catch (_) { return; }

    if (!status.done) return;

    clearInterval(pollInterval);
    if (btn) btn.classList.remove('loading');

    if (status.error) {
      showToast(t('import_transcript_error'));
      return;
    }

    showToast(t('import_transcript_done'));
    await refreshMeetingList();
    if (status.path) openMeeting(status.path);
  }, 2000);
}

// ── Búsqueda ──────────────────────────────────────────────────────────────────

function onSearch(query) {
  clearTimeout(searchTimeout);
  if (!query.trim()) { renderSidebar(allMeetings); return; }
  searchTimeout = setTimeout(async () => {
    const results = await pywebview.api.search(query);
    results.forEach((m, i) => { meetingPaths[i] = m.path; });
    renderSidebar(results);
  }, 300);
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function openMinutesInClaude(path) {
  // Mostrar picker Terminal / App
  const existing = document.getElementById('claude-mode-picker');
  if (existing) existing.remove();

  const btn = document.getElementById('btn-claude');
  const rect = btn.getBoundingClientRect();

  const picker = document.createElement('div');
  picker.id = 'claude-mode-picker';
  picker.className = 'claude-mode-picker';
  picker.style.top  = (rect.bottom + 6) + 'px';
  picker.style.left = rect.left + 'px';

  const noTranscript = currentLang === 'es'
    ? 'No hay transcripción disponible para esta reunión'
    : 'No transcript available for this meeting';

  const terminalLabel = currentLang === 'es' ? 'Terminal' : 'Terminal';
  const appLabel      = currentLang === 'es' ? 'App' : 'App';

  picker.innerHTML = `
    <button class="claude-mode-opt" id="claude-opt-terminal">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
      ${terminalLabel}
    </button>
    <button class="claude-mode-opt" id="claude-opt-app">
      <svg width="13" height="13" viewBox="0 0 248 248" fill="currentColor"><path d="M13.827 3.52h-3.654L5.277 20.48h3.4l.85-2.807h5.946l.85 2.807h3.4L13.827 3.52zm-3.8 11.495 2.173-7.153 2.174 7.153H10.027z"/></svg>
      ${appLabel}
    </button>`;

  document.body.appendChild(picker);

  const close = (e) => { if (!picker.contains(e.target) && e.target !== btn) { picker.remove(); document.removeEventListener('mousedown', close); } };
  setTimeout(() => document.addEventListener('mousedown', close), 0);

  document.getElementById('claude-opt-terminal').onclick = async () => {
    picker.remove();
    document.removeEventListener('mousedown', close);
    const ok = await pywebview.api.open_minutes_in_claude(path, currentLang);
    if (!ok) showToast(noTranscript, 'error');
  };

  document.getElementById('claude-opt-app').onclick = async () => {
    picker.remove();
    document.removeEventListener('mousedown', close);
    const ok = await pywebview.api.open_minutes_in_claude_app(path, currentLang);
    if (ok) showToast(currentLang === 'es'
      ? 'Transcript copiado — pégalo en Claude'
      : 'Transcript copied — paste it in Claude');
    else showToast(noTranscript, 'error');
  };
}


function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function showToast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}

// ── Settings ──────────────────────────────────────────────────────────────────

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
}

async function initSettings() {
  let savedLang = 'es';
  let savedTheme = 'light';
  try {
    const s = await pywebview.api.get_settings();
    savedLang  = s.language || localStorage.getItem('lang')  || 'es';
    savedTheme = s.theme    || localStorage.getItem('theme') || 'light';
  } catch (e) {
    savedLang  = localStorage.getItem('lang')  || 'es';
    savedTheme = localStorage.getItem('theme') || 'light';
  }
  localStorage.setItem('lang',  savedLang);
  localStorage.setItem('theme', savedTheme);
  applyTheme(savedTheme);
  applyLang(savedLang);

  document.querySelectorAll('#theme-toggle .toggle-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.themeVal === savedTheme);
  });
  document.querySelectorAll('#lang-toggle .toggle-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.lang === savedLang);
  });

  document.querySelectorAll('#theme-toggle .toggle-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const theme = btn.dataset.themeVal;
      applyTheme(theme);
      localStorage.setItem('theme', theme);
      document.querySelectorAll('#theme-toggle .toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      await pywebview.api.save_settings({ theme });
      showToast(t('settings_saved'));
    });
  });

  document.querySelectorAll('#lang-toggle .toggle-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const lang = btn.dataset.lang;
      localStorage.setItem('lang', lang);
      applyLang(lang);
      document.querySelectorAll('#lang-toggle .toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      await pywebview.api.save_settings({ language: lang });
      showToast(t('settings_saved'));
    });
  });

  const coachingCb = document.getElementById('coaching-enabled');
  if (coachingCb) {
    pywebview.api.get_settings().then(s => {
      coachingCb.checked = s.coaching_enabled === true;
    });
  }

  const nameInput = document.getElementById('user-name-input');
  if (nameInput) {
    pywebview.api.get_settings().then(s => {
      nameInput.value = s.user_name || '';
    });
    const saveNameBtn = document.getElementById('btn-save-name');
    if (saveNameBtn) {
      saveNameBtn.addEventListener('click', async () => {
        await pywebview.api.save_settings({ user_name: nameInput.value.trim() });
        showToast(t('settings_saved'));
      });
    }
    nameInput.addEventListener('keydown', async e => {
      if (e.key === 'Enter') {
        await pywebview.api.save_settings({ user_name: nameInput.value.trim() });
        showToast(t('settings_saved'));
      }
    });
  }
}

// ── Panel de ejecuciones en paralelo ─────────────────────────────────────────

const _runTimers = {};  // runId → setInterval id

function openRunsPanel() {
  if (document.getElementById('view-meetings').classList.contains('hidden')) {
    showView('meetings');
  }
  document.getElementById('runs-panel').classList.add('open');
  document.getElementById('runs-panel-title').textContent = t('runs_panel_title');
  updateRunsFab();
}

function closeRunsPanel() {
  document.getElementById('runs-panel').classList.remove('open');
  updateRunsFab();
}

function updateRunsFab() {
  const fab   = document.getElementById('runs-toggle-fab');
  const dot   = document.getElementById('runs-fab-dot');
  const label = document.getElementById('runs-fab-label');
  if (!fab) return;
  const list    = document.getElementById('runs-list');
  const total   = list?.children.length || 0;
  const running = list ? [...list.children].filter(c => c.querySelector('.run-card-status.running')).length : 0;
  const panelOpen = document.getElementById('runs-panel')?.classList.contains('open');
  if (total === 0 || panelOpen) { fab.style.display = 'none'; return; }
  fab.style.display = 'flex';
  if (dot)   { dot.classList.toggle('active', running > 0); }
  if (label) {
    label.textContent = running > 0
      ? `${running} ${t('runs_fab_running')}`
      : `${total} ${t('runs_fab_done')}`;
  }
}

const _runStartTimes = {};
const _runMeta = {};  // runId → {path, index, proj_path} populated on done

function _needsConversation(output) {
  if (!output || output.length < 10) return false;
  const tail = output.trimEnd().slice(-400).toLowerCase();
  // Ends with a question mark, or contains common clarification phrases
  if (/\?\s*$/.test(tail)) return true;
  return ['could you', 'please provide', 'please clarify', 'i need more info',
          'which would you', 'should i ', 'let me know if', 'do you want',
          'podrías', '¿', 'necesito saber', 'puedes indicar', 'me indicas',
          'qué prefieres', 'cuál prefieres'].some(p => tail.includes(p));
}

async function continueInTerminal(runId) {
  const meta = _runMeta[runId];
  if (!meta) return;
  await pywebview.api.continue_in_terminal(meta.path, meta.index, meta.proj_path, meta.output || '');
}

function addRunCard(runId) {
  _runStartTimes[runId] = Date.now();
  const list = document.getElementById('runs-list');
  const card = document.createElement('div');
  card.className = 'run-card';
  card.id = 'run-card-' + runId;
  card.innerHTML = `
    <div class="run-card-header">
      <span class="run-card-title" id="runtitle-${runId}">...</span>
      <span class="run-card-status running" id="runstatus-${runId}">
        <span class="run-spinner"></span>${t('run_running')} <span id="runtimer-${runId}">0s</span>
      </span>
    </div>
    <div class="run-card-dir" id="rundir-${runId}"></div>
    <div class="run-card-footer" id="runfooter-${runId}">
      <button class="btn btn-ghost btn-sm" onclick="dismissRun('${runId}')">${t('btn_delete')}</button>
    </div>`;
  list.prepend(card);
  _runTimers[runId] = setInterval(() => pollRun(runId), 700);
  updateRunsFab();
}

function _fmtElapsed(runId) {
  const secs = Math.floor((Date.now() - (_runStartTimes[runId] || Date.now())) / 1000);
  if (secs < 60) return secs + 's';
  return Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
}

async function pollRun(runId) {
  try {
    const s = await pywebview.api.get_action_run_status(runId);
    const titleEl  = document.getElementById('runtitle-'  + runId);
    const statusEl = document.getElementById('runstatus-' + runId);
    const dirEl    = document.getElementById('rundir-'    + runId);
    if (titleEl  && s.title)     titleEl.textContent = s.title;
    if (dirEl    && s.proj_path) dirEl.textContent   = s.proj_path;
    const timerEl = document.getElementById('runtimer-' + runId);
    if (timerEl && !s.done) timerEl.textContent = _fmtElapsed(runId);
    if (s.done) {
      clearInterval(_runTimers[runId]);
      delete _runTimers[runId];
      _runMeta[runId] = {path: s.path || '', index: s.index ?? -1, proj_path: s.proj_path || '', output: s.output || ''};
      const needsChat = !s.error && _needsConversation(s.output);
      if (statusEl) {
        statusEl.innerHTML = s.error
          ? t('run_error') + ': ' + s.error
          : needsChat ? t('run_needs_input') : t('run_done');
        statusEl.className = 'run-card-status ' + (s.error ? 'error' : needsChat ? 'pending' : 'done');
      }
      updateRunsFab();
      const footerEl = document.getElementById('runfooter-' + runId);
      if (footerEl) {
        footerEl.innerHTML = `
          <button class="btn btn-ghost btn-sm" onclick="continueInTerminal('${runId}')">${t('btn_continue_terminal')}</button>
          <button class="btn btn-ghost btn-sm" onclick="dismissRun('${runId}')">${t('btn_delete')}</button>`;
      }
      if (!s.error && s.path && s.path === currentPath) openMeeting(s.path);
    }
  } catch (e) {
    clearInterval(_runTimers[runId]);
    delete _runTimers[runId];
  }
}

function dismissRun(runId) {
  if (_runTimers[runId]) { clearInterval(_runTimers[runId]); delete _runTimers[runId]; }
  document.getElementById('run-card-' + runId)?.remove();
  const list = document.getElementById('runs-list');
  if (!list.children.length) closeRunsPanel();
  else updateRunsFab();
}

async function sendFollowup(runId) {
  const el = document.getElementById('followup-' + runId);
  if (!el) return;
  const msg = el.value.trim();
  if (!msg) return;
  el.value = '';
  const newRunId = await pywebview.api.send_action_followup(runId, msg);
  if (newRunId) addRunCard(newRunId);
}

// ── Regenerar minutas ─────────────────────────────────────────────────────────

function toggleRegenBar(forceVisible) {
  const bar = document.getElementById('regen-bar');
  if (!bar) return;
  const show = forceVisible !== undefined ? forceVisible : !_regenVisible;
  _regenVisible = show;
  bar.classList.toggle('hidden', !show);
  if (show) document.getElementById('regen-textarea')?.focus();
}

function _regenStageLabel(stage) {
  const key = 'regen_stage_' + stage;
  return t(key) || stage;
}

function _showRegenProgress(bar) {
  bar.innerHTML = `
    <div class="regen-progress-wrap">
      <div class="regen-progress-label" id="regen-progress-label">${t('toast_regenerating')}</div>
      <div class="regen-progress-track">
        <div class="regen-progress-fill" id="regen-progress-fill" style="width:0%"></div>
      </div>
      <div class="regen-progress-pct" id="regen-progress-pct">0%</div>
    </div>`;
  bar.classList.remove('hidden');
}

function _restoreRegenBar(bar) {
  bar.classList.add('hidden');
  bar.innerHTML = `
    <textarea id="regen-textarea" rows="2" placeholder="${t('regen_placeholder')}"></textarea>
    <div class="regen-bar-btns">
      <button class="btn btn-primary btn-sm" id="btn-regen-confirm">${t('regen_confirm')}</button>
      <button class="btn btn-ghost btn-sm" id="btn-regen-cancel">${t('regen_cancel')}</button>
    </div>`;
  _regenVisible = false;
  const path = currentPath;
  document.getElementById('btn-regen-confirm')?.addEventListener('click', () => confirmRegen(path));
  document.getElementById('btn-regen-cancel')?.addEventListener('click', () => toggleRegenBar(false));
}

async function confirmRegen(path) {
  const ta = document.getElementById('regen-textarea');
  const ctx = ta ? ta.value.trim() : '';

  const bar = document.getElementById('regen-bar');
  _showRegenProgress(bar);
  _regenPath = path;

  const ok = await pywebview.api.regenerate_minutes(path, ctx);
  if (!ok) {
    _regenPath = null;
    _restoreRegenBar(bar);
    showToast(t('toast_regen_error'));
    return;
  }

  // Polling: actualiza barra de progreso cada segundo hasta done (max 120s)
  for (let i = 0; i < 120; i++) {
    await new Promise(r => setTimeout(r, 1000));

    // If the panel was rebuilt by something else, bar is detached — keep
    // polling Python but skip UI updates until we call openMeeting at the end.
    if (bar.isConnected) {
      try {
        const s = await pywebview.api.get_regen_status(path);
        const fill  = document.getElementById('regen-progress-fill');
        const label = document.getElementById('regen-progress-label');
        const pctEl = document.getElementById('regen-progress-pct');
        if (fill)  fill.style.width  = (s.pct || 0) + '%';
        if (pctEl) pctEl.textContent = (s.pct || 0) + '%';
        if (label) label.textContent = s.error ? t('regen_stage_error') : _regenStageLabel(s.stage);

        if (s.done) {
          if (s.error) {
            _regenPath = null;
            _restoreRegenBar(bar);
            showToast(t('regen_stage_error') + ': ' + s.error);
            return;
          }
          _regenPath = null;
          showToast(t('toast_regen_done'));
          await openMeeting(path);
          return;
        }
      } catch (_) {}
    } else {
      // Bar detached — just poll status, no UI updates
      try {
        const s = await pywebview.api.get_regen_status(path);
        if (s.done) {
          _regenPath = null;
          showToast(s.error ? t('regen_stage_error') + ': ' + s.error : t('toast_regen_done'));
          if (!s.error) await openMeeting(path);
          return;
        }
      } catch (_) {}
    }
  }
  _regenPath = null;
  if (bar.isConnected) _restoreRegenBar(bar);
  showToast(t('toast_regen_done'));
  if (path === currentPath) await openMeeting(path);
}

// ── Resize sidebar ────────────────────────────────────────────────────────────

function initResize() {
  const handle  = document.querySelector('.resize-handle');
  const sidebar = document.querySelector('.sidebar');
  if (!handle || !sidebar) return;

  const saved = localStorage.getItem('sidebarWidth');
  if (saved) sidebar.style.width = saved + 'px';

  let isResizing = false, startX = 0, startW = 0;

  handle.addEventListener('mousedown', e => {
    isResizing = true;
    startX = e.clientX;
    startW = sidebar.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', e => {
    if (!isResizing) return;
    const w = Math.max(160, Math.min(480, startW + (e.clientX - startX)));
    sidebar.style.width = w + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!isResizing) return;
    isResizing = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    localStorage.setItem('sidebarWidth', sidebar.offsetWidth);
  });
}

// ── Pipeline status tab + panel ───────────────────────────────────────────────

let _pipelinePanelOpen = false;

async function cancelPipelineJob(stem) {
  if (!stem) return;
  if (!confirm(t('discard_job_confirm'))) return;
  try {
    await pywebview.api.cancel_job(stem);
    showToast(t('discard_job_done'));
  } catch (e) {
    showToast('Error: ' + e);
  }
}

let _hadPipelineJobs = false;

async function updatePipelineFooter() {
  const tab  = document.getElementById('pipeline-tab');
  const dot  = document.getElementById('pipeline-tab-dot');
  const body = document.getElementById('pipeline-panel-body');
  if (!tab || !body) return;

  let status;
  try { status = await pywebview.api.get_pipeline_status(); } catch (_) { return; }

  const jobs = status?.jobs ?? [];

  // Cuando el pipeline termina hay una minuta nueva. La lista lateral solo se
  // cargaba al abrir la app, así que la reunión recién procesada se abría en el
  // detalle (vía get_navigate_request) pero no aparecía en el panel izquierdo
  // hasta reiniciar.
  if (jobs.length) {
    _hadPipelineJobs = true;
  } else if (_hadPipelineJobs) {
    _hadPipelineJobs = false;
    try { await refreshMeetingList(); } catch (_) {}
  }

  if (!jobs.length) {
    tab.classList.add('idle');
    dot.className = 'pipeline-tab-dot idle';
    body.innerHTML = `<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px 0">${t('pipeline_idle') || 'Sin actividad'}</div>`;
    return;
  }

  tab.classList.remove('idle');
  const anyRecording = jobs.some(j => j.stage === 'recording');
  dot.className = 'pipeline-tab-dot ' + (anyRecording ? 'recording' : 'processing');

  const L = currentLang === 'en';
  body.innerHTML = jobs.map(j => {
    const dotClass = j.stage === 'recording' ? 'recording' : j.stage === 'queued' ? 'queued' : 'processing';
    const pct      = j.pct ?? null;

    // Title line: prefer parsed title, fall back to raw label
    const titleText = j.title || (j.stage === 'recording' ? (L ? 'Recording...' : 'Grabando...') : j.label);
    const timeText  = j.time ? `<span class="pipeline-job-time">${escHtml(j.time)}</span>` : '';

    // Step pills: show for recording and processing stages
    let stepsHtml = '';
    if (j.total_steps && j.stage !== 'queued') {
      const stepDefs = L
        ? ['Recording', 'Transcribing', 'Minutes', 'Actions']
        : ['Grabando',  'Transcribiendo', 'Minutas', 'Acciones'];
      // steps: 0=recording, 1=transcription, 2=minutes, 3=actions
      const curStep = j.step ?? 0;
      stepsHtml = `<div class="pipeline-job-steps">
        ${stepDefs.slice(0, j.total_steps + 1).map((name, i) => {
          const cls = i < curStep ? 'done' : i === curStep ? 'active' : 'pending';
          return `<span class="pipeline-step-pill ${cls}">${escHtml(name)}</span>`;
        }).join('<span class="pipeline-step-arrow">›</span>')}
      </div>`;
    }

    // Progress bar
    let progHtml;
    if (j.stage === 'queued') {
      progHtml = `<div class="pipeline-job-progress"><div class="pipeline-job-progress-fill" style="width:0%"></div></div>`;
    } else if (pct != null) {
      progHtml = `<div class="pipeline-job-progress"><div class="pipeline-job-progress-fill" style="width:${pct}%"></div></div>`;
    } else {
      progHtml = `<div class="pipeline-job-progress shimmer-wrap"><div class="pipeline-job-progress-fill shimmer"></div></div>`;
    }

    // Elapsed for recording; pct% for transcription; MM:SS for minutes/actions (step_started)
    let elapsedHtml;
    if (j.stage === 'recording' && j.elapsed != null) {
      elapsedHtml = `<span class="pipeline-job-elapsed">${Math.floor(j.elapsed/60).toString().padStart(2,'0')}:${(j.elapsed%60).toString().padStart(2,'0')}</span>`;
    } else if (pct != null) {
      elapsedHtml = `<span class="pipeline-job-pct">${pct}%</span>`;
    } else if (j.step_started) {
      const _e = Math.max(0, Math.floor(Date.now() / 1000 - j.step_started));
      elapsedHtml = `<span class="pipeline-job-pct">${Math.floor(_e/60).toString().padStart(2,'0')}:${(_e%60).toString().padStart(2,'0')}</span>`;
    } else {
      elapsedHtml = '';
    }

    // Descartar: solo para trabajos ya en cola o en proceso. Una grabación en
    // curso se para desde el menú de la bandeja, que tiene su propio flujo.
    const cancelHtml = (j.stem && j.stage !== 'recording')
      ? `<button class="pipeline-job-cancel" title="${escHtml(t('discard_job'))}"
                 onclick="cancelPipelineJob(decodeURIComponent('${encodeURIComponent(j.stem)}'))">×</button>`
      : '';

    const isSemantic = j.kind === 'semantic';
    const mainLine = isSemantic ? (currentLang === 'en' ? 'Export to Semantic Layer' : 'Exportar a Semantic Layer') : titleText;
    const subLine = (isSemantic && j.subtitle)
      ? `<div class="pipeline-job-subtitle" style="font-size:11px;color:var(--muted);margin:1px 0 0 16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(j.subtitle)}</div>`
      : '';

    return `
      <div class="pipeline-job-card ${dotClass}">
        <div class="pipeline-job-header">
          <span class="pipeline-job-dot ${dotClass}"></span>
          <span class="pipeline-job-label">${escHtml(mainLine)}</span>
          ${timeText}
          ${elapsedHtml}
          ${cancelHtml}
        </div>
        ${subLine}
        ${stepsHtml}
        ${progHtml}
      </div>`;
  }).join('');
}

function togglePipelinePanel() {
  _pipelinePanelOpen = !_pipelinePanelOpen;
  const panel = document.getElementById('pipeline-panel');
  const tab   = document.getElementById('pipeline-tab');
  if (panel) panel.classList.toggle('open', _pipelinePanelOpen);
  if (tab)   tab.classList.toggle('panel-open', _pipelinePanelOpen);
}

document.addEventListener('click', e => {
  if (!_pipelinePanelOpen) return;
  const panel = document.getElementById('pipeline-panel');
  const tab   = document.getElementById('pipeline-tab');
  if (panel && !panel.contains(e.target) && tab && !tab.contains(e.target)) {
    _pipelinePanelOpen = false;
    panel.classList.remove('open');
    tab.classList.remove('panel-open');
  }
});

// ── Editar notas (editor visual WYSIWYG) ──────────────────────────────────────

let _editingNotes = false;

async function toggleEditNotes(path) {
  if (_editingNotes) return;
  const sec = document.getElementById('section-notes');
  if (!sec) return;
  document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-notes')?.classList.add('active');
  document.getElementById('section-notes')?.classList.remove('hidden');
  document.getElementById('section-actions')?.classList.add('hidden');

  let html = '';
  try { html = await pywebview.api.get_minutes_html(path); } catch (_) {}
  _editingNotes = true;
  sec.innerHTML = `
    <div class="notes-edit-toolbar">
      <div class="notes-edit-tools">
        <button class="notes-tool" data-cmd="bold" title="${t('fmt_bold')}"><b>B</b></button>
        <button class="notes-tool" data-cmd="italic" title="${t('fmt_italic')}"><i>I</i></button>
        <button class="notes-tool" data-block="H2" title="${t('fmt_h2')}">H2</button>
        <button class="notes-tool" data-block="H3" title="${t('fmt_h3')}">H3</button>
        <button class="notes-tool" data-block="P" title="${t('fmt_text')}">¶</button>
        <button class="notes-tool" data-cmd="insertUnorderedList" title="${t('fmt_list')}">•</button>
        <span class="notes-tool-dropdown">
          <button class="notes-tool" data-action="tablemenu" title="${t('fmt_table')}"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18M3 14h18M9 4v16M15 4v16"/></svg> ▾</button>
          <div class="table-menu hidden" id="table-menu">
            <button data-tbl="insert">${t('tbl_insert')}</button>
            <button data-tbl="addrow">${t('tbl_addrow')}</button>
            <button data-tbl="delrow">${t('tbl_delrow')}</button>
            <button data-tbl="addcol">${t('tbl_addcol')}</button>
            <button data-tbl="delcol">${t('tbl_delcol')}</button>
            <button data-tbl="deltable" class="table-menu-danger">${t('tbl_delete')}</button>
          </div>
        </span>
      </div>
      <div class="notes-edit-btns">
        <button class="btn btn-primary btn-sm" id="btn-notes-save">${t('save_btn')}</button>
        <button class="btn btn-ghost btn-sm" id="btn-notes-cancel">${t('cancel')}</button>
      </div>
    </div>
    <div class="notes-edit-hint">${t('edit_notes_hint')}</div>
    <div class="notes-edit-area minutes-content" id="notes-edit-area" contenteditable="true">${html ? sanitizeHtml(html) : ''}</div>`;
  const area = document.getElementById('notes-edit-area');
  if (area) area.focus();
  const _tb = sec.querySelector('.notes-edit-toolbar');
  const _hdr = document.querySelector('.detail-header');
  const _tabs = document.querySelector('.detail-tabs');
  if (_tb) _tb.style.top = ((_hdr?.offsetHeight || 0) + (_tabs?.offsetHeight || 0)) + 'px';
  sec.querySelectorAll('.notes-tool').forEach(btn => {
    btn.addEventListener('mousedown', e => e.preventDefault());
    btn.addEventListener('click', () => {
      if (btn.dataset.action === 'tablemenu') {
        document.getElementById('table-menu')?.classList.toggle('hidden');
        return;
      }
      area.focus();
      if (btn.dataset.cmd) document.execCommand(btn.dataset.cmd, false, null);
      else if (btn.dataset.block) document.execCommand('formatBlock', false, btn.dataset.block);
    });
  });
  sec.querySelectorAll('#table-menu button').forEach(mi => {
    mi.addEventListener('mousedown', e => e.preventDefault());
    mi.addEventListener('click', () => {
      _tableOp(area, mi.dataset.tbl);
      document.getElementById('table-menu')?.classList.add('hidden');
    });
  });
  document.getElementById('btn-notes-save').onclick = async () => {
    const md = htmlToMarkdown(area);
    let ok = false;
    try { ok = await pywebview.api.save_minutes_notes(path, md); } catch (_) {}
    _editingNotes = false;
    if (ok) showToast(t('notes_saved'));
    await _reRenderNotes(path);
  };
  document.getElementById('btn-notes-cancel').onclick = async () => {
    _editingNotes = false;
    await _reRenderNotes(path);
  };
}

function htmlToMarkdown(root) {
  function inline(node) {
    let out = '';
    node.childNodes.forEach(n => {
      if (n.nodeType === 3) { out += n.textContent; return; }
      if (n.nodeType !== 1) return;
      const tag = n.tagName.toLowerCase();
      const inner = inline(n);
      if (tag === 'strong' || tag === 'b') out += `**${inner}**`;
      else if (tag === 'em' || tag === 'i') out += `*${inner}*`;
      else if (tag === 'code') out += '`' + inner + '`';
      else if (tag === 'a') out += `[${inner}](${n.getAttribute('href') || ''})`;
      else if (tag === 'br') out += '\n';
      else out += inner;
    });
    return out;
  }
  function tableToMarkdown(table) {
    const trs = [...table.querySelectorAll('tr')];
    const out = [];
    trs.forEach((tr, ri) => {
      const cells = [...tr.children].map(c => (inline(c).trim().replace(/\|/g, '\\|')) || ' ');
      out.push('| ' + cells.join(' | ') + ' |');
      if (ri === 0) out.push('| ' + cells.map(() => '---').join(' | ') + ' |');
    });
    return out;
  }
  const lines = [];
  function block(node) {
    node.childNodes.forEach(n => {
      if (n.nodeType === 3) { const txt = n.textContent.trim(); if (txt) lines.push(txt, ''); return; }
      if (n.nodeType !== 1) return;
      const tag = n.tagName.toLowerCase();
      if (tag === 'h1') lines.push('# ' + inline(n).trim(), '');
      else if (tag === 'h2') lines.push('## ' + inline(n).trim(), '');
      else if (tag === 'h3') lines.push('### ' + inline(n).trim(), '');
      else if (tag === 'p' || tag === 'div') { const txt = inline(n).trim(); if (txt) lines.push(txt, ''); else block(n); }
      else if (tag === 'ul') { n.querySelectorAll(':scope > li').forEach(li => lines.push('- ' + inline(li).trim())); lines.push(''); }
      else if (tag === 'ol') { let i = 1; n.querySelectorAll(':scope > li').forEach(li => lines.push((i++) + '. ' + inline(li).trim())); lines.push(''); }
      else if (tag === 'table') { tableToMarkdown(n).forEach(l => lines.push(l)); lines.push(''); }
      else if (tag === 'blockquote') { lines.push('> ' + inline(n).trim(), ''); }
      else if (tag === 'br') { lines.push(''); }
      else { const txt = inline(n).trim(); if (txt) lines.push(txt, ''); }
    });
  }
  block(root);
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
}

function _placeCaret(node) {
  try {
    const sel = window.getSelection();
    const r = document.createRange();
    r.setStart(node, 0); r.collapse(true);
    sel.removeAllRanges(); sel.addRange(r);
  } catch (_) {}
}

function _tableCtx(area) {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return null;
  let node = sel.anchorNode, cell = null, tr = null, table = null;
  while (node && node !== area) {
    if (node.nodeType === 1) {
      const tag = node.tagName;
      if (!cell && (tag === 'TD' || tag === 'TH')) cell = node;
      if (!tr && tag === 'TR') tr = node;
      if (tag === 'TABLE') { table = node; break; }
    }
    node = node.parentNode;
  }
  if (!table) return null;
  if (!tr && cell) tr = cell.parentNode;
  const cellIndex = (cell && tr) ? [...tr.children].indexOf(cell) : 0;
  return { table, tr, cell, cellIndex };
}

function _insertTable(area) {
  area.focus();
  const cols = 3, rows = 2;
  let html = '<table>';
  for (let r = 0; r < rows; r++) {
    html += '<tr>';
    for (let c = 0; c < cols; c++) {
      const tag = r === 0 ? 'th' : 'td';
      html += `<${tag}>${r === 0 ? 'Columna ' + (c + 1) : '&nbsp;'}</${tag}>`;
    }
    html += '</tr>';
  }
  html += '</table><p><br></p>';
  document.execCommand('insertHTML', false, html);
}

function _tableOp(area, op) {
  area.focus();
  if (op === 'insert') { _insertTable(area); return; }
  const ctx = _tableCtx(area);
  if (!ctx) { showToast(t('table_need_cursor')); return; }
  const { table, tr, cellIndex } = ctx;
  const rows = [...table.querySelectorAll('tr')];
  if (op === 'addrow') {
    const cols = (tr || rows[0]).children.length || 1;
    const nr = document.createElement('tr');
    for (let i = 0; i < cols; i++) { const td = document.createElement('td'); td.innerHTML = '<br>'; nr.appendChild(td); }
    (tr || rows[rows.length - 1]).after(nr);
    _placeCaret(nr.firstChild);
  } else if (op === 'delrow') {
    if (rows.length <= 1) { table.remove(); return; }
    (tr || rows[rows.length - 1]).remove();
  } else if (op === 'addcol') {
    rows.forEach(r => {
      const isHead = !!r.querySelector('th') && !r.querySelector('td');
      const cell = document.createElement(isHead ? 'th' : 'td');
      cell.innerHTML = isHead ? 'Columna' : '<br>';
      const ref = r.children[cellIndex];
      if (ref && ref.nextSibling) r.insertBefore(cell, ref.nextSibling);
      else r.appendChild(cell);
    });
  } else if (op === 'delcol') {
    const colCount = rows[0] ? rows[0].children.length : 0;
    if (colCount <= 1) { table.remove(); return; }
    rows.forEach(r => { const c = r.children[cellIndex]; if (c) c.remove(); });
  } else if (op === 'deltable') {
    table.remove();
  }
}

async function _reRenderNotes(path) {
  const sec = document.getElementById('section-notes');
  if (!sec) return;
  let html = '';
  try { html = await pywebview.api.get_minutes_html(path); } catch (_) {}
  sec.innerHTML = `<div class="minutes-content">${html ? sanitizeHtml(html) : `<em>${t('no_minutes')}</em>`}</div>`;
}

// ── Clipboard helpers (María's triple-fallback) ───────────────────────────────

function _copyRich(html, text) {
  const done = () => showToast(t('copy_transcript_done'));
  const fail = () => showToast(t('copy_failed'));
  if (navigator.clipboard && window.ClipboardItem && html) {
    try {
      const item = new ClipboardItem({
        'text/html':  new Blob([html], { type: 'text/html' }),
        'text/plain': new Blob([text], { type: 'text/plain' }),
      });
      navigator.clipboard.write([item]).then(done, () => _copyPlain(text, done, fail));
      return;
    } catch (_) {}
  }
  _copyPlain(text, done, fail);
}

function _copyPlain(text, done, fail) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => _copyExec(text, done, fail));
  } else { _copyExec(text, done, fail); }
}

function _copyExec(text, done, fail) {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.cssText = 'position:fixed;left:-9999px;top:0';
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (_) {}
  ta.remove();
  ok ? done() : fail();
}

async function _copyActionsTable(path) {
  let actions = [];
  try { actions = (await pywebview.api.get_actions(path)) || []; } catch (_) {}
  const heads = ['Acción', 'Responsable', 'Fecha', 'Plazo'];
  const rows  = actions.map(a => [
    a.title || '',
    a.assignee || '—',
    a.date || '—',
    a.deadline || '—',
  ]);
  const TH = 'text-align:center;padding:10px 16px;border:1px solid #9ca3af;background:#374151;color:#fff;font-family:Calibri,Arial,sans-serif;font-size:13px;font-weight:600;letter-spacing:0.3px;white-space:nowrap';
  const TD = (even) => `text-align:center;padding:8px 16px;border:1px solid #e5e7eb;font-family:Calibri,Arial,sans-serif;font-size:13px;color:#1a1a2e;background:${even ? '#f9fafb' : '#fff'};white-space:nowrap`;
  const th  = heads.map(h => `<th style="${TH}">${escHtml(h)}</th>`).join('');
  const trs = rows.map((r, i) => `<tr>${r.map(c => `<td style="${TD(i % 2 === 1)}">${escHtml(c)}</td>`).join('')}</tr>`).join('');
  const rich = `<table style="border-collapse:collapse;font-family:Calibri,Arial,sans-serif;font-size:13px;width:auto"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
  const text = [heads.join('\t'), ...rows.map(r => r.join('\t'))].join('\n');
  _copyRich(rich, text);
}

// ── Inline styles for clipboard (makes Word/Outlook paste look good) ──────────

function _inlineStyles(html) {
  const div = document.createElement('div');
  div.innerHTML = html;

  const S  = 'font-family:Calibri,Arial,sans-serif;font-size:13px;color:#1a1a2e;line-height:1.65';
  const BD = 'border-bottom:1.5px solid #e5e7eb;padding-bottom:5px;margin:18px 0 8px';

  div.querySelectorAll('h1').forEach(el => el.setAttribute('style', `font-size:20px;font-weight:700;color:#1a1a2e;${BD};font-family:Calibri,Arial,sans-serif`));
  div.querySelectorAll('h2').forEach(el => el.setAttribute('style', `font-size:16px;font-weight:700;color:#1a1a2e;text-decoration:underline;${BD};font-family:Calibri,Arial,sans-serif`));
  div.querySelectorAll('h3').forEach(el => el.setAttribute('style', `font-size:14px;font-weight:700;color:#374151;text-decoration:underline;margin:12px 0 5px;font-family:Calibri,Arial,sans-serif`));
  div.querySelectorAll('p').forEach(el  => el.setAttribute('style', `margin:0 0 9px;color:#374151;font-family:Calibri,Arial,sans-serif;font-size:13px;line-height:1.65`));
  div.querySelectorAll('ul,ol').forEach(el => el.setAttribute('style', `margin:0 0 10px;padding-left:22px;color:#374151;font-family:Calibri,Arial,sans-serif;font-size:13px`));
  div.querySelectorAll('li').forEach(el  => el.setAttribute('style', `margin-bottom:5px;color:#374151`));
  div.querySelectorAll('strong,b').forEach(el => el.setAttribute('style', `font-weight:700;color:#1a1a2e`));
  div.querySelectorAll('em,i').forEach(el => el.setAttribute('style', `font-style:italic;color:#374151`));
  div.querySelectorAll('code').forEach(el => el.setAttribute('style', `font-family:Consolas,Courier New,monospace;font-size:12px;background:#f3f0f9;padding:1px 5px;border-radius:3px;color:#7c3aed`));
  div.querySelectorAll('blockquote').forEach(el => el.setAttribute('style', `border-left:3px solid #A100FF;margin:10px 0;padding:6px 12px;color:#5c5470;font-style:italic;background:#faf5ff`));
  div.querySelectorAll('table').forEach(el => el.setAttribute('style', `border-collapse:collapse;width:100%;margin:12px 0;font-family:Calibri,Arial,sans-serif;font-size:13px`));
  div.querySelectorAll('th').forEach(el  => el.setAttribute('style', `text-align:left;padding:8px 13px;border:1px solid #c4b5fd;background:#7c3aed;color:#fff;font-weight:600`));
  div.querySelectorAll('td').forEach(el  => el.setAttribute('style', `padding:7px 13px;border:1px solid #e5e7eb;color:#1a1a2e;background:#fff`));
  // Alternate row tint
  div.querySelectorAll('tbody tr:nth-child(even) td').forEach(el => el.setAttribute('style', `padding:7px 13px;border:1px solid #e5e7eb;color:#1a1a2e;background:#faf5ff`));
  div.querySelectorAll('hr').forEach(el  => el.setAttribute('style', `border:none;border-top:1.5px solid #e5e7eb;margin:14px 0`));

  return `<div style="${S}">${div.innerHTML}</div>`;
}

// ── Sticky notes ──────────────────────────────────────────────────────────────

async function _loadStickies(path) {
  const layer = document.getElementById('sticky-layer');
  if (!layer) return;
  try { _stickies = (await pywebview.api.get_stickies(path)) || []; }
  catch (_) { _stickies = []; }
  layer.innerHTML = _stickies.map(_stickyHtml).join('');
  _wireStickies();
}

function _stickyHtml(s) {
  const x = s.x ?? 20, y = s.y ?? 20;
  const posStyle = s.anchor === 'right' ? `right:${x}px;top:${y}px;` : `left:${x}px;top:${y}px;`;
  const body = s.html
    ? `<div class="sticky-html">${s.html}</div>`
    : `<textarea class="sticky-body" placeholder="${t('sticky_ph')}" spellcheck="false">${escHtml(s.text || '')}</textarea>`;
  return `<div class="sticky-note${s.minimized ? ' min' : ''}" data-sid="${escHtml(s.id)}" style="${posStyle}">
    <div class="sticky-head">
      <span class="sticky-preview">${escHtml(s.label || (s.text || '').split('\n')[0].slice(0, 28))}</span>
      <button class="sticky-btn sticky-min" title="${t('sticky_min')}">${s.minimized ? '+' : '–'}</button>
      <button class="sticky-btn sticky-del" title="${t('sticky_del')}">×</button>
    </div>
    ${body}
  </div>`;
}

function _wireStickies() {
  const layer = document.getElementById('sticky-layer');
  if (!layer) return;
  layer.querySelectorAll('.sticky-note').forEach(node => {
    const id = node.dataset.sid;
    const ta = node.querySelector('.sticky-body');
    node.querySelector('.sticky-del').onclick = () => {
      _stickies = _stickies.filter(x => x.id !== id);
      node.remove();
      _saveStickies();
    };
    node.querySelector('.sticky-min').onclick = (e) => {
      const s = _stickies.find(x => x.id === id);
      if (!s) return;
      s.minimized = !s.minimized;
      node.classList.toggle('min', s.minimized);
      e.currentTarget.textContent = s.minimized ? '+' : '–';
      const prev = node.querySelector('.sticky-preview');
      if (prev) prev.textContent = s.label || (ta ? ta.value : (s.text || '')).split('\n')[0].slice(0, 28);
      _saveStickies();
    };
    if (ta) ta.oninput = () => {
      const found = _stickies.find(x => x.id === id);
      if (found) found.text = ta.value;
      _saveStickies();
    };
    // Drag to reposition
    const head = node.querySelector('.sticky-head');
    head.addEventListener('mousedown', e => {
      if (e.target.closest('button')) return;
      e.preventDefault();
      const layerRect = layer.getBoundingClientRect();
      const offX = e.clientX - node.getBoundingClientRect().left;
      const offY = e.clientY - node.getBoundingClientRect().top;
      node.classList.add('dragging');
      const onMove = ev => {
        const nx = Math.max(0, ev.clientX - layerRect.left - offX);
        const ny = Math.max(0, ev.clientY - layerRect.top  - offY);
        node.style.left = nx + 'px';
        node.style.top  = ny + 'px';
      };
      const onUp = () => {
        node.classList.remove('dragging');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup',   onUp);
        const s = _stickies.find(x => x.id === id);
        if (s) { s.anchor = 'left'; s.x = parseInt(node.style.left)||0; s.y = parseInt(node.style.top)||0; _saveStickies(); }
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup',   onUp);
    });
  });
}

function addSticky() {
  if (!currentPath) return;
  const layer = document.getElementById('sticky-layer');
  if (!layer) return;
  const offset = _stickies.length * 24;
  const header = document.querySelector('.detail-header');
  const tabs   = document.querySelector('.detail-tabs');
  const topOffset = (header ? header.offsetHeight : 80) + (tabs ? tabs.offsetHeight : 40) + 12;
  const s = { id: 'st' + Date.now(), text: '', minimized: false, x: 20 + offset, y: topOffset + offset };
  _stickies.push(s);
  layer.insertAdjacentHTML('beforeend', _stickyHtml(s));
  _wireStickies();
  _saveStickies();
  const ta = layer.querySelector(`.sticky-note[data-sid="${s.id}"] .sticky-body`);
  if (ta) ta.focus();
}

function _saveStickies() {
  clearTimeout(_stickySaveTimer);
  const path = currentPath;
  const snap = JSON.parse(JSON.stringify(_stickies));
  _stickySaveTimer = setTimeout(() => {
    try { pywebview.api.save_stickies(path, snap); } catch (_) {}
  }, 400);
}


