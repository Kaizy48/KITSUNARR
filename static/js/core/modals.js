// ==========================================
// GESTIÓN DE MODALES GENÉRICOS
// ==========================================

// Variable global para controlar el bucle de telemetría de qBittorrent
window.currentTelemetryInterval = null;

/**
 * Oculta todos los modales genéricos y de configuración de la interfaz 
 * añadiendo la clase 'hidden' de Tailwind.
 */
function closeModals() {
    const modals = ['selectorModal', 'configModal', 'restartModal', 'aiWarningModal', 'aiBatchModal'];
    modals.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });
}

/**
 * Oculta específicamente el modal de Información Dual (Tracker/TVDB).
 * DETIENE el bucle de telemetría para no saturar al servidor y qBittorrent.
 */
function closeInfoModal() {
    if (window.currentTelemetryInterval) {
        clearInterval(window.currentTelemetryInterval);
        window.currentTelemetryInterval = null;
    }
    
    const el = document.getElementById('infoCacheModal');
    if (el) el.classList.add('hidden');
}

/**
 * Oculta específicamente el modal de Edición Manual de la caché.
 */
function closeCacheModal() {
    const el = document.getElementById('editCacheModal');
    if (el) el.classList.add('hidden');
}


// ==========================================
// LÓGICA DE LA FICHA DUAL (INFO MODAL)
// ==========================================

/**
 * Alterna la vista del modal de información entre los datos crudos del tracker 
 * y los metadatos enriquecidos por TheTVDB/IA. Cambia estilos, insignias y el póster.
 */
function switchInfoTab(tabName) {
    const viewTracker = document.getElementById('info_view_tracker');
    const viewTvdb = document.getElementById('info_view_tvdb');
    const btnTracker = document.getElementById('btn_tab_tracker');
    const btnTvdb = document.getElementById('btn_tab_tvdb');
    const badge = document.getElementById('info_poster_badge');
    const poster = document.getElementById('info_poster');
    const t = window.currentActiveTorrent;

    if (!viewTracker || !viewTvdb || !t) return;

    if (tabName === 'tracker') {
        viewTracker.classList.remove('hidden'); viewTvdb.classList.add('hidden');
        btnTracker.className = "flex-1 py-2 text-sm font-bold text-black rounded-md shadow-sm bg-yellow-500";
        btnTvdb.className = "flex-1 py-2 text-sm font-bold text-gray-400 hover:text-white rounded-md";
        badge.innerText = "TRACKER"; badge.className = "absolute top-8 left-8 bg-black/80 text-yellow-500 text-xs font-bold px-2 py-1 rounded backdrop-blur-md border border-gray-700 z-10 uppercase tracking-widest shadow-lg";
        poster.style.backgroundImage = t.poster_url ? `url('/api/ui/poster?url=${encodeURIComponent(t.poster_url)}')` : 'none';
    } else {
        viewTracker.classList.add('hidden'); viewTvdb.classList.remove('hidden');
        btnTracker.className = "flex-1 py-2 text-sm font-bold text-gray-400 hover:text-white rounded-md";
        btnTvdb.className = "flex-1 py-2 text-sm font-bold text-black rounded-md shadow-sm bg-blue-500";
        badge.innerText = "THETVDB"; badge.className = "absolute top-8 left-8 bg-black/80 text-blue-400 text-xs font-bold px-2 py-1 rounded backdrop-blur-md border border-blue-700 z-10 uppercase tracking-widest shadow-lg";
        
        let tvdbImg = null;
        if (t.tvdb_status === 'Listo' && t.tvdb_poster_path) {
            tvdbImg = t.tvdb_poster_path;
        } else if(t.tvdb_candidates) {
            try {
                const cands = JSON.parse(t.tvdb_candidates);
                const match = cands.find(c => c.tvdb_id == t.tvdb_id) || cands[0];
                if(match && match.image_url) tvdbImg = match.image_url;
            } catch(e) {}
        }
        poster.style.backgroundImage = tvdbImg ? `url('/api/ui/poster?url=${encodeURIComponent(tvdbImg)}')` : 'none';
    }
}

/**
 * Recibe un objeto torrent completo, inyecta todos sus datos técnicos, estados de IA 
 * y de TheTVDB en el DOM del modal de Ficha Dual, e inicia la telemetría en vivo.
 */
function populateAndOpenDualModal(torrentObj) {
    window.currentActiveTorrent = torrentObj;
    if (!torrentObj) return;

    if (window.currentTelemetryInterval) {
        clearInterval(window.currentTelemetryInterval);
        window.currentTelemetryInterval = null;
    }

    document.getElementById('info_original_title').innerText = torrentObj.original_title || '-';
    document.getElementById('info_enriched_title').innerText = torrentObj.enriched_title || '-';
    document.getElementById('info_guid').innerText = torrentObj.guid || '-';
    document.getElementById('info_size').innerText = formatBytes(torrentObj.size_bytes);
    document.getElementById('info_desc_tracker').innerText = torrentObj.description || 'Sin sinopsis disponible del tracker.';
    
    let flText = '<span class="text-gray-500">No activo</span>';
    if (torrentObj.freeleech_until) {
        const flDate = new Date(torrentObj.freeleech_until);
        const now = new Date();
        if (flDate > now) {
            const diffMs = flDate - now;
            const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
            const diffHrs = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
            flText = `<span class="text-green-400 font-bold"><i class="fa-solid fa-gift mr-1 text-pink-500"></i> Sí (${diffDays}d ${diffHrs}h restantes)</span>`;
        }
    }
    document.getElementById('info_freeleech').innerHTML = flText;

    document.getElementById('info_ai_title').innerText = torrentObj.ai_translated_title || 'Pendiente de procesar...';
    
    let aiStatusHtml = `<span class="text-gray-500">${torrentObj.ai_status}</span>`;
    if (torrentObj.ai_status === 'Listo') aiStatusHtml = `<span class="text-green-500 font-bold"><i class="fa-solid fa-check mr-1"></i> Listo</span>`;
    else if (torrentObj.ai_status === 'Pendiente') aiStatusHtml = `<span class="text-yellow-500 font-bold"><i class="fa-solid fa-clock mr-1"></i> Pendiente</span>`;
    else if (torrentObj.ai_status === 'Manual') aiStatusHtml = `<span class="text-purple-400 font-bold"><i class="fa-solid fa-user-pen mr-1"></i> Editado Manual</span>`;
    else if (torrentObj.ai_status === 'Error') aiStatusHtml = `<span class="text-red-500 font-bold"><i class="fa-solid fa-xmark mr-1"></i> Error</span>`;
    document.getElementById('info_ai_status').innerHTML = aiStatusHtml;

    document.getElementById('info_tvdb_id').innerText = torrentObj.tvdb_id || 'No asignado';
    let tvdbStatusHtml = `<span class="text-gray-500">${torrentObj.tvdb_status}</span>`;
    if (torrentObj.tvdb_status === 'Listo') tvdbStatusHtml = `<span class="text-blue-400 font-bold"><i class="fa-solid fa-check-circle mr-1"></i> Validado</span>`;
    else if (torrentObj.tvdb_status === 'Candidatos') tvdbStatusHtml = `<span class="text-purple-400 font-bold"><i class="fa-solid fa-list-ul mr-1"></i> Candidatos Encontrados</span>`;
    else if (torrentObj.tvdb_status === 'Revisión Manual') tvdbStatusHtml = `<span class="text-orange-400 font-bold"><i class="fa-solid fa-triangle-exclamation mr-1"></i> Requiere Revisión</span>`;
    document.getElementById('info_tvdb_status').innerHTML = tvdbStatusHtml;

    const linkTvdb = document.getElementById('link_tvdb_external');
    if (torrentObj.tvdb_id && torrentObj.tvdb_status === 'Listo') {
        linkTvdb.href = `https://thetvdb.com/dereferrer/series/${torrentObj.tvdb_id}`;
        linkTvdb.classList.remove('pointer-events-none', 'opacity-50');
        linkTvdb.classList.add('hover:text-white');
    } else {
        linkTvdb.href = "#";
        linkTvdb.classList.add('pointer-events-none', 'opacity-50');
    }

    const btnForceAi = document.getElementById('btn_force_ai');
    btnForceAi.onclick = () => forceSingleAIProcess(torrentObj.guid);

    const btnForceTvdb = document.getElementById('btn_force_tvdb');
    btnForceTvdb.onclick = () => forceSingleTVDBProcess(torrentObj.guid);

    const posterContainer = document.getElementById('info_poster');
    const placeholder = document.getElementById('info_poster_placeholder');
    if (torrentObj.poster_url) {
        posterContainer.style.backgroundImage = `url('/api/ui/poster?url=${encodeURIComponent(torrentObj.poster_url)}')`;
        placeholder.classList.add('hidden');
    } else {
        posterContainer.style.backgroundImage = `url('/static/img/Kitsunarr-logo-512x512.png')`;
        placeholder.classList.add('hidden');
    }

    // =======================================================
    // 2. CONFIGURACIÓN DE TELEMETRÍA (qBittorrent)
    // =======================================================
    const telemetryContainer = document.getElementById('telemetry_container');
    const telemetryMissing = document.getElementById('telemetry_missing');
    const btnCalcHash = document.getElementById('btn_calc_hash');
    
    document.getElementById('info_qb_progress_bar').style.width = '0%';
    document.getElementById('info_qb_progress_text').innerText = '0.0%';
    document.getElementById('info_qb_dlspeed').innerText = '0 B/s';
    document.getElementById('info_qb_status').innerText = 'Cargando...';
    document.getElementById('info_qb_eta').innerText = '-';
    
    if (!torrentObj.info_hash) {
        telemetryContainer.classList.add('hidden');
        telemetryMissing.classList.remove('hidden');
        btnCalcHash.classList.remove('hidden');
    } else {
        telemetryContainer.classList.remove('hidden');
        telemetryMissing.classList.add('hidden');
        btnCalcHash.classList.add('hidden');
        
        fetchTelemetryData(torrentObj.guid);
        window.currentTelemetryInterval = setInterval(() => {
            fetchTelemetryData(torrentObj.guid);
        }, 3000);
    }
    // =======================================================

    switchInfoTab('tracker');
    document.getElementById('infoCacheModal').classList.remove('hidden');
}


// ==========================================
// ACCIONES RÁPIDAS (Botonera Modal Dual)
// ==========================================

/**
 * Envía la orden a la API para procesar un único torrent saltándose la cola.
 * Utilizado desde el modal de la ficha dual.
 */
async function forceSingleAIProcess(guid) {
    showToast("Enviando petición a la IA...");
    try {
        const res = await fetch('/api/ui/ai/force_specific', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ guids: [guid] })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Procesamiento completado. Actualiza la vista.");
        } else {
            showToast(data.error, false);
        }
    } catch(e) {
        showToast("Error de red forzando IA.", false);
    }
}

/**
 * Obliga al trabajador de TheTVDB a volver a raspar candidatos 
 * para un torrent en específico.
 */
async function forceSingleTVDBProcess(guid) {
    showToast("Forzando búsqueda de candidatos en TVDB...");
    try {
        const res = await fetch('/api/ui/tvdb/force_specific', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ guids: [guid] })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Búsqueda en TVDB finalizada. Refresca la vista.");
        } else { showToast(data.error, false); }
    } catch(e) { showToast("Error de red forzando TVDB.", false); }
}

// ==========================================
// TELEMETRÍA: QBITTORRENT EN VIVO
// ==========================================

/**
 * Hace una petición GET al endpoint de telemetría de Kitsunarr para
 * obtener el progreso y velocidades actuales desde qBittorrent, y
 * anima los elementos visuales del modal.
 */
async function fetchTelemetryData(guid) {
    const progressBar = document.getElementById('info_qb_progress_bar');
    const progressText = document.getElementById('info_qb_progress_text');
    const speedText = document.getElementById('info_qb_dlspeed');
    const statusText = document.getElementById('info_qb_status');
    const etaText = document.getElementById('info_qb_eta');
    
    try {
        const res = await fetch(`/api/ui/torrent/${guid}/telemetry`);
        const data = await res.json();
        
        if (data.success && data.telemetry) {
            const t = data.telemetry;
            const percent = (t.progress * 100).toFixed(1);
            
            progressBar.style.width = `${percent}%`;
            progressText.innerText = `${percent}%`;
            speedText.innerText = `${formatBytes(t.download_speed)}/s`;
            
            const stateMap = {
                'downloading': 'Descargando ⬇️',
                'stalledDL': 'Estancado (DL)',
                'stalledUP': 'Sedeando ⬆️',
                'uploading': 'Subiendo ⬆️',
                'pausedDL': 'Pausado ⏸️',
                'pausedUP': 'Completado ⏸️',
                'queuedDL': 'En Cola',
                'checkingDL': 'Comprobando',
                'checkingUP': 'Comprobando',
                'allocating': 'Asignando Espacio'
            };
            statusText.innerText = stateMap[t.client_status] || t.client_status;
            
            if (t.eta === 8640000 || t.client_status.includes('paused') || t.client_status.includes('UP')) {
                etaText.innerText = '∞';
            } else {
                etaText.innerText = formatETA(t.eta);
            }
            
            if (t.progress === 1) {
                progressBar.className = "bg-green-500 h-2 rounded-full transition-all duration-500";
            } else if (t.client_status.includes('paused')) {
                progressBar.className = "bg-gray-500 h-2 rounded-full transition-all duration-500";
            } else {
                progressBar.className = "bg-blue-500 h-2 rounded-full transition-all duration-500";
            }
            
        } else {
            statusText.innerText = 'No encontrado en Cliente';
            progressBar.style.width = '0%';
            speedText.innerText = '0 B/s';
            etaText.innerText = '-';
        }
    } catch (e) {
        statusText.innerText = 'Error de Conexión';
    }
}

/**
 * Función que se dispara al pulsar el botón de "Obtener Hash".
 * Llama al backend para redescargar silenciosamente el torrent,
 * calcular su hash e iniciar la telemetría automáticamente.
 */
async function calculateHash() {
    const t = window.currentActiveTorrent;
    if (!t || t.info_hash) return;

    const btn = document.getElementById('btn_calc_hash');
    const originalHtml = btn.innerHTML;
    
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Calculando...';
    btn.disabled = true;

    try {
        const res = await fetch(`/api/ui/torrent/${t.guid}/calculate_hash`, { method: 'POST' });
        const data = await res.json();
        
        if (data.success) {
            showToast("¡Hash calculado y vinculado correctamente!");
            t.info_hash = data.info_hash;
            
            document.getElementById('telemetry_container').classList.remove('hidden');
            document.getElementById('telemetry_missing').classList.add('hidden');
            btn.classList.add('hidden');
            
            fetchTelemetryData(t.guid);
            window.currentTelemetryInterval = setInterval(() => {
                fetchTelemetryData(t.guid);
            }, 3000);
            
        } else {
            showToast(`Error: ${data.error}`, false);
            btn.innerHTML = originalHtml;
            btn.disabled = false;
        }
    } catch (e) {
        showToast("Error de red intentando calcular el hash.", false);
        btn.innerHTML = originalHtml;
        btn.disabled = false;
    }
}

/**
 * Helper para formatear los segundos restantes (ETA) a un formato humano (1h 23m).
 */
function formatETA(seconds) {
    if (seconds <= 0) return '0s';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    
    if (h > 24) return `+${Math.floor(h/24)} días`;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}