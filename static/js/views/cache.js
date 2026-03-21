// ==========================================
// LÓGICA DE LA VISTA: CACHÉ LOCAL
// ==========================================

/**
 * Consulta el servidor para obtener los últimos 2000 torrents y los renderiza en la galería.
 */
async function loadCacheGrid() {
    const grid = document.getElementById('cache-grid');
    if(!grid) return;
    
    grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando base de datos...</div>';
    
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        window.currentCacheData = data.torrents;
        renderCacheGrid(window.currentCacheData);
    } catch (e) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-red-500">Error al cargar la caché.</div>';
    }
}

/**
 * Pinta las tarjetas (cards) de la caché en el DOM.
 */
function renderCacheGrid(data) {
    const grid = document.getElementById('cache-grid');
    if(!grid) return;
    grid.innerHTML = '';
    
    if (data.length === 0) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500 flex flex-col items-center"><i class="fa-solid fa-box-open text-4xl mb-3"></i><p>La caché está vacía.</p></div>';
        return;
    }

    data.forEach(t => {
        const card = document.createElement('div');
        card.className = "group flex flex-col relative bg-[#11051c] rounded-lg border border-gray-800 shadow-md hover:border-yellow-500/50 transition-all duration-300";
        
        let displayName = "";
        if (t.tvdb_status === 'Listo' && t.tvdb_series_name_es) {
            displayName = t.tvdb_series_name_es;
        } else {
            displayName = t.enriched_title.replace(/\[.*?\]/g, '').trim();
        }

        let fansubTag = t.fansub_name ? `[${t.fansub_name}]` : `[UnionFansub]`;
        
        let iaIcon = '';
        if (t.ai_status === 'Listo') iaIcon = '<i class="fa-solid fa-circle-check text-green-500" title="IA Lista"></i>';
        else if (t.ai_status === 'Manual') iaIcon = '<i class="fa-solid fa-user-gear text-blue-400" title="IA Editada Manualmente"></i>';
        else if (t.ai_status === 'Error') iaIcon = '<i class="fa-solid fa-triangle-exclamation text-red-500" title="Error IA"></i>';
        else iaIcon = '<i class="fa-solid fa-hourglass-start text-gray-500" title="IA Pendiente"></i>';

        let tvdbIcon = '';
        if (t.tvdb_status === 'Listo') tvdbIcon = '<i class="fa-solid fa-circle-check text-green-500" title="TVDB Validado"></i>';
        else if (t.tvdb_status === 'Revisión Manual') tvdbIcon = '<i class="fa-solid fa-eye text-orange-400" title="Requiere Revisión Manual"></i>';
        else if (t.tvdb_status === 'Candidatos') tvdbIcon = '<i class="fa-solid fa-list-check text-purple-400" title="Candidatos Encontrados"></i>';
        else if (t.tvdb_status === 'Error' || t.tvdb_status === 'No Encontrado') tvdbIcon = '<i class="fa-solid fa-magnifying-glass-question text-red-400" title="No Encontrado"></i>';
        else tvdbIcon = '<i class="fa-solid fa-hourglass-start text-gray-500" title="TVDB Pendiente"></i>';

        const posterUrl = t.poster_url ? `/api/ui/poster?url=${encodeURIComponent(t.poster_url)}` : '/static/img/Kitsunarr-logo-512x512.png';

        card.innerHTML = `
            <div class="relative aspect-[2/3] rounded-t-lg overflow-hidden cursor-pointer bg-black" onclick="openInfoModalFromCache('${t.guid}')">
                <img src="${posterUrl}" alt="Poster" class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-110 opacity-90 group-hover:opacity-100">
                <div class="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                    <i class="fa-solid fa-eye text-3xl text-white"></i>
                </div>
                <div class="absolute top-2 right-2 flex flex-col gap-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                    <button onclick="event.stopPropagation(); openEditModal('${t.guid}')" class="bg-black/80 hover:bg-yellow-500 text-white p-2 rounded border border-gray-600 transition" title="Editar Manualmente">
                        <i class="fa-solid fa-pen text-xs"></i>
                    </button>
                    <button onclick="event.stopPropagation(); deleteCacheEntry('${t.guid}')" class="bg-black/80 hover:bg-red-500 text-white p-2 rounded border border-gray-600 transition" title="Eliminar">
                        <i class="fa-solid fa-trash text-xs"></i>
                    </button>
                </div>
            </div>
            <div class="p-3 flex flex-col justify-between flex-1">
                <div>
                    <div class="text-xs font-bold text-yellow-500 mb-1 truncate">${fansubTag}</div>
                    <div class="text-white text-sm font-bold line-clamp-2 leading-tight" title="${displayName}">${displayName}</div>
                </div>
                <div class="mt-3 flex justify-between items-center border-t border-gray-800 pt-2">
                    <div class="text-xs text-gray-500 font-mono">${t.guid}</div>
                    <div class="flex space-x-2 text-base">
                        ${iaIcon} ${tvdbIcon}
                    </div>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });
}

/**
 * Filtra los elementos visuales de la caché en el lado del cliente (Frontend).
 */
function filterCacheGrid() {
    const q = document.getElementById('cacheSearch').value.toLowerCase();
    const filtered = window.currentCacheData.filter(t => 
        t.enriched_title.toLowerCase().includes(q) || 
        t.guid.includes(q) || 
        (t.ai_translated_title && t.ai_translated_title.toLowerCase().includes(q)) ||
        (t.fansub_name && t.fansub_name.toLowerCase().includes(q)) ||
        (t.tvdb_series_name_es && t.tvdb_series_name_es.toLowerCase().includes(q))
    );
    renderCacheGrid(filtered);
}

/**
 * Busca el torrent en la memoria global y abre el modal de Ficha Dual.
 */
function openInfoModalFromCache(guid) {
    const t = window.currentCacheData.find(x => x.guid === guid);
    if(t) populateAndOpenDualModal(t);
}


// ==========================================
// MODAL DE EDICIÓN Y OMNIBOX TVDB
// ==========================================

/**
 * Abre el modal de edición manual para corregir el parseo de la IA o asignar
 * manualmente una serie desde TheTVDB.
 */
async function openEditModal(guid) {
    const t = window.currentCacheData.find(x => x.guid === guid);
    if(!t) return;
    document.getElementById('edit_cache_guid').value = t.guid;
    document.getElementById('edit_original_title').innerText = t.original_title;
    document.getElementById('edit_enriched_title').innerText = t.enriched_title;
    document.getElementById('edit_cache_title').value = t.ai_translated_title || t.enriched_title;
    document.getElementById('edit_cache_description').value = t.description || '';
    
    document.getElementById('edit_tvdb_search').value = '';
    document.getElementById('edit_tvdb_id').value = '';
    document.getElementById('omnibox_selected_badge').classList.add('hidden');
    
    document.getElementById('edit_id_size').innerText = `${t.guid} | ${formatBytes(t.size_bytes)}`;
    
    const p = document.getElementById('edit_poster');
    p.style.backgroundImage = t.poster_url ? `url('/api/ui/poster?url=${encodeURIComponent(t.poster_url)}')` : 'none';
    
    try {
        const resLocal = await fetch('/api/ui/tvdb/local_candidates');
        const dataLocal = await resLocal.json();
        if(dataLocal.success) window.currentLocalCandidates = dataLocal.results;
        
        const resSpecific = await fetch(`/api/ui/torrent/${guid}/candidates`);
        const dataSpecific = await resSpecific.json();
        if(dataSpecific.success) window.currentSpecificCandidates = dataSpecific.results;
        
        if (t.tvdb_id) {
            const existingShow = window.currentLocalCandidates.find(s => s.tvdb_id === t.tvdb_id);
            if (existingShow) selectOmniboxItem(existingShow.tvdb_id, existingShow.series_name_es);
            else selectOmniboxItem(t.tvdb_id, `ID: ${t.tvdb_id}`);
        }
    } catch(e) { console.error("Error cargando candidatos del Omnibox"); }

    document.getElementById('editCacheModal').classList.remove('hidden');
}

/**
 * Muestra el desplegable interactivo de búsqueda de TheTVDB en el modal de edición.
 */
function showOmnibox() { 
    filterOmnibox();
    document.getElementById('omnibox_dropdown').classList.remove('hidden'); 
}

/**
 * Oculta el desplegable con retraso para poder capturar los clics en sus elementos.
 */
function hideOmniboxDelayed() { 
    setTimeout(() => { 
        const d = document.getElementById('omnibox_dropdown');
        if(d) d.classList.add('hidden'); 
    }, 200); 
}

/**
 * Comprueba si el término de búsqueda coincide con alguno de los alias japoneses
 * o ingleses registrados en TheTVDB.
 */
function getAliasMatch(s, q) {
    if (!q || !s.aliases) return null;
    try {
        const aliasArr = JSON.parse(s.aliases);
        const matched = aliasArr.find(a => a.toLowerCase().includes(q));
        if (matched) return matched;
    } catch(e) {}
    return null;
}

/**
 * Filtra los resultados del Omnibox en base al texto, separando entre candidatos 
 * directos para ese torrent y candidatos de la biblioteca local general.
 */
function filterOmnibox() {
    const q = document.getElementById('edit_tvdb_search').value.toLowerCase().trim();
    const dropdown = document.getElementById('omnibox_dropdown');
    dropdown.innerHTML = '';
    
    let hasResults = false;

    const filteredSpecific = window.currentSpecificCandidates.filter(s => {
        if (!q) return true;
        s._matchedAlias = getAliasMatch(s, q);
        return (s.series_name_es && s.series_name_es.toLowerCase().includes(q)) || 
               (s.tvdb_id && s.tvdb_id.includes(q)) || 
               s._matchedAlias;
    });

    if (filteredSpecific.length > 0) {
        dropdown.innerHTML += `<div class="px-3 py-1 bg-yellow-900/40 text-yellow-500 text-[10px] font-bold uppercase tracking-wider border-b border-yellow-700/50">Candidatos Sugeridos por IA</div>`;
        filteredSpecific.forEach(s => dropdown.appendChild(createOmniboxItem(s, true)));
        hasResults = true;
    }

    const specificIds = window.currentSpecificCandidates.map(s => s.tvdb_id);
    const filteredLocal = window.currentLocalCandidates.filter(s => {
        if (specificIds.includes(s.tvdb_id)) return false;
        if (!q) return false;
        s._matchedAlias = getAliasMatch(s, q);
        return (s.series_name_es && s.series_name_es.toLowerCase().includes(q)) || 
               (s.tvdb_id && s.tvdb_id.includes(q)) ||
               s._matchedAlias;
    });

    if (filteredLocal.length > 0) {
        if (hasResults) dropdown.innerHTML += `<div class="h-1 bg-black border-t border-gray-800"></div>`;
        dropdown.innerHTML += `<div class="px-3 py-1 bg-blue-900/40 text-blue-400 text-[10px] font-bold uppercase tracking-wider border-b border-blue-800/50">Búsqueda en Biblioteca Local</div>`;
        filteredLocal.slice(0, 10).forEach(s => dropdown.appendChild(createOmniboxItem(s, false)));
        hasResults = true;
    }

    if (/^\d+$/.test(q)) {
        const manualDiv = document.createElement('div');
        manualDiv.className = "p-3 bg-gray-800 text-white text-xs font-bold hover:bg-gray-700 cursor-pointer border-t border-gray-600 transition";
        manualDiv.innerHTML = `<i class="fa-solid fa-cloud-arrow-down mr-2 text-yellow-500"></i> Forzar ID Manual: ${q}`;
        manualDiv.onclick = () => selectOmniboxItem(q, `ID Forzado: ${q}`);
        dropdown.appendChild(manualDiv);
        hasResults = true;
    }

    if(!hasResults) {
        dropdown.innerHTML = '<div class="p-3 text-xs text-gray-500 italic text-center">No se encontraron coincidencias.</div>';
    }
}

/**
 * Crea el elemento HTML para una opción sugerida en el Omnibox.
 */
function createOmniboxItem(s, isSuggested) {
    const div = document.createElement('div');
    const hoverClass = isSuggested ? "hover:bg-yellow-900/20" : "hover:bg-blue-900/30";
    div.className = `flex items-center p-2 cursor-pointer border-b border-gray-800 transition ${hoverClass}`;
    
    const badge = s.is_full_record 
        ? '<span class="ml-2 text-[10px] bg-green-900/50 text-green-400 px-1 rounded border border-green-700">Ficha Maestra</span>'
        : '<span class="ml-2 text-[10px] bg-purple-900/50 text-purple-400 px-1 rounded border border-purple-700">Candidato</span>';
        
    const aliasHtml = s._matchedAlias ? `<div class="text-[10px] text-purple-400 mt-1"><i class="fa-solid fa-tags mr-1"></i> Alias coincidente: <span class="font-bold">${s._matchedAlias}</span></div>` : '';

    div.innerHTML = `
        <div class="flex-1 overflow-hidden">
            <div class="text-sm text-white font-bold truncate">${s.series_name_es} ${badge}</div>
            ${aliasHtml}
            <div class="text-xs text-gray-500 font-mono mt-1">ID: ${s.tvdb_id} • Año: ${s.first_aired || '----'}</div>
        </div>
    `;
    div.onclick = () => selectOmniboxItem(s.tvdb_id, s.series_name_es);
    return div;
}

/**
 * Selecciona un ítem del Omnibox, actualiza el valor visual y oculta el desplegable.
 */
function selectOmniboxItem(tvdbId, displayName) {
    document.getElementById('edit_tvdb_id').value = tvdbId;
    document.getElementById('edit_tvdb_search').value = ''; 
    
    document.getElementById('omnibox_selected_text').innerText = displayName;
    document.getElementById('omnibox_selected_badge').classList.remove('hidden');
    document.getElementById('omnibox_dropdown').classList.add('hidden');
}

/**
 * Deselecciona el TVDB ID elegido permitiendo volver a buscar.
 */
function clearOmniboxSelection() {
    document.getElementById('edit_tvdb_id').value = '';
    document.getElementById('omnibox_selected_badge').classList.add('hidden');
    document.getElementById('edit_tvdb_search').focus();
}

/**
 * Envía a la API las modificaciones manuales hechas sobre el torrent.
 */
async function saveCacheEdit() {
    const guid = document.getElementById('edit_cache_guid').value;
    const title = document.getElementById('edit_cache_title').value.trim();
    const desc = document.getElementById('edit_cache_description').value.trim();
    const tvdbId = document.getElementById('edit_tvdb_id').value.trim(); 
    
    if(!title) return showToast("El título no puede estar vacío.", false);
    try {
        const res = await fetch(`/api/ui/cache/${guid}`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ai_translated_title: title, description: desc, tvdb_id: tvdbId })
        });
        const data = await res.json();
        if(data.success) { closeCacheModal(); showToast("Caché actualizada."); loadCacheGrid(); }
    } catch(e) { showToast("Error de red.", false); }
}

/**
 * Borra por completo un registro de la base de datos local.
 */
async function deleteCacheEntry(guid) {
    if(!confirm("¿Estás seguro de que quieres eliminar este torrent de la caché local?")) return;
    try {
        const res = await fetch(`/api/ui/cache/${guid}`, { method: 'DELETE' });
        const data = await res.json();
        if(data.success) {
            showToast("Torrent eliminado.");
            loadCacheGrid();
        }
    } catch(e) { showToast("Error de red.", false); }
}


// ==========================================
// EXPORTACIÓN E IMPORTACIÓN MASIVA
// ==========================================

function openExportModal() {
    const m = document.getElementById('exportModal');
    if (m) m.classList.remove('hidden');
}

function closeExportModal() {
    const m = document.getElementById('exportModal');
    if (m) m.classList.add('hidden');
}

/**
 * Dispara la descarga del fichero JSON (Bundle, Torrents o TVDB).
 */
function submitExport() {
    const selected = document.querySelector('input[name="export_type"]:checked');
    if (!selected) return showToast("Selecciona una opción de exportación.", false);
    
    showToast(`Generando archivo de exportación (${selected.value})...`);
    window.location.href = `/api/ui/cache/export?module=${selected.value}`;
    closeExportModal();
}

/**
 * Envía un archivo de exportación previo al servidor para inyectarlo en la base de datos local,
 * manteniendo las relaciones relacionales de TheTVDB intactas.
 */
async function handleImportCache(event) {
    const file = event.target.files[0];
    if(!file) return;
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        showToast("Analizando e importando datos relacionales...");
        const res = await fetch('/api/ui/cache/import', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if(data.success) {
            let msg = `Importación exitosa. ${data.total || data.count} registros insertados.`;
            if (data.missing_count > 0) msg += ` Descargando ${data.missing_count} series huérfanas en 2º plano.`;
            showToast(msg, true);
            loadCacheGrid();
        } else {
            showToast("Error: " + data.error, false);
        }
    } catch(e) { showToast("Error de red durante la importación.", false); }
    
    event.target.value = ''; 
}


// ==========================================
// PROCESAMIENTO IA POR LOTES (BATCH)
// ==========================================

/**
 * Abre el modal de procesamiento por lotes. Consulta la base de datos 
 * para mostrar únicamente los torrents que tienen estado "Pendiente" de IA.
 */
async function openBatchModal() {
    const list = document.getElementById('batch_list_container');
    const btn = document.getElementById('btn_submit_batch');
    const count = document.getElementById('batch_selected_count');
    
    list.innerHTML = '<div class="text-center text-gray-500 py-4"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando BD...</div>';
    document.getElementById('aiBatchModal').classList.remove('hidden');
    
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        const pending = data.torrents.filter(t => t.ai_status === 'Pendiente');
        
        list.innerHTML = ''; count.innerText = '0';
        btn.disabled = true; btn.classList.add('opacity-50', 'cursor-not-allowed');
        
        if(pending.length === 0) return list.innerHTML = '<div class="text-center text-gray-500 py-4">No hay torrents pendientes.</div>';
        
        pending.forEach(t => {
            const div = document.createElement('div');
            div.className = "flex items-center p-3 border border-gray-800 rounded bg-black hover:bg-gray-900 transition cursor-pointer";
            div.innerHTML = `
                <input type="checkbox" value="${t.guid}" class="batch-checkbox w-4 h-4 text-yellow-500 bg-gray-800 border-gray-600 rounded cursor-pointer" onchange="updateBatchCount()">
                <div class="ml-3 overflow-hidden">
                    <div class="text-sm text-white font-bold truncate">${t.enriched_title}</div>
                </div>`;
            div.onclick = (e) => { if(e.target.tagName !== 'INPUT') { const cb = div.querySelector('input'); cb.checked = !cb.checked; updateBatchCount(); } };
            list.appendChild(div);
        });
    } catch (e) { list.innerHTML = '<div class="text-center text-red-500">Error de red.</div>'; }
}

/**
 * Actualiza el contador de elementos seleccionados en el modal de procesamiento 
 * por lotes y bloquea el botón si se excede el límite máximo de envíos simultáneos.
 */
function updateBatchCount() {
    const checked = document.querySelectorAll('.batch-checkbox:checked').length;
    const btn = document.getElementById('btn_submit_batch');
    document.getElementById('batch_selected_count').innerText = checked;
    
    if (checked > 0 && checked <= 5) {
        btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed');
    } else {
        btn.disabled = true; btn.classList.add('opacity-50', 'cursor-not-allowed');
        if(checked > 5) showToast("Máximo 5 elementos por lote", false);
    }
}

/**
 * Envía la lista de GUIDs seleccionados en el modal de lotes al backend 
 * para forzar su procesamiento mediante la IA.
 */
async function submitBatchProcess() {
    const guids = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value);
    if(guids.length === 0 || guids.length > 5) return;
    
    closeModals(); showToast(`Enviando ${guids.length} torrents a procesar...`);
    try {
        const res = await fetch('/api/ui/ai/force_specific', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ guids })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Procesamiento enviado. Revisa la consola.");
            loadCacheGrid();
        }
    } catch(e) { showToast("Error de red.", false); }
}

/**
 * Cierra el modal de procesamiento por lotes.
 */
function closeBatchModal() { document.getElementById('aiBatchModal').classList.add('hidden'); }

// Al cargar la vista de caché, carga la cuadrícula de torrents
document.addEventListener("DOMContentLoaded", () => {
    if(document.getElementById('cache-grid')) {
        loadCacheGrid();
    }
});