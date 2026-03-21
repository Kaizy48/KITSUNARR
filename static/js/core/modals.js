// ==========================================
// GESTIÓN DE MODALES GENÉRICOS
// ==========================================

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
 */
function closeInfoModal() {
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
 * y de TheTVDB en el DOM del modal de Ficha Dual, y finalmente lo muestra en pantalla.
 */
function populateAndOpenDualModal(torrentObj) {
    window.currentActiveTorrent = torrentObj;
    if (!torrentObj) return;

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