/*
 * BLOQUE CACHE LOCAL Y ESTADOS TVDB
 */

/*
 * Funcion para traducir el estado oficial de TVDB a textos visibles en KITSUNARR.
 */
function translateTvdbStatus(status) {
    const map = {
        Ended: 'Finalizado',
        Continuing: 'Continua',
        Planned: 'Planificada',
        Upcoming: 'Próximamente',
        InProduction: 'En producción',
        Pilot: 'Piloto',
        Rumored: 'Rumoreada',
        Canceled: 'Cancelada',
        OnHiatus: 'En pausa'
    };
    return map[status] || status || 'Desconocido';
}
/*
 * Funcion de carga de series vinculadas y torrents pendientes desde la cache local.
 */
async function loadCacheGrid() {
    const grid = document.getElementById('cache-grid');
    if(!grid) return;
    
    grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando base de datos...</div>';
    
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        
        window.currentUnlinkedData = data.unlinked_torrents || [];
        window.currentSeriesData = data.linked_series || [];
        
        extractAndRenderGlobalTags(window.currentUnlinkedData);
        
        toggleBatchSelection();
        filterCacheGrid();
    } catch (e) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-red-500">Error al cargar la caché.</div>';
    }
}

/*
 * BLOQUE FILTRADO DE TAGS EN CACHE
 */

/*
 * Funcion para extraer etiquetas disponibles y pintar los filtros globales de la cache.
 */
function extractAndRenderGlobalTags(torrents) {
    const tagsSet = new Set();
    
    torrents.forEach(t => {
        if (t.tags) {
            try {
                const parsed = JSON.parse(t.tags);
                if (Array.isArray(parsed)) parsed.forEach(tag => tagsSet.add(tag));
            } catch(e) {}
        }
    });

    const tagsArray = Array.from(tagsSet).sort(sortTags);
    const container = document.getElementById('filter_tags_container');
    if (!container) return;
    
    container.innerHTML = '';
    
    if (tagsArray.length === 0) {
        container.innerHTML = '<span class="text-[10px] text-gray-600 italic">No hay etiquetas disponibles</span>';
        return;
    }

    tagsArray.forEach(tag => {
        const tData = getTagData(tag);
        const btn = document.createElement('button');
        btn.className = `px-3 py-1 rounded-full text-[10px] font-bold border transition-all ${tData.style} opacity-50 hover:opacity-100`;
        btn.innerHTML = `${tData.icon} ${tag}`;
        btn.onclick = () => toggleTagFilter(tag, btn);
        container.appendChild(btn);
    });
}
/*
 * Funcion para activar o desactivar un filtro de etiqueta en la vista de cache.
 */
function toggleTagFilter(tag, btnElement) {
    if (window.activeTagFilters.has(tag)) {
        window.activeTagFilters.delete(tag);
        btnElement.classList.remove('opacity-100', 'ring-2', 'ring-white');
        btnElement.classList.add('opacity-50');
    } else {
        window.activeTagFilters.add(tag);
        btnElement.classList.remove('opacity-50');
        btnElement.classList.add('opacity-100', 'ring-2', 'ring-white');
    }
    filterCacheGrid();
}
/*
 * Funcion para filtrar series y torrents por texto, alias y etiquetas activas.
 */
function filterCacheGrid() {
    const query = document.getElementById('cacheSearch') ? document.getElementById('cacheSearch').value.toLowerCase() : "";
    
    const filteredTorrents = window.currentUnlinkedData.filter(t => {
        const textMatch = t.original_title.toLowerCase().includes(query) || 
                          t.enriched_title.toLowerCase().includes(query) || 
                          t.guid.toLowerCase().includes(query);
                          
        let tagsMatch = true;
        if (window.activeTagFilters.size > 0) {
            if (!t.tags) {
                tagsMatch = false;
            } else {
                try {
                    const tTags = JSON.parse(t.tags);
                    for (let activeTag of window.activeTagFilters) {
                        if (!tTags.includes(activeTag)) {
                            tagsMatch = false;
                            break;
                        }
                    }
                } catch(e) {
                    tagsMatch = false;
                }
            }
        }
        
        return textMatch && tagsMatch;
    });

    const filteredSeries = window.currentSeriesData
        .map(s => {
            const aliases = extractSeriesAliases(s.aliases);
            const nameMatch = s.series_name_es?.toLowerCase().includes(query) ||
                              s.series_name_original?.toLowerCase().includes(query) ||
                              s.tvdb_id.toLowerCase().includes(query);

            let aliasMatched = false;
            let matchedAlias = null;
            if (query) {
                matchedAlias = aliases.find(a => a.toLowerCase().includes(query)) || null;
                aliasMatched = Boolean(matchedAlias);
            }

            return {
                ...s,
                _matches_query: !query || nameMatch || aliasMatched,
                _matched_by_alias: aliasMatched,
                _matched_alias: matchedAlias,
            };
        })
        .filter(s => s._matches_query);

    renderCacheGrid(filteredTorrents, filteredSeries);
}
/*
 * Funcion para normalizar alias de series TVDB usados en las busquedas de cache.
 */
function extractSeriesAliases(rawAliases) {
    if (!rawAliases) return [];

    if (Array.isArray(rawAliases)) {
        return rawAliases.map(a => String(a).trim()).filter(Boolean);
    }

    if (typeof rawAliases === 'string') {
        try {
            const parsed = JSON.parse(rawAliases);
            if (Array.isArray(parsed)) {
                return parsed.map(a => String(a).trim()).filter(Boolean);
            }
        } catch (e) {
            return rawAliases
                .split(',')
                .map(a => a.trim())
                .filter(Boolean);
        }
    }

    return [];
}
/*
 * Funcion de renderizado de etiquetas ordenadas dentro de una tarjeta de torrent.
 */
function renderCardTags(tagsJson) {
    if (!tagsJson) return '<span class="text-[9px] text-gray-700 italic px-1">Sin etiquetas</span>';
    
    try {
        const parsed = JSON.parse(tagsJson);
        if (!Array.isArray(parsed) || parsed.length === 0) return '<span class="text-[9px] text-gray-700 italic px-1">Sin etiquetas</span>';
        
        const sortedTags = parsed.sort(sortTags);
        
        return sortedTags.map(tag => {
            const tData = getTagData(tag);
            return `
                <span class="px-1.5 py-0.5 rounded text-[9px] font-bold border ${tData.style} flex items-center shadow-sm" title="${tag}">
                    <span class="mr-1">${tData.icon}</span> <span>${tag}</span>
                </span>
            `;
        }).join('');
    } catch (e) {
        return '<span class="text-[9px] text-red-900/50 italic px-1">Error JSON</span>';
    }
}

/*
 * BLOQUE RENDERIZADO DE CACHE
 */

/*
 * Funcion de renderizado de series validadas y torrents huerfanos en la cache local.
 */
function renderCacheGrid(torrents, series) {
    const grid = document.getElementById('cache-grid');
    if (!grid) return;
    grid.innerHTML = '';

    if (torrents.length === 0 && series.length === 0) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500">No se encontraron resultados en la caché local.</div>';
        return;
    }

    series.forEach(s => {
        const posterUrl = s.poster_path ? `/api/ui/poster?url=${encodeURIComponent(s.poster_path)}` : '/static/img/Kitsunarr-logo-512x512.png';
        const fallbackImg = "this.onerror=null; this.src='/static/img/Kitsunarr-logo-512x512.png';";

        let seasonLabel = 'T?';
        if (s.seasons_data) {
            try {
                const seasonsObj = JSON.parse(s.seasons_data);
                const nums = Object.keys(seasonsObj)
                    .map(n => parseInt(n, 10))
                    .filter(n => !Number.isNaN(n))
                    .sort((a, b) => a - b);
                if (nums.length) {
                    const first = nums[0];
                    const last = nums[nums.length - 1];
                    const isRange = nums.every((n, idx) => idx === 0 || n === nums[idx - 1] + 1);
                    seasonLabel = isRange && nums.length > 1 ? `T${first}-T${last}` : `T${nums.join(', T')}`;
                }
            } catch (e) {
                seasonLabel = 'T?';
            }
        }

        const yearLabel = s.first_aired ? String(s.first_aired).slice(0, 4) : '----';
        const seasonsAndYear = `${seasonLabel} • ${yearLabel}`;
        
        const isContinuing = s.status === 'Continuing';
        const isEnded = s.status === 'Ended';
        const statusColor = isContinuing
            ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.8)] animate-pulse'
            : (isEnded ? 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.8)]' : 'bg-gray-500');
        const statusTitle = translateTvdbStatus(s.status);

        const card = document.createElement('div');
        card.className = "k-card group relative flex flex-col justify-between overflow-hidden shadow-lg border-2 border-green-500/30 hover:border-green-500/80 transition-all bg-[#0d0415]";
        card.innerHTML = `
            <div class="relative aspect-[2/3] w-full overflow-hidden block bg-[#05080c]">
                <img src="${posterUrl}" onerror="${fallbackImg}" alt="Poster" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105 opacity-90 group-hover:opacity-100">

                <a href="/cache/series/${s.tvdb_id}" class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center z-20 cursor-pointer backdrop-blur-[2px]">
                    <span class="text-white font-bold bg-green-600/90 px-5 py-2.5 rounded-full shadow-[0_0_15px_rgba(22,163,74,0.5)] transform transition-transform group-hover:scale-110 flex items-center">
                        <i class="fa-solid fa-book-open mr-2"></i> Ver Serie
                    </span>
                </a>
            </div>
            
            <div class="p-3 flex flex-col flex-1 bg-gradient-to-b from-[#0a1a10] to-[#050d08]">
                
                <div class="poster-card-title mb-2" title="${s.series_name_es || s.series_name_original}">
                    ${s.series_name_es || s.series_name_original}
                </div>

                ${s._matched_by_alias ? `
                <div class="mb-2 text-[10px] font-bold text-blue-300 bg-blue-900/30 border border-blue-800/60 rounded px-2 py-1 truncate" title="Coincidencia por alias: ${s._matched_alias || ''}">
                    <i class="fa-solid fa-link mr-1"></i> Coincidencia por alias: ${s._matched_alias || 'Alias'}
                </div>
                ` : ''}
                
                <div class="flex justify-between items-center mt-auto pt-2 border-t border-green-900/30">
                    <div class="poster-card-meta text-green-600/70 font-mono bg-green-900/20 px-2 py-0.5 rounded border border-green-900/30 flex-1 mr-2" title="Temporadas y estreno">
                        <i class="fa-solid fa-layer-group mr-1"></i> ${seasonsAndYear}
                    </div>
                    
                    <div class="flex items-center space-x-2 shrink-0">
                        <div class="w-2 h-2 rounded-full ${statusColor}" title="${statusTitle}"></div>
                        <div class="text-[10px] font-bold text-green-400 bg-green-900/40 px-2 py-0.5 rounded shadow-inner" title="Torrents Vinculados">
                            <i class="fa-solid fa-link mr-1"></i> ${s.linked_torrents_count || 0}
                        </div>
                    </div>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });

    torrents.forEach(t => {
        const isFreeleech = t.freeleech_until && new Date(t.freeleech_until) > new Date();
        const displayName = getCleanTorrentTitle(t);
        const posterUrl = t.poster_url ? `/api/ui/poster?url=${encodeURIComponent(t.poster_url)}` : '/static/img/Kitsunarr-logo-512x512.png';
        const fallbackImg = "this.onerror=null; this.src='/static/img/Kitsunarr-logo-512x512.png';";
        const fansubName = t.fansub_name || 'TRACKER';
        
        let aiIcon = getAiBadge(t);
        let tvdbIcon = getTvdbBadge(t);
        let trackerBadge = isFreeleech ? `<div class="absolute top-2 left-2 bg-yellow-500 text-black text-[10px] font-black px-2 py-0.5 rounded shadow-lg z-30 tracking-wider">FREELEECH</div>` : '';

        const card = document.createElement('div');
        card.className = "k-card group relative flex flex-col justify-between overflow-hidden shadow-lg border border-gray-800 hover:border-yellow-500/50 transition-all bg-[#0d0415]";
        card.innerHTML = `
            ${trackerBadge}
            
            <div class="absolute top-2 right-2 z-30">
                <input type="checkbox" class="batch-checkbox w-4 h-4 text-yellow-500 bg-gray-900 border-gray-600 rounded focus:ring-yellow-500 cursor-pointer shadow-md" value="${t.guid}" onclick="toggleBatchSelection()">
            </div>
            
            <div class="relative aspect-[2/3] w-full overflow-hidden block bg-[#05080c]">
                <img src="${posterUrl}" onerror="${fallbackImg}" alt="Poster" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105 opacity-90 group-hover:opacity-100">
                
                <div class="absolute bottom-0 left-0 w-full bg-gradient-to-t from-black via-black/80 to-transparent p-2 pt-8 z-20 pointer-events-none">
                    <div class="text-[10px] font-bold uppercase tracking-widest drop-shadow-[0_2px_4px_rgba(0,0,0,0.6)]">
                        <div class="mb-1">
                            <span class="fansub-badge inline-flex items-center rounded-sm bg-yellow-500/15 border border-yellow-500/50 text-yellow-300 font-black tracking-widest">FANSUB</span>
                        </div>
                        <div class="fansub-shell inline-block rounded">
                            <span class="fansub-label text-yellow-300 tracking-widest">${fansubName}</span>
                        </div>
                    </div>
                </div>

                <div class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center space-x-6 z-20 backdrop-blur-[2px]">
                    <a href="/cache/torrent/${t.guid}" title="Ver Ficha" class="text-white hover:text-yellow-500 transition transform hover:scale-125">
                        <i class="fa-solid fa-eye text-4xl drop-shadow-lg"></i>
                    </a>
                    <a href="/cache/edit/${t.guid}" title="Editar Manualmente" class="text-white hover:text-blue-500 transition transform hover:scale-125">
                        <i class="fa-solid fa-pen text-4xl drop-shadow-lg"></i>
                    </a>
                </div>
            </div>
            
            <div class="p-3 flex flex-col flex-1 bg-gradient-to-b from-[#11051c] to-[#0a0310]">
                
                <div class="poster-card-title mb-2" title="${displayName}">
                    ${displayName}
                </div>
                
                <div class="flex justify-between items-center mb-3">
                    <div class="poster-card-meta text-gray-400 font-mono bg-black/50 px-2 py-0.5 rounded border border-gray-800 shadow-inner flex-1 mr-2" title="ID en el Tracker: ${t.guid}">
                        <i class="fa-solid fa-fingerprint mr-1"></i> ${t.guid}
                    </div>
                    <div class="flex space-x-1.5 text-sm shrink-0 bg-black/30 px-1.5 py-0.5 rounded border border-gray-800">
                        ${aiIcon} ${tvdbIcon}
                    </div>
                </div>

                <div class="flex flex-wrap gap-1 mt-auto pt-3 border-t border-gray-800/60">
                    ${renderCardTags(t.tags)}
                </div>
            </div>
        `;
        grid.appendChild(card);
    });

    if (typeof refreshPosterLayouts === 'function') {
        refreshPosterLayouts();
    }
}
/*
 * Funcion para devolver el indicador visual del estado de IA de un torrent.
 */
function getAiBadge(t) {
    if (t.ai_status === 'Listo') return '<i class="fa-solid fa-robot text-green-500" title="IA Completada"></i>';
    if (t.ai_status === 'Manual') return '<i class="fa-solid fa-user-gear text-blue-400" title="IA Editada Manualmente"></i>';
    if (t.ai_status === 'Error') return '<i class="fa-solid fa-circle-xmark text-red-500" title="Error IA"></i>';
    return '<i class="fa-solid fa-clock text-gray-500" title="IA Pendiente"></i>';
}
/*
 * Funcion para devolver el indicador visual del estado TVDB de un torrent.
 */
function getTvdbBadge(t) {
    if (t.tvdb_status === 'Listo') return '<i class="fa-solid fa-circle-check text-green-500" title="TVDB Validado"></i>';
    if (t.tvdb_status === 'Manual' || t.tvdb_status === 'Revisión Manual') return '<i class="fa-solid fa-user-gear text-blue-400" title="TVDB Editado Manualmente / Requiere Revisión"></i>';
    if (t.tvdb_status === 'Error' || t.tvdb_status === 'No Encontrado') return '<i class="fa-solid fa-circle-xmark text-red-500" title="Error TVDB / No Encontrado"></i>';
    return '<i class="fa-solid fa-clock text-gray-500" title="TVDB Pendiente"></i>';
}

/*
 * BLOQUE ACCIONES MASIVAS DE CACHE
 */

/*
 * Funcion para actualizar los controles de accion masiva segun la seleccion actual.
 */
function toggleBatchSelection() {
    const checked = document.querySelectorAll('.batch-checkbox:checked').length;
    const btnAi = document.getElementById('btn_batch_ai');
    const btnDel = document.getElementById('btn_batch_delete');
    
    if (checked > 0) {
        if(btnAi) { btnAi.disabled = false; btnAi.classList.remove('opacity-50', 'cursor-not-allowed'); }
        if(btnDel) { btnDel.disabled = false; btnDel.classList.remove('opacity-50', 'cursor-not-allowed'); }
    } else {
        if(btnAi) { btnAi.disabled = true; btnAi.classList.add('opacity-50', 'cursor-not-allowed'); }
        if(btnDel) { btnDel.disabled = true; btnDel.classList.add('opacity-50', 'cursor-not-allowed'); }
    }
}
/*
 * Funcion para confirmar el envio masivo de torrents seleccionados a la IA.
 */
async function openBatchModal() {
    const guids = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value);
    if (guids.length === 0) return showToast("Selecciona al menos un torrent.", false);
    const accepted = await appConfirm(
        `¿Enviar ${guids.length} torrent(s) a procesar por la IA ahora mismo?`,
        'Confirmar procesamiento IA'
    );
    if (!accepted) return;
    processBatchAI();
}
/*
 * Funcion para solicitar el procesamiento IA de los torrents seleccionados.
 */
async function processBatchAI() {
    const guids = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value);
    if(guids.length === 0) return;
    
    showToast(`Enviando ${guids.length} torrents a procesar por IA...`);
    
    try {
        const res = await fetch('/api/ui/ai/force_specific', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({guids: guids})
        });
        const data = await res.json();
        if(data.success) {
            showToast("Procesamiento enviado. Se recargará la vista en breve.");
            setTimeout(() => loadCacheGrid(), 2000);
        } else if (data.error) {
            showToast(data.error, false);
        }
    } catch(e) { showToast("Error de red.", false); }
}
/*
 * Funcion para borrar de la cache los torrents seleccionados.
 */
async function deleteBatchSelection() {
    const guids = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value);
    if(guids.length === 0) return;
    
    const accepted = await appConfirm(
        `¿Estás completamente seguro de ELIMINAR ${guids.length} torrents de la base de datos?\n\nEsta acción no se puede deshacer.`,
        'Confirmar eliminación'
    );
    if (!accepted) return;

    showToast(`Borrando ${guids.length} registros...`);
    
    try {
        const deletePromises = guids.map(guid => 
            fetch(`/api/ui/cache/${guid}`, { method: 'DELETE' })
        );
        
        await Promise.all(deletePromises);
        
        showToast(`Se han eliminado ${guids.length} registros correctamente.`);
        window.activeTagFilters.clear();
        loadCacheGrid();
        
    } catch(e) {
        showToast("Hubo un error de red borrando algunos registros.", false);
        loadCacheGrid();
    }
}

/*
 * BLOQUE IMPORTACION Y EXPORTACION DE CACHE
 */

/*
 * Funcion para abrir el modal de exportacion de datos de cache.
 */
function openExportModal() {
    document.getElementById('exportModal').classList.remove('hidden');
}
/*
 * Funcion para cerrar el modal de exportacion de datos de cache.
 */
function closeExportModal() {
    document.getElementById('exportModal').classList.add('hidden');
}
/*
 * Funcion para iniciar la descarga del backup de cache seleccionado.
 */
function submitExport() {
    const selected = document.querySelector('input[name="export_type"]:checked');
    const module = selected ? selected.value : 'bundle';

    window.location.href = `/api/ui/cache/export?module=${module}`;
    closeExportModal();
    showToast("Generando y descargando el archivo...");
}

/*
 * Funcion para abrir el modal de rehidratacion de fichas locales.
 */
function openRehydrateModal() {
    resetRehydrateProgressUi();
    document.getElementById('rehydrateModal').classList.remove('hidden');
}
/*
 * Funcion para cerrar el modal de rehidratacion de fichas locales.
 */
function closeRehydrateModal() {
    stopRehydratePolling();
    document.getElementById('rehydrateModal').classList.add('hidden');
}

/*
 * Funcion para limpiar el estado visual del panel de progreso de rehidratacion.
 */
function resetRehydrateProgressUi() {
    const panel = document.getElementById('rehydrate_progress_panel');
    const bar = document.getElementById('rehydrate_progress_bar');
    const pct = document.getElementById('rehydrate_progress_percent');
    const count = document.getElementById('rehydrate_progress_count');
    const remaining = document.getElementById('rehydrate_progress_remaining');
    const batch = document.getElementById('rehydrate_progress_batch_label');
    const msg = document.getElementById('rehydrate_progress_message');
    if (!panel || !bar || !pct || !count || !remaining || !batch || !msg) return;
    panel.classList.add('hidden');
    bar.style.width = '0%';
    pct.innerText = '0%';
    count.innerText = '0/0';
    remaining.innerText = 'Faltan 0';
    batch.innerText = 'Lote 0/0';
    msg.innerText = 'En espera';
    setRehydrateActionButtons('idle');
}

/*
 * Funcion para mostrar y pintar el progreso de rehidratacion segun el estado del job.
 */
function renderRehydrateProgress(job) {
    const panel = document.getElementById('rehydrate_progress_panel');
    const bar = document.getElementById('rehydrate_progress_bar');
    const pct = document.getElementById('rehydrate_progress_percent');
    const count = document.getElementById('rehydrate_progress_count');
    const remaining = document.getElementById('rehydrate_progress_remaining');
    const batch = document.getElementById('rehydrate_progress_batch_label');
    const msg = document.getElementById('rehydrate_progress_message');
    if (!panel || !bar || !pct || !count || !remaining || !batch || !msg) return;

    panel.classList.remove('hidden');

    const batchPercent = Number(job.batch_percent || 0);
    const processed = Number(job.processed || 0);
    const total = Number(job.total || 0);
    const remainingCount = Number(job.remaining_count || 0);
    const currentBatch = Number(job.current_batch || 0);
    const totalBatches = Number(job.total_batches || 0);

    bar.style.width = `${Math.max(0, Math.min(100, batchPercent))}%`;
    pct.innerText = `${Math.max(0, Math.min(100, batchPercent))}%`;
    count.innerText = `${processed}/${total}`;
    remaining.innerText = `Faltan ${remainingCount}`;
    batch.innerText = `Lote ${currentBatch}/${totalBatches}`;
    msg.innerText = job.message || 'Procesando';
}

/*
 * Funcion para alternar controles del modal durante la ejecucion de rehidratacion.
 */
function setRehydrateModalBusyState(isBusy) {
    const submitBtn = document.getElementById('btn_rehydrate_submit');
    const radios = document.querySelectorAll('input[name="rehydrate_type"]');
    if (submitBtn) {
        submitBtn.disabled = isBusy;
        submitBtn.classList.toggle('opacity-50', isBusy);
        submitBtn.classList.toggle('cursor-not-allowed', isBusy);
    }
    radios.forEach(r => { r.disabled = isBusy; });
}

/*
 * Funcion para alternar los botones de iniciar, cancelar y reanudar
 * segun el estado actual del job de rehidratacion.
 */
function setRehydrateActionButtons(state) {
    const submitBtn = document.getElementById('btn_rehydrate_submit');
    const cancelBtn = document.getElementById('btn_rehydrate_cancel');
    const resumeBtn = document.getElementById('btn_rehydrate_resume');
    if (!submitBtn || !cancelBtn || !resumeBtn) return;

    if (state === 'running') {
        submitBtn.classList.add('hidden');
        cancelBtn.classList.remove('hidden');
        resumeBtn.classList.add('hidden');
        return;
    }
    if (state === 'cancelled') {
        submitBtn.classList.add('hidden');
        cancelBtn.classList.add('hidden');
        resumeBtn.classList.remove('hidden');
        return;
    }
    submitBtn.classList.remove('hidden');
    cancelBtn.classList.add('hidden');
    resumeBtn.classList.add('hidden');
}

/*
 * Funcion para detener el polling del progreso de rehidratacion.
 */
function stopRehydratePolling() {
    if (window.rehydratePollTimer) {
        clearInterval(window.rehydratePollTimer);
        window.rehydratePollTimer = null;
    }
}

/*
 * Funcion para consultar periodicamente el estado del job de rehidratacion.
 */
function startRehydratePolling(jobId) {
    stopRehydratePolling();
    window.activeRehydrateJobId = jobId;

    const pollOnce = async () => {
        try {
            const res = await fetch(`/api/ui/cache/rehydrate/${jobId}/status`);
            const data = await res.json();
            if (!data.success) {
                stopRehydratePolling();
                setRehydrateModalBusyState(false);
                showToast(data.error || "No se pudo consultar el progreso.", false);
                return;
            }

            const job = data.job || {};
            renderRehydrateProgress(job);

            if (job.status === 'completed') {
                stopRehydratePolling();
                setRehydrateModalBusyState(false);
                setRehydrateActionButtons('idle');
                const summary = `Rehidratación completada: ${job.updated || 0} actualizadas, ${job.unchanged || 0} sin cambios, ${job.skipped || 0} omitidas, ${job.failed || 0} fallidas.`;
                showToast(summary, (job.failed || 0) === 0);
                loadCacheGrid();
                return;
            }

            if (job.status === 'failed') {
                stopRehydratePolling();
                setRehydrateModalBusyState(false);
                setRehydrateActionButtons('idle');
                showToast(job.error || "La rehidratación falló.", false);
                return;
            }

            if (job.status === 'cancelled') {
                stopRehydratePolling();
                setRehydrateModalBusyState(false);
                setRehydrateActionButtons('cancelled');
                showToast(`Rehidratación pausada. Pendientes: ${job.remaining_count || 0}.`);
                return;
            }

            if (job.status === 'running') {
                setRehydrateActionButtons('running');
            }
        } catch (e) {
            stopRehydratePolling();
            setRehydrateModalBusyState(false);
            setRehydrateActionButtons('idle');
            showToast("Error de red consultando progreso.", false);
        }
    };

    pollOnce();
    window.rehydratePollTimer = setInterval(pollOnce, 900);
}
/*
 * Funcion para iniciar la rehidratacion de fichas seleccionadas o de toda la cache.
 */
async function submitRehydrate() {
    const selected = document.querySelector('input[name="rehydrate_type"]:checked');
    const mode = selected ? selected.value : 'selected';
    const selectedGuids = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value);

    if (mode === 'selected' && selectedGuids.length === 0) {
        showToast("Marca al menos una ficha para rehidratar.", false);
        return;
    }

    const confirmMessage = mode === 'all'
        ? '¿Rehidratar TODAS las fichas de la caché local? Este proceso puede tardar varios minutos.'
        : `¿Rehidratar ${selectedGuids.length} ficha(s) seleccionada(s)?`;
    const accepted = await appConfirm(confirmMessage, 'Confirmar rehidratación');
    if (!accepted) return;

    setRehydrateModalBusyState(true);
    setRehydrateActionButtons('running');
    showToast("Iniciando rehidratación de fichas...");

    try {
        const payload = { mode: mode, guids: mode === 'selected' ? selectedGuids : [] };
        const res = await fetch('/api/ui/cache/rehydrate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (!data.success) {
            setRehydrateModalBusyState(false);
            setRehydrateActionButtons('idle');
            showToast(data.error || "No se pudo rehidratar la caché.", false);
            return;
        }
        startRehydratePolling(data.job_id);
    } catch (e) {
        setRehydrateModalBusyState(false);
        setRehydrateActionButtons('idle');
        showToast("Error de red durante la rehidratación.", false);
    }
}

/*
 * Funcion para solicitar la cancelacion de un job de rehidratacion en curso.
 */
async function cancelRehydrateJob() {
    const jobId = window.activeRehydrateJobId;
    if (!jobId) return;

    try {
        const res = await fetch(`/api/ui/cache/rehydrate/${jobId}/cancel`, { method: 'POST' });
        const data = await res.json();
        if (!data.success) {
            showToast(data.error || "No se pudo cancelar el job.", false);
            return;
        }
        showToast("Cancelación solicitada. Esperando cierre de ficha actual...");
    } catch (e) {
        showToast("Error de red al cancelar rehidratación.", false);
    }
}

/*
 * Funcion para reanudar un job cancelado desde los elementos pendientes.
 */
async function resumeRehydrateJob() {
    const jobId = window.activeRehydrateJobId;
    if (!jobId) return;

    setRehydrateModalBusyState(true);
    setRehydrateActionButtons('running');

    try {
        const res = await fetch(`/api/ui/cache/rehydrate/${jobId}/resume`, { method: 'POST' });
        const data = await res.json();
        if (!data.success) {
            setRehydrateModalBusyState(false);
            setRehydrateActionButtons('cancelled');
            showToast(data.error || "No se pudo reanudar el job.", false);
            return;
        }
        showToast("Reanudación iniciada.");
        startRehydratePolling(jobId);
    } catch (e) {
        setRehydrateModalBusyState(false);
        setRehydrateActionButtons('cancelled');
        showToast("Error de red al reanudar.", false);
    }
}
/*
 * Funcion para importar un backup JSON en la cache local de KITSUNARR.
 */
async function handleImportCache(event) {
    const file = event.target.files[0];
    if (!file) return;

    const accepted = await appConfirm(
        `¿Deseas importar el archivo "${file.name}"?\n\nKitsunarr añadirá solo registros nuevos, no sobrescribirá fichas existentes, adaptará la URL de descarga a esta instalación, conservará el info hash y reiniciará la telemetría del cliente.`,
        'Confirmar importación'
    );
    if (!accepted) {
        event.target.value = '';
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    showToast("Importando archivo, esto puede tardar un momento...");

    try {
        const res = await fetch('/api/ui/cache/import', {
            method: 'POST',
            body: formData
        });
        
        const data = await res.json();
        
        if (data.success) {
            let msg = "Importación completada:\n";
            if (data.imported.torrents > 0) msg += `- ${data.imported.torrents} Torrents\n`;
            if (data.imported.shows > 0) msg += `- ${data.imported.shows} Series (TVDB)\n`;
            if (data.imported.episodes > 0) msg += `- ${data.imported.episodes} Episodios\n`;
            if (data.imported.candidates > 0) msg += `- ${data.imported.candidates} Enlaces (Candidatos)\n`;
            
            if (
                (data.imported.torrents || 0) === 0 &&
                (data.imported.shows || 0) === 0 &&
                (data.imported.episodes || 0) === 0 &&
                (data.imported.candidates || 0) === 0
            ) {
                msg = "No se ha importado ningún dato nuevo (todo existía ya en tu base de datos).";
            }
            
            showToast(msg);
            setTimeout(() => location.reload(), 2000);
        } else {
            showToast("Error importando el archivo: " + data.error, false);
        }
    } catch (e) {
        showToast("Error de red al intentar importar.", false);
    }

    event.target.value = '';
}

/*
 * BLOQUE INICIALIZACION
 */

/*
 * Funcion de inicializacion de la cache local cuando la cuadricula esta disponible.
 */
document.addEventListener("DOMContentLoaded", () => {
    if(document.getElementById('cache-grid')) {
        loadCacheGrid();
    }
});
