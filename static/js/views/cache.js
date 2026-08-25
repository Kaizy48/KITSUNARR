/*
 * BLOQUE CACHE LOCAL Y ESTADOS TVDB
 */

/*
 * Estado estable de la vista: la seleccion vive fuera del DOM para sobrevivir a
 * filtros y repintados; la rehidratacion conserva su job activo aunque se cierre
 * el dialogo. Todos los temporizadores se almacenan para evitar pollings solapados.
 */
window.selectedCacheGuids = window.selectedCacheGuids instanceof Set ? window.selectedCacheGuids : new Set();
window.activeRehydrateJobId = window.activeRehydrateJobId || null;
window.lastRehydrateJob = window.lastRehydrateJob || null;
window.rehydratePollTimer = null;
window.rehydratePollGeneration = Number(window.rehydratePollGeneration || 0);
window.rehydratePollController = null;
window.rehydratePollInFlight = false;
window.rehydrateNetworkFailures = 0;
window.rehydrateDiscoveryFailures = 0;
window.rehydrateStartFailures = 0;
window.rehydrateCancelFailures = 0;
window.rehydrateModalOpener = null;
window.rehydrateBodyOverflow = '';
window.rehydratePreferredScope = null;
window.rehydrateStopPending = false;
window.rehydrateCancelRetryTimer = null;
window.rehydrateStartRetryTimer = null;
window.rehydrateDiscoveryRetryTimer = null;
window.pendingRehydratePayload = null;
window.rehydrateTerminalToastJobId = null;

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
 * Objetivo: cargar desde /api/ui/cache las series vinculadas y los torrents
 * individuales que alimentan la cuadricula. No recibe parametros ni devuelve un
 * valor util; actualiza los datos globales, depura GUID ya inexistentes de la
 * seleccion persistente y vuelve a aplicar filtros. Los errores HTTP/JSON/red se
 * representan dentro de la cuadricula. Depende de extractAndRenderGlobalTags,
 * pruneCacheSelection, toggleBatchSelection y filterCacheGrid.
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

        pruneCacheSelection(window.currentUnlinkedData);
        
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
 * Extrae tags de los torrents y pinta filtros seguros. Recibe la colección, no
 * devuelve valor y reemplaza el contenedor; JSON corrupto se omite. Depende de
 * getTagData/sortTags/toggleTagFilter y usa textContent para texto remoto.
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

    /*
     * Crea un botón de filtro por cada tag recibido y no devuelve valor. Añade
     * nodos al contenedor, conserva el texto remoto mediante textContent y asigna
     * su handler; cualquier fallo DOM o de getTagData se propaga al render padre.
     * Depende de getTagData, toggleTagFilter y del container ya resuelto.
     */
    tagsArray.forEach(tag => {
        const tData = getTagData(tag);
        const btn = document.createElement('button');
        btn.className = `px-3 py-1 rounded-full text-[10px] font-bold border transition-all ${tData.style} opacity-50 hover:opacity-100`;
        btn.textContent = `${tData.icon} ${String(tag)}`;
        /*
         * Alterna el filtro de este tag. Sin entradas/salida explícitas; captura
         * tag/botón, muta filtros/DOM y delega el repintado en toggleTagFilter;
         * cualquier error de ese helper se propaga al despachador del navegador.
         */
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
 * Convierte un JSON de tags en badges HTML seguros para una tarjeta. Recibe texto,
 * devuelve markup o placeholder y no muta DOM/estado; ordena la copia parseada,
 * escapa valores y captura estructura/JSON inválidos. Depende de tags.js/utils.js.
 */
function renderCardTags(tagsJson) {
    if (!tagsJson) return '<span class="text-[9px] text-gray-700 italic px-1">Sin etiquetas</span>';
    
    try {
        const parsed = JSON.parse(tagsJson);
        if (!Array.isArray(parsed) || parsed.length === 0) return '<span class="text-[9px] text-gray-700 italic px-1">Sin etiquetas</span>';
        
        const sortedTags = parsed.sort(sortTags);
        
        /*
         * Convierte cada tag a markup de badge. Recibe el valor, devuelve string
         * escapado, no tiene efectos y depende de getTagData/escapeHtml. Cualquier
         * error del catálogo o del escape queda capturado por el try/catch exterior.
         */
        return sortedTags.map(tag => {
            const tData = getTagData(tag);
            const safeTag = escapeHtml(tag);
            return `
                <span class="px-1.5 py-0.5 rounded text-[9px] font-bold border ${tData.style} flex items-center shadow-sm" title="${safeTag}">
                    <span class="mr-1">${tData.icon}</span> <span>${safeTag}</span>
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
 * Objetivo: reconstruir las tarjetas visibles para las colecciones filtradas de
 * torrents y series recibidas. No devuelve datos; sustituye el contenido del grid
 * y restaura el checked de cada torrent desde selectedCacheGuids, de modo que un
 * filtro no borre la seleccion. Tolera colecciones vacias y datos opcionales, y
 * depende de los helpers de titulos, badges, tags y layouts compartidos.
 */
function renderCacheGrid(torrents, series) {
    const grid = document.getElementById('cache-grid');
    if (!grid) return;
    grid.innerHTML = '';

    if (torrents.length === 0 && series.length === 0) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500">No se encontraron resultados en la caché local.</div>';
        return;
    }

    for (const s of series) {
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
        const seriesName = escapeHtml(s.series_name_es || s.series_name_original || 'Serie sin título');
        const seriesHref = `/cache/series/${encodeURIComponent(String(s.tvdb_id ?? ''))}`;
        const safeMatchedAlias = escapeHtml(s._matched_alias || 'Alias');
        const safeSeasonsAndYear = escapeHtml(seasonsAndYear);
        const safeStatusTitle = escapeHtml(statusTitle);
        const linkedCount = Number.isFinite(Number(s.linked_torrents_count))
            ? Math.max(0, Math.floor(Number(s.linked_torrents_count)))
            : 0;

        const card = document.createElement('div');
        card.className = "k-card group relative flex flex-col justify-between overflow-hidden shadow-lg border-2 border-green-500/30 hover:border-green-500/80 transition-all bg-[#0d0415]";
        card.innerHTML = `
            <div class="relative aspect-[2/3] w-full overflow-hidden block bg-[#05080c]">
                <img src="${posterUrl}" onerror="${fallbackImg}" alt="Poster" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105 opacity-90 group-hover:opacity-100">

                <a href="${seriesHref}" class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center z-20 cursor-pointer backdrop-blur-[2px]">
                    <span class="text-white font-bold bg-green-600/90 px-5 py-2.5 rounded-full shadow-[0_0_15px_rgba(22,163,74,0.5)] transform transition-transform group-hover:scale-110 flex items-center">
                        <i class="fa-solid fa-book-open mr-2"></i> Ver Serie
                    </span>
                </a>
            </div>
            
            <div class="p-3 flex flex-col flex-1 bg-gradient-to-b from-[#0a1a10] to-[#050d08]">
                
                <div class="poster-card-title mb-2" title="${seriesName}">
                    ${seriesName}
                </div>

                ${s._matched_by_alias ? `
                <div class="mb-2 text-[10px] font-bold text-blue-300 bg-blue-900/30 border border-blue-800/60 rounded px-2 py-1 truncate" title="Coincidencia por alias: ${safeMatchedAlias}">
                    <i class="fa-solid fa-link mr-1"></i> Coincidencia por alias: ${safeMatchedAlias}
                </div>
                ` : ''}
                
                <div class="flex justify-between items-center mt-auto pt-2 border-t border-green-900/30">
                    <div class="poster-card-meta text-green-600/70 font-mono bg-green-900/20 px-2 py-0.5 rounded border border-green-900/30 flex-1 mr-2" title="Temporadas y estreno">
                        <i class="fa-solid fa-layer-group mr-1"></i> ${safeSeasonsAndYear}
                    </div>
                    
                    <div class="flex items-center space-x-2 shrink-0">
                        <div class="w-2 h-2 rounded-full ${statusColor}" title="${safeStatusTitle}"></div>
                        <div class="text-[10px] font-bold text-green-400 bg-green-900/40 px-2 py-0.5 rounded shadow-inner" title="Torrents Vinculados">
                            <i class="fa-solid fa-link mr-1"></i> ${linkedCount}
                        </div>
                    </div>
                </div>
            </div>
        `;
        grid.appendChild(card);
    }

    for (const t of torrents) {
        const isFreeleech = Boolean(t.is_freeleech);
        const displayName = getCleanTorrentTitle(t);
        const posterUrl = t.poster_url ? `/api/ui/poster?url=${encodeURIComponent(t.poster_url)}` : '/static/img/Kitsunarr-logo-512x512.png';
        const fallbackImg = "this.onerror=null; this.src='/static/img/Kitsunarr-logo-512x512.png';";
        const fansubName = t.fansub_name || 'TRACKER';
        const safeGuid = escapeHtml(t.guid);
        const encodedGuid = encodeURIComponent(String(t.guid ?? ''));
        const safeDisplayName = escapeHtml(displayName);
        const safeFansubName = escapeHtml(fansubName);
        
        let aiIcon = getAiBadge(t);
        let tvdbIcon = getTvdbBadge(t);
        let trackerBadge = isFreeleech ? `<div class="absolute top-2 left-2 bg-yellow-500 text-black text-[10px] font-black px-2 py-0.5 rounded shadow-lg z-30 tracking-wider">FREELEECH</div>` : '';

        const card = document.createElement('div');
        card.className = "k-card group relative flex flex-col justify-between overflow-hidden shadow-lg border border-gray-800 hover:border-yellow-500/50 transition-all bg-[#0d0415]";
        card.innerHTML = `
            ${trackerBadge}
            
            <div class="absolute top-2 right-2 z-30">
                <input type="checkbox" class="batch-checkbox w-4 h-4 text-yellow-500 bg-gray-900 border-gray-600 rounded focus:ring-yellow-500 cursor-pointer shadow-md" value="${safeGuid}" ${window.selectedCacheGuids.has(String(t.guid)) ? 'checked' : ''} onchange="updateCacheSelection(this)" aria-label="Seleccionar torrent ${safeGuid}">
            </div>
            
            <div class="relative aspect-[2/3] w-full overflow-hidden block bg-[#05080c]">
                <img src="${posterUrl}" onerror="${fallbackImg}" alt="Poster" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105 opacity-90 group-hover:opacity-100">
                
                <div class="absolute bottom-0 left-0 w-full bg-gradient-to-t from-black via-black/80 to-transparent p-2 pt-8 z-20 pointer-events-none">
                    <div class="text-[10px] font-bold uppercase tracking-widest drop-shadow-[0_2px_4px_rgba(0,0,0,0.6)]">
                        <div class="mb-1">
                            <span class="fansub-badge inline-flex items-center rounded-sm bg-yellow-500/15 border border-yellow-500/50 text-yellow-300 font-black tracking-widest">FANSUB</span>
                        </div>
                        <div class="fansub-shell inline-block rounded">
                            <span class="fansub-label text-yellow-300 tracking-widest">${safeFansubName}</span>
                        </div>
                    </div>
                </div>

                <div class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center space-x-6 z-20 backdrop-blur-[2px]">
                    <a href="/cache/torrent/${encodedGuid}" title="Ver Ficha" class="text-white hover:text-yellow-500 transition transform hover:scale-125">
                        <i class="fa-solid fa-eye text-4xl drop-shadow-lg"></i>
                    </a>
                    <a href="/cache/edit/${encodedGuid}" title="Editar Manualmente" class="text-white hover:text-blue-500 transition transform hover:scale-125">
                        <i class="fa-solid fa-pen text-4xl drop-shadow-lg"></i>
                    </a>
                </div>
            </div>
            
            <div class="p-3 flex flex-col flex-1 bg-gradient-to-b from-[#11051c] to-[#0a0310]">
                
                <div class="poster-card-title mb-2" title="${safeDisplayName}">
                    ${safeDisplayName}
                </div>
                
                <div class="flex justify-between items-center mb-3">
                    <div class="poster-card-meta text-gray-400 font-mono bg-black/50 px-2 py-0.5 rounded border border-gray-800 shadow-inner flex-1 mr-2" title="ID en el Tracker: ${safeGuid}">
                        <i class="fa-solid fa-fingerprint mr-1"></i> ${safeGuid}
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
    }

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
 * Objetivo: devolver una copia lineal de los GUID seleccionados con independencia
 * de los checkboxes que sigan visibles tras filtrar. No recibe parametros, no
 * produce efectos laterales y depende exclusivamente de selectedCacheGuids.
 */
function getSelectedCacheGuids() {
    return Array.from(window.selectedCacheGuids);
}

/*
 * Objetivo: eliminar de la seleccion persistente GUID que ya no existen en la
 * respuesta completa de torrents individuales. Recibe dicha coleccion, no devuelve
 * valor y muta selectedCacheGuids; ignora entradas sin GUID. Se ejecuta tras una
 * carga correcta para no perder seleccion durante errores de red temporales.
 */
function pruneCacheSelection(torrents) {
    const availableGuids = new Set();
    for (const torrent of torrents || []) {
        if (torrent && torrent.guid !== undefined && torrent.guid !== null) {
            availableGuids.add(String(torrent.guid));
        }
    }

    for (const guid of window.selectedCacheGuids) {
        if (!availableGuids.has(guid)) window.selectedCacheGuids.delete(guid);
    }
}

/*
 * Objetivo: sincronizar un checkbox de tarjeta con la seleccion persistente.
 * Recibe el elemento input que origina el cambio, no devuelve valor y actualiza
 * selectedCacheGuids y los controles masivos. Ignora elementos sin GUID y depende
 * de toggleBatchSelection para refrescar botones y el selector de rehidratacion.
 */
function updateCacheSelection(checkbox) {
    if (!checkbox || checkbox.value === undefined || checkbox.value === null) return;
    const guid = String(checkbox.value);
    if (checkbox.checked) window.selectedCacheGuids.add(guid);
    else window.selectedCacheGuids.delete(guid);
    toggleBatchSelection();
}

/*
 * Objetivo: actualizar los controles de acciones masivas y el ámbito disponible
 * del dialogo de rehidratacion a partir de la seleccion persistente. No recibe ni
 * devuelve valores; modifica estados disabled y clases visuales. Puede ejecutarse
 * antes de que exista el modal y, si su helper esta cargado, lo sincroniza.
 */
function toggleBatchSelection() {
    const checked = window.selectedCacheGuids.size;
    const btnAi = document.getElementById('btn_batch_ai');
    const btnDel = document.getElementById('btn_batch_delete');
    
    if (checked > 0) {
        if(btnAi) { btnAi.disabled = false; btnAi.classList.remove('opacity-50', 'cursor-not-allowed'); }
        if(btnDel) { btnDel.disabled = false; btnDel.classList.remove('opacity-50', 'cursor-not-allowed'); }
    } else {
        if(btnAi) { btnAi.disabled = true; btnAi.classList.add('opacity-50', 'cursor-not-allowed'); }
        if(btnDel) { btnDel.disabled = true; btnDel.classList.add('opacity-50', 'cursor-not-allowed'); }
    }

    if (typeof updateRehydrateScopeUi === 'function') updateRehydrateScopeUi();
}
/*
 * Objetivo: pedir confirmacion antes de enviar la seleccion persistente al proceso
 * masivo de IA. No recibe ni devuelve datos utiles; abre appConfirm y, si se acepta,
 * delega en processBatchAI. Una seleccion vacia se comunica mediante showToast.
 */
async function openBatchModal() {
    const guids = getSelectedCacheGuids();
    if (guids.length === 0) return showToast("Selecciona al menos un torrent.", false);
    const accepted = await appConfirm(
        `¿Enviar ${guids.length} torrent(s) a procesar por la IA ahora mismo?`,
        'Confirmar procesamiento IA'
    );
    if (!accepted) return;
    processBatchAI();
}
/*
 * Objetivo: solicitar al endpoint force_specific el procesamiento IA de todos los
 * GUID conservados en la seleccion persistente. No recibe ni devuelve valor; emite
 * avisos y recarga la cache tras aceptar el backend. Los fallos de red o de negocio
 * se muestran sin modificar la seleccion y depende de fetch, showToast y loadCacheGrid.
 */
async function processBatchAI() {
    const guids = getSelectedCacheGuids();
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
            setTimeout(loadCacheGrid, 2000);
        } else if (data.error) {
            showToast(data.error, false);
        }
    } catch(e) { showToast("Error de red.", false); }
}
/*
 * Objetivo: borrar un único GUID y normalizar la respuesta HTTP/lógica del backend.
 * Recibe el GUID, devuelve siempre {guid, success} y no modifica selección ni DOM.
 * Los errores de red, JSON inválido, HTTP no exitoso y success distinto de true se
 * convierten en success=false para que deleteBatchSelection conserve ese registro.
 */
async function deleteSelectedCacheGuid(guid) {
    try {
        const response = await fetch(`/api/ui/cache/${encodeURIComponent(guid)}`, { method: 'DELETE' });
        const data = await response.json();
        return { guid: String(guid), success: response.ok && data.success === true };
    } catch (error) {
        return { guid: String(guid), success: false };
    }
}

/*
 * Objetivo: confirmar y borrar de la cache todos los GUID seleccionados, incluidos
 * los preservados fuera del filtro visible. No recibe ni devuelve valor; informa de
 * seleccion oculta, ejecuta en paralelo helpers DELETE y retira del Set únicamente
 * éxitos HTTP/lógicos confirmados. Los fallos quedan seleccionados para reintento;
 * depende de appConfirm, deleteSelectedCacheGuid, showToast y loadCacheGrid.
 */
async function deleteBatchSelection() {
    const guids = getSelectedCacheGuids();
    if(guids.length === 0) return;

    let visibleSelectedCount = 0;
    for (const checkbox of document.querySelectorAll('.batch-checkbox')) {
        if (window.selectedCacheGuids.has(String(checkbox.value))) visibleSelectedCount += 1;
    }
    const hiddenSelectedCount = Math.max(guids.length - visibleSelectedCount, 0);
    const hiddenSelectionNotice = hiddenSelectedCount > 0
        ? `\n\n${hiddenSelectedCount} de los seleccionados no están visibles con el filtro actual y también se eliminarán.`
        : '';
    
    const accepted = await appConfirm(
        `¿Estás completamente seguro de ELIMINAR ${guids.length} torrents de la base de datos?${hiddenSelectionNotice}\n\nEsta acción no se puede deshacer.`,
        'Confirmar eliminación'
    );
    if (!accepted) return;

    showToast(`Borrando ${guids.length} registros...`);

    const deletePromises = [];
    for (const guid of guids) deletePromises.push(deleteSelectedCacheGuid(guid));
    const results = await Promise.all(deletePromises);
    let deletedCount = 0;
    for (const result of results) {
        if (!result.success) continue;
        window.selectedCacheGuids.delete(result.guid);
        deletedCount += 1;
    }

    const failedCount = guids.length - deletedCount;
    if (failedCount === 0) {
        showToast(`Se han eliminado ${deletedCount} registros correctamente.`);
    } else if (deletedCount > 0) {
        showToast(`Se eliminaron ${deletedCount} de ${guids.length} registros. Los ${failedCount} restantes siguen seleccionados para reintentar.`, false);
    } else {
        showToast(`No se pudo confirmar la eliminación de los ${failedCount} registros; siguen seleccionados para reintentar.`, false);
    }

    toggleBatchSelection();
    if (deletedCount > 0) window.activeTagFilters.clear();
    loadCacheGrid();
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
 * Inicia la exportación elegida en el formulario. No recibe argumentos ni devuelve
 * valor; navega al endpoint, cierra el modal y muestra un toast. Si faltan controles
 * usa bundle, mientras errores DOM inesperados se propagan. Depende de la plantilla,
 * closeExportModal y showToast.
 */
function submitExport() {
    const selected = document.querySelector('input[name="export_type"]:checked');
    const module = selected ? selected.value : 'bundle';

    window.location.href = `/api/ui/cache/export?module=${module}`;
    closeExportModal();
    showToast("Generando y descargando el archivo...");
}

/*
 * BLOQUE REHIDRATACION DE FICHAS
 */

/*
 * Objetivo: indicar si un estado de backend cierra definitivamente un job.
 * Recibe cualquier valor de status, devuelve boolean y no produce efectos. Se
 * mantiene centralizado para que polling, render y reenganche compartan lifecycle.
 */
function isRehydrateTerminalStatus(status) {
    return status === 'completed' || status === 'failed' || status === 'cancelled';
}

/*
 * Objetivo: convertir un contador de backend en entero no negativo seguro para UI.
 * Recibe cualquier valor, devuelve cero para NaN/infinito/negativos y no modifica
 * estado. Evita que respuestas parciales rompan estadísticas o atributos ARIA.
 */
function toRehydrateCount(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0;
}

/*
 * Objetivo: extraer texto de error de respuestas JSON sin asumir una forma única.
 * Recibe payload y fallback, devuelve una cadena y no toca el DOM. Solo se usa con
 * textContent; los avisos HTML globales reciben mensajes fijos para evitar inyección.
 */
function getRehydrateErrorMessage(data, fallback) {
    if (data && typeof data.error === 'string' && data.error.trim()) return data.error.trim();
    if (data && typeof data.detail === 'string' && data.detail.trim()) return data.detail.trim();
    return fallback;
}

/*
 * Objetivo: generar una clave idempotente compatible con [A-Za-z0-9_-]{8,64}.
 * No recibe parámetros y devuelve una cadena nueva por inicio consciente; no toca
 * estado global. Usa Crypto.getRandomValues cuando está disponible y, si el navegador
 * no lo ofrece o lanza error, combina reloj y Math.random como fallback no sensible.
 * La clave no autentica: sendRehydrateStartRequest solo la reutiliza para deduplicar.
 */
function createRehydrateRequestId() {
    if (window.crypto && typeof window.crypto.getRandomValues === 'function') {
        try {
            const randomBytes = new Uint8Array(16);
            window.crypto.getRandomValues(randomBytes);
            let token = 'reh_';
            for (const byte of randomBytes) token += byte.toString(16).padStart(2, '0');
            return token;
        } catch (error) {
            // El fallback inferior mantiene el contrato aunque Web Crypto no esté operativo.
        }
    }
    return `reh_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 14)}`;
}

/*
 * Objetivo: reconocer exclusivamente la respuesta que confirma que un UUID ya no
 * existe. Recibe Response y JSON, devuelve boolean y no modifica estado. Prioriza el
 * código estable rehydrate_job_not_found, admite HTTP 404 y conserva compatibilidad
 * con el texto lógico legado; cualquier otro fallo se considera transitorio.
 */
function isRehydrateStatusNotFoundResponse(response, data) {
    if (data && data.code === 'rehydrate_job_not_found') return true;
    if (response && response.status === 404) return true;
    const errorText = data && typeof data.error === 'string' ? data.error.trim() : '';
    return data && data.success === false && errorText === 'Job de rehidratación no encontrado.';
}

/*
 * Objetivo: escribir texto de forma segura en un nodo del modal identificado por ID.
 * Recibe ID y valor, no devuelve datos y no falla si la plantilla no contiene el
 * nodo. Depende únicamente de document.getElementById y usa textContent.
 */
function setRehydrateText(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) element.textContent = String(value);
}

/*
 * Objetivo: mostrar u ocultar un error general de configuración de rehidratación.
 * Recibe texto opcional, no devuelve valor y actualiza el alert accesible sin HTML.
 * Una cadena vacía limpia el mensaje; depende del nodo rehydrate_config_error.
 */
function showRehydrateConfigurationError(message) {
    const errorElement = document.getElementById('rehydrate_config_error');
    if (!errorElement) return;
    const safeMessage = typeof message === 'string' ? message.trim() : '';
    errorElement.textContent = safeMessage;
    errorElement.classList.toggle('hidden', !safeMessage);
}

/*
 * Objetivo: sincronizar el ámbito del formulario con la cantidad persistente de
 * torrents marcados. No recibe ni devuelve valores; deshabilita selected cuando
 * vale cero y usa all solo como alternativa visual, conservando la preferencia
 * selected para restaurarla al volver a marcar fichas. Depende de
 * selectedCacheGuids, rehydratePreferredScope y los radios de cache.html.
 */
function updateRehydrateScopeUi() {
    const selectedRadio = document.getElementById('rehydrate_scope_selected');
    const allRadio = document.getElementById('rehydrate_scope_all');
    const selectedCard = document.getElementById('rehydrate_scope_selected_card');
    const count = window.selectedCacheGuids.size;

    setRehydrateText('rehydrate_selected_count', `(${count})`);
    if (!selectedRadio || !allRadio) return;

    selectedRadio.disabled = count === 0;
    if (count === 0) {
        allRadio.checked = true;
    } else if (window.rehydratePreferredScope === 'all') {
        allRadio.checked = true;
    } else {
        selectedRadio.checked = true;
    }

    if (selectedCard) {
        selectedCard.classList.toggle('opacity-50', count === 0);
        selectedCard.classList.toggle('cursor-not-allowed', count === 0);
        selectedCard.setAttribute('aria-disabled', count === 0 ? 'true' : 'false');
    }
}

/*
 * Objetivo: registrar el valor selected o all elegido explícitamente por el usuario.
 * No recibe ni devuelve valores; lee el radio activo, conserva selected aunque quede
 * temporalmente deshabilitado por una selección vacía y delega la sincronización en
 * updateRehydrateScopeUi. Se invoca desde onchange de ambos radios de ámbito.
 */
function handleRehydrateScopeChange() {
    const checkedRadio = document.querySelector('input[name="rehydrate_scope"]:checked');
    if (checkedRadio && (checkedRadio.value === 'selected' || checkedRadio.value === 'all')) {
        window.rehydratePreferredScope = checkedRadio.value;
    }
    updateRehydrateScopeUi();
}

/*
 * Objetivo: retirar el error del umbral de antigüedad tras corregir o cambiar la
 * política. No recibe ni devuelve valores; limpia aria-invalid y el alert asociado.
 * Tolera que el input o el mensaje todavía no existan en el DOM.
 */
function clearRehydrateDaysError() {
    const input = document.getElementById('rehydrate_older_than_days');
    const errorElement = document.getElementById('rehydrate_days_error');
    if (input) input.setAttribute('aria-invalid', 'false');
    if (errorElement) {
        errorElement.textContent = '';
        errorElement.classList.add('hidden');
    }
}

/*
 * Objetivo: alternar los controles propios de stale y la advertencia de complete.
 * No recibe ni devuelve valores; oculta y deshabilita el umbral cuando no se usa,
 * muestra la advertencia completa y limpia errores obsoletos. Depende de los radios
 * rehydrate_policy y de clearRehydrateDaysError.
 */
function updateRehydratePolicyUi() {
    const staleRadio = document.getElementById('rehydrate_policy_stale');
    const daysContainer = document.getElementById('rehydrate_days_container');
    const daysInput = document.getElementById('rehydrate_older_than_days');
    const warning = document.getElementById('rehydrate_complete_warning');
    const isStale = !staleRadio || staleRadio.checked;

    if (daysContainer) daysContainer.classList.toggle('hidden', !isStale);
    if (daysInput) daysInput.disabled = !isStale;
    if (warning) warning.classList.toggle('hidden', isStale);
    clearRehydrateDaysError();
}

/*
 * Objetivo: validar los dos ejes del formulario y construir el contrato POST.
 * No recibe parametros; devuelve {mode, guids, refresh_policy, older_than_days} o
 * null. En error enfoca el control responsable y actualiza alert/ARIA. Depende de
 * getSelectedCacheGuids, updateRehydrateScopeUi y los controles de cache.html.
 */
function validateRehydrateConfiguration() {
    showRehydrateConfigurationError('');
    clearRehydrateDaysError();
    updateRehydrateScopeUi();

    const scopeRadio = document.querySelector('input[name="rehydrate_scope"]:checked');
    const policyRadio = document.querySelector('input[name="rehydrate_policy"]:checked');
    const selectedGuids = getSelectedCacheGuids();
    const mode = scopeRadio ? scopeRadio.value : 'all';
    const refreshPolicy = policyRadio ? policyRadio.value : 'stale';

    if (mode === 'selected' && selectedGuids.length === 0) {
        showRehydrateConfigurationError('Marca al menos una ficha o selecciona toda la caché.');
        const allRadio = document.getElementById('rehydrate_scope_all');
        if (allRadio) allRadio.focus();
        return null;
    }

    let olderThanDays = 1;
    if (refreshPolicy === 'stale') {
        const input = document.getElementById('rehydrate_older_than_days');
        const rawValue = input ? input.value.trim() : '';
        olderThanDays = Number(rawValue);
        if (!Number.isInteger(olderThanDays) || olderThanDays < 1 || olderThanDays > 3650) {
            const errorElement = document.getElementById('rehydrate_days_error');
            if (input) {
                input.setAttribute('aria-invalid', 'true');
                input.focus();
            }
            if (errorElement) {
                errorElement.textContent = 'Escribe un número entero entre 1 y 3650 días.';
                errorElement.classList.remove('hidden');
            }
            return null;
        }
    }

    return {
        mode: mode,
        guids: mode === 'selected' ? selectedGuids : [],
        refresh_policy: refreshPolicy,
        older_than_days: olderThanDays
    };
}

/*
 * Objetivo: alternar visibilidad flex de un botón del pie sin duplicar clases.
 * Recibe elemento y boolean, no devuelve valor y no falla con nodos ausentes.
 * Centraliza el comportamiento usado por todos los estados del dialogo.
 */
function setRehydrateButtonVisible(button, isVisible) {
    if (!button) return;
    button.classList.toggle('hidden', !isVisible);
    button.classList.toggle('flex', isVisible);
}

/*
 * Objetivo: representar los estados config, starting, active, stopping y terminal
 * con acciones inequívocas. Recibe el nombre de estado, no devuelve valor y ajusta
 * visibilidad, disabled, aria-disabled y texto del botón de detención. Si Detener
 * tenía foco al deshabilitarse, lo traslada a Cerrar para no dejar foco sobre un
 * control inoperable. Tolera nodos ausentes; otros errores DOM se propagan. Depende
 * de document y setRehydrateButtonVisible; nunca cancela el job.
 */
function setRehydrateFooterState(state) {
    const submitButton = document.getElementById('btn_rehydrate_submit');
    const cancelButton = document.getElementById('btn_rehydrate_cancel');
    const newButton = document.getElementById('btn_rehydrate_new');
    const closeButton = document.getElementById('btn_rehydrate_close');
    const cancelText = document.getElementById('btn_rehydrate_cancel_text');
    const moveFocusFromCancel = state === 'stopping' && document.activeElement === cancelButton;

    setRehydrateButtonVisible(submitButton, state === 'config');
    setRehydrateButtonVisible(cancelButton, state === 'active' || state === 'stopping');
    setRehydrateButtonVisible(newButton, state === 'terminal');

    if (submitButton) submitButton.disabled = state !== 'config';
    if (cancelButton) {
        const isStopping = state === 'stopping';
        cancelButton.disabled = isStopping;
        cancelButton.setAttribute('aria-disabled', isStopping ? 'true' : 'false');
        cancelButton.classList.toggle('opacity-60', isStopping);
        cancelButton.classList.toggle('cursor-wait', isStopping);
    }
    if (cancelText) cancelText.textContent = state === 'stopping' ? 'Deteniendo…' : 'Detener proceso';
    if (moveFocusFromCancel && closeButton) closeButton.focus();
}

/*
 * Objetivo: devolver el dialogo al formulario de configuracion, opcionalmente con
 * valores iniciales. Recibe resetDefaults boolean, no devuelve valor y alterna los
 * paneles, limpia errores y configura ámbito/política. No detiene ningún job; solo
 * debe llamarse cuando no hay una ejecución activa o antes de iniciarla. Tolera
 * nodos opcionales, pero propaga fallos de los helpers. Depende de
 * showRehydrateConfigurationError, updateRehydrateScopeUi,
 * updateRehydratePolicyUi y setRehydrateFooterState.
 */
function showRehydrateConfiguration(resetDefaults) {
    const configPanel = document.getElementById('rehydrate_config_panel');
    const progressPanel = document.getElementById('rehydrate_progress_panel');
    const selectedRadio = document.getElementById('rehydrate_scope_selected');
    const allRadio = document.getElementById('rehydrate_scope_all');
    const staleRadio = document.getElementById('rehydrate_policy_stale');
    const completeRadio = document.getElementById('rehydrate_policy_complete');
    const daysInput = document.getElementById('rehydrate_older_than_days');

    if (configPanel) configPanel.classList.remove('hidden');
    if (progressPanel) {
        progressPanel.classList.add('hidden');
        progressPanel.setAttribute('aria-busy', 'false');
    }

    if (resetDefaults) {
        window.rehydratePreferredScope = null;
        if (selectedRadio) selectedRadio.checked = window.selectedCacheGuids.size > 0;
        if (allRadio) allRadio.checked = window.selectedCacheGuids.size === 0;
        if (staleRadio) staleRadio.checked = true;
        if (completeRadio) completeRadio.checked = false;
        if (daysInput) daysInput.value = '1';
    }

    showRehydrateConfigurationError('');
    updateRehydrateScopeUi();
    updateRehydratePolicyUi();
    setRehydrateFooterState('config');
}

/*
 * Objetivo: obtener en orden los controles enfocables y visibles del dialogo.
 * No recibe parametros y devuelve un array; no modifica estado. Filtra disabled y
 * nodos ocultos para que el trap de teclado no alcance paneles inactivos.
 */
function getRehydrateFocusableElements() {
    const dialog = document.getElementById('rehydrateDialog');
    if (!dialog) return [];
    const candidates = dialog.querySelectorAll('button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])');
    const focusable = [];
    for (const candidate of candidates) {
        if (!candidate.disabled && candidate.getAttribute('aria-hidden') !== 'true' && candidate.offsetParent !== null) {
            focusable.push(candidate);
        }
    }
    return focusable;
}

/*
 * Objetivo: cerrar con Escape sin cancelar y mantener Tab dentro del dialogo modal.
 * Recibe KeyboardEvent, no devuelve valor y previene la navegación que abandonaría
 * el diálogo, incluso si otro script desplazó antes el foco fuera de él. Ignora
 * eventos si el modal está oculto; depende de closeRehydrateModal y del listado
 * producido por getRehydrateFocusableElements.
 */
function handleRehydrateDialogKeydown(event) {
    const modal = document.getElementById('rehydrateModal');
    const dialog = document.getElementById('rehydrateDialog');
    if (!modal || modal.classList.contains('hidden') || !dialog) return;

    if (event.key === 'Escape') {
        event.preventDefault();
        closeRehydrateModal();
        return;
    }
    if (event.key !== 'Tab') return;

    const focusable = getRehydrateFocusableElements();
    if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const activeElement = document.activeElement;
    if (!dialog.contains(activeElement) || activeElement === dialog) {
        event.preventDefault();
        if (event.shiftKey) last.focus();
        else first.focus();
    } else if (event.shiftKey && activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && activeElement === last) {
        event.preventDefault();
        first.focus();
    }
}

/*
 * Objetivo: enfocar el primer control significativo al mostrar el modal. No recibe
 * ni devuelve valores; prioriza el ámbito del formulario y usa Cerrar durante un
 * job. Tolera plantillas parciales y recurre al contenedor dialog con tabindex -1.
 */
function focusInitialRehydrateControl() {
    const configPanel = document.getElementById('rehydrate_config_panel');
    const selectedRadio = document.getElementById('rehydrate_scope_selected');
    const allRadio = document.getElementById('rehydrate_scope_all');
    const closeButton = document.getElementById('btn_rehydrate_header_close');
    const dialog = document.getElementById('rehydrateDialog');

    if (configPanel && !configPanel.classList.contains('hidden')) {
        if (selectedRadio && !selectedRadio.disabled) selectedRadio.focus();
        else if (allRadio) allRadio.focus();
        else if (dialog) dialog.focus();
        return;
    }
    if (closeButton) closeButton.focus();
    else if (dialog) dialog.focus();
}

/*
 * Objetivo: abrir el dialogo accesible conservando su origen de foco y mostrando
 * configuración, progreso activo o último terminal según el estado local. No recibe
 * ni devuelve valores; bloquea scroll, instala trap y consulta /active para recuperar
 * jobs de otra pestaña o tras recarga. La ausencia del modal es un no-op y otros
 * errores DOM/helpers se propagan. Depende de handleRehydrateDialogKeydown, los
 * helpers de scope/policy/render/polling, discoverActiveRehydrateJob y foco.
 */
function openRehydrateModal() {
    const modal = document.getElementById('rehydrateModal');
    if (!modal) return;
    const wasHidden = modal.classList.contains('hidden');

    if (wasHidden) {
        window.rehydrateModalOpener = document.activeElement;
        window.rehydrateBodyOverflow = document.body.style.overflow;
    }
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    document.removeEventListener('keydown', handleRehydrateDialogKeydown);
    document.addEventListener('keydown', handleRehydrateDialogKeydown);

    updateRehydrateScopeUi();
    updateRehydratePolicyUi();
    if (window.activeRehydrateJobId && window.lastRehydrateJob && !isRehydrateTerminalStatus(window.lastRehydrateJob.status)) {
        renderRehydrateJob(window.lastRehydrateJob);
        startRehydratePolling(window.activeRehydrateJobId);
    } else if (window.lastRehydrateJob && isRehydrateTerminalStatus(window.lastRehydrateJob.status)) {
        renderRehydrateJob(window.lastRehydrateJob);
    } else if (!window.pendingRehydratePayload) {
        showRehydrateConfiguration(false);
    }

    discoverActiveRehydrateJob(false);
    focusInitialRehydrateControl();
}

/*
 * Objetivo: ocultar el dialogo y devolver el foco al control que lo abrió. No recibe
 * ni devuelve valores; restaura scroll y elimina el trap de teclado. Deliberadamente
 * no cancela, no limpia el job y no detiene polling/reintentos, por lo que cerrar es
 * una acción visual distinta de Detener.
 */
function closeRehydrateModal() {
    const modal = document.getElementById('rehydrateModal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = window.rehydrateBodyOverflow;
    document.removeEventListener('keydown', handleRehydrateDialogKeydown);

    const opener = window.rehydrateModalOpener;
    window.rehydrateModalOpener = null;
    if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
}

/*
 * Objetivo: pintar un snapshot escalar del job usando percent_total y todos los
 * contadores tolerados por el contrato. Recibe el job, no devuelve valor y actualiza
 * panel, progressbar ARIA, mensajes y acciones terminales/activas. Los datos del
 * backend se insertan con textContent y valores numéricos saneados. Una entrada no
 * objeto es un no-op; otros errores DOM se propagan. Depende de toRehydrateCount,
 * isRehydrateTerminalStatus, setRehydrateText y setRehydrateFooterState.
 */
function renderRehydrateJob(job) {
    if (!job || typeof job !== 'object') return;
    const configPanel = document.getElementById('rehydrate_config_panel');
    const progressPanel = document.getElementById('rehydrate_progress_panel');
    const progressBar = document.getElementById('rehydrate_progress_bar');
    const progressTrack = document.getElementById('rehydrate_progress_track');
    const statusBadge = document.getElementById('rehydrate_status_badge');

    const status = typeof job.status === 'string' ? job.status : 'queued';
    const processed = toRehydrateCount(job.processed);
    const total = toRehydrateCount(job.total);
    const scopeTotal = toRehydrateCount(job.scope_total);
    const remaining = job.remaining_count === undefined || job.remaining_count === null
        ? Math.max(total - processed, 0)
        : toRehydrateCount(job.remaining_count);
    const rawPercent = Number(job.percent_total);
    const percent = Number.isFinite(rawPercent) ? Math.max(0, Math.min(100, Math.round(rawPercent))) : 0;
    const isTerminal = isRehydrateTerminalStatus(status);
    const isStopping = !isTerminal && (
        window.rehydrateStopPending ||
        job.cancel_requested === true ||
        status === 'stopping'
    );
    if (isStopping) window.rehydrateStopPending = true;

    window.lastRehydrateJob = job;
    if (configPanel) configPanel.classList.add('hidden');
    if (progressPanel) {
        progressPanel.classList.remove('hidden');
        progressPanel.setAttribute('aria-busy', isTerminal ? 'false' : 'true');
    }

    const scopeLabel = job.mode === 'selected' ? 'Fichas marcadas' : 'Toda la caché';
    const policyLabel = job.refresh_policy === 'stale'
        ? `Antiguas: más de ${toRehydrateCount(job.older_than_days) || 1} día(s)`
        : 'Actualización completa';
    setRehydrateText('rehydrate_job_context', `${scopeLabel} · ${policyLabel}`);

    const statusLabels = {
        queued: 'En cola',
        running: 'Procesando',
        completed: 'Completada',
        failed: 'Finalizada con error',
        cancelled: 'Detenida'
    };
    setRehydrateText('rehydrate_status_badge', isStopping ? 'Deteniendo…' : (statusLabels[status] || 'Procesando'));
    if (statusBadge) {
        statusBadge.className = 'mt-1 text-lg font-black';
        if (status === 'failed') statusBadge.classList.add('text-red-400');
        else if (status === 'completed') statusBadge.classList.add('text-green-400');
        else statusBadge.classList.add('text-yellow-400');
    }

    if (progressBar) progressBar.style.width = `${percent}%`;
    setRehydrateText('rehydrate_progress_percent', `${percent}%`);
    if (progressTrack) {
        progressTrack.setAttribute('aria-valuenow', String(percent));
        progressTrack.setAttribute('aria-valuetext', `${percent}%, ${processed} de ${total} fichas procesadas`);
    }

    const jobMessage = typeof job.message === 'string' && job.message.trim() ? job.message.trim() : 'Procesando fichas';
    const errorMessage = typeof job.error === 'string' && job.error.trim() ? job.error.trim() : jobMessage;
    setRehydrateText('rehydrate_progress_message', isStopping ? 'Deteniendo después de la ficha actual…' : (status === 'failed' ? errorMessage : jobMessage));
    setRehydrateText('rehydrate_stat_scope', scopeTotal);
    setRehydrateText('rehydrate_stat_total', total);
    setRehydrateText('rehydrate_stat_processed', processed);
    setRehydrateText('rehydrate_stat_remaining', remaining);
    setRehydrateText('rehydrate_stat_updated', toRehydrateCount(job.updated));
    setRehydrateText('rehydrate_stat_unchanged', toRehydrateCount(job.unchanged));
    setRehydrateText('rehydrate_stat_partial', toRehydrateCount(job.partial));
    setRehydrateText('rehydrate_stat_fresh', toRehydrateCount(job.skipped_fresh));
    setRehydrateText('rehydrate_stat_skipped', toRehydrateCount(job.skipped));
    setRehydrateText('rehydrate_stat_failed', toRehydrateCount(job.failed));

    if (isTerminal) setRehydrateFooterState('terminal');
    else if (isStopping) setRehydrateFooterState('stopping');
    else setRehydrateFooterState('active');
}

/*
 * Objetivo: conservar el snapshot visible mientras una operación conocida pierde
 * conexión. Recibe mensaje fijo, no devuelve valor y no cambia job/contadores a
 * idle; marca estado Reconectando y mantiene Detener o Deteniendo según corresponda.
 * Depende de renderRehydrateJob y de lastRehydrateJob.
 */
function renderRehydrateConnectionState(message) {
    if (window.lastRehydrateJob) renderRehydrateJob(window.lastRehydrateJob);
    setRehydrateText('rehydrate_status_badge', 'Reconectando…');
    setRehydrateText('rehydrate_progress_message', message || 'Se reintentará la conexión automáticamente.');
    setRehydrateFooterState(window.rehydrateStopPending ? 'stopping' : (window.activeRehydrateJobId ? 'active' : 'starting'));
}

/*
 * Objetivo: invalidar la cadena de polling actual y cancelar su timer/fetch.
 * No recibe ni devuelve valores; incrementa una generación para que respuestas
 * tardías no alteren el nuevo estado. No limpia activeRehydrateJobId ni la UI.
 */
function stopRehydratePolling() {
    window.rehydratePollGeneration += 1;
    if (window.rehydratePollTimer !== null) {
        clearTimeout(window.rehydratePollTimer);
        window.rehydratePollTimer = null;
    }
    if (window.rehydratePollController) {
        window.rehydratePollController.abort();
        window.rehydratePollController = null;
    }
    window.rehydratePollInFlight = false;
}

/*
 * Objetivo: arrancar una única cadena de polling para el UUID indicado. Recibe jobId,
 * no devuelve valor y evita reiniciar si ya existe timer/fetch para el mismo job.
 * Para reemplazar otra cadena usa stopRehydratePolling y delega la primera consulta
 * en pollRehydrateStatus.
 */
function startRehydratePolling(jobId) {
    if (!jobId) return;
    const normalizedJobId = String(jobId);
    const sameJob = window.activeRehydrateJobId === normalizedJobId;
    if (sameJob && (window.rehydratePollTimer !== null || window.rehydratePollInFlight)) return;

    stopRehydratePolling();
    window.activeRehydrateJobId = normalizedJobId;
    window.rehydrateNetworkFailures = 0;
    const generation = window.rehydratePollGeneration;
    pollRehydrateStatus(normalizedJobId, generation);
}

/*
 * Objetivo: programar la siguiente consulta solo después de finalizar la anterior.
 * Recibe UUID, generación y demora, no devuelve valor y usa setTimeout encadenado.
 * Ignora generaciones antiguas para impedir solapes tras terminal o reenganche;
 * errores nativos del timer se propagan. Depende de pollRehydrateStatus y del
 * contador global rehydratePollGeneration.
 */
function scheduleRehydratePoll(jobId, generation, delay) {
    if (generation !== window.rehydratePollGeneration) return;
    if (window.rehydratePollTimer !== null) clearTimeout(window.rehydratePollTimer);
    window.rehydratePollTimer = setTimeout(pollRehydrateStatus, delay, jobId, generation);
}

/*
 * Objetivo: consultar una vez /status y decidir terminal, siguiente consulta o
 * reconexión. Recibe UUID/generación, devuelve Promise<void> y nunca solapa llamadas:
 * el siguiente setTimeout nace tras resolver fetch/JSON. Red, HTTP y payloads
 * transitorios conservan UI y reintentan con backoff; solo un not-found tipado se
 * contrasta con /active. Muta controller/in-flight y estado visual global. Depende de
 * isRehydrateStatusNotFoundResponse, discoverActiveRehydrateJob,
 * scheduleRehydratePoll, renderRehydrateJob y handleRehydrateTerminalJob.
 */
async function pollRehydrateStatus(jobId, generation) {
    if (generation !== window.rehydratePollGeneration) return;
    window.rehydratePollTimer = null;
    const controller = new AbortController();
    window.rehydratePollController = controller;
    window.rehydratePollInFlight = true;

    try {
        const response = await fetch(`/api/ui/cache/rehydrate/${encodeURIComponent(jobId)}/status`, {
            signal: controller.signal
        });
        const data = await response.json();
        if (generation !== window.rehydratePollGeneration) return;

        if (isRehydrateStatusNotFoundResponse(response, data)) {
            renderRehydrateConnectionState('Comprobando si el proceso sigue activo…');
            const discoveryResult = await discoverActiveRehydrateJob(true);
            if (
                discoveryResult === 'found' &&
                generation === window.rehydratePollGeneration &&
                window.activeRehydrateJobId === String(jobId)
            ) {
                scheduleRehydratePoll(jobId, generation, 900);
            } else if (
                discoveryResult === 'error' &&
                generation === window.rehydratePollGeneration &&
                window.activeRehydrateJobId === String(jobId)
            ) {
                scheduleRehydratePoll(jobId, generation, 2500);
            }
            return;
        }

        if (!response.ok || !data.success || !data.job) {
            window.rehydrateNetworkFailures += 1;
            renderRehydrateConnectionState('El servidor no pudo devolver el progreso. Se reintentará automáticamente.');
            const delay = Math.min(10000, 1000 * (2 ** Math.min(window.rehydrateNetworkFailures - 1, 4)));
            scheduleRehydratePoll(jobId, generation, delay);
            return;
        }

        window.rehydrateNetworkFailures = 0;
        const job = data.job;
        renderRehydrateJob(job);
        if (isRehydrateTerminalStatus(job.status)) {
            handleRehydrateTerminalJob(job);
            return;
        }
        scheduleRehydratePoll(jobId, generation, 900);
    } catch (error) {
        if (generation !== window.rehydratePollGeneration || (error && error.name === 'AbortError')) return;
        window.rehydrateNetworkFailures += 1;
        renderRehydrateConnectionState('No se pudo consultar el progreso. Se reintentará automáticamente.');
        const delay = Math.min(10000, 1000 * (2 ** Math.min(window.rehydrateNetworkFailures - 1, 4)));
        scheduleRehydratePoll(jobId, generation, delay);
    } finally {
        if (window.rehydratePollController === controller) {
            window.rehydratePollController = null;
            window.rehydratePollInFlight = false;
        }
    }
}

/*
 * Objetivo: cerrar lifecycle local al recibir completed, failed o cancelled.
 * Recibe snapshot terminal, no devuelve valor y cancela reintentos, libera el UUID,
 * conserva el resumen en el modal, avisa una sola vez por job y recarga la cuadrícula.
 * Depende de renderRehydrateJob, showToast y loadCacheGrid.
 */
function handleRehydrateTerminalJob(job) {
    const previouslyFocused = document.activeElement;
    stopRehydratePolling();
    if (window.rehydrateCancelRetryTimer !== null) clearTimeout(window.rehydrateCancelRetryTimer);
    if (window.rehydrateStartRetryTimer !== null) clearTimeout(window.rehydrateStartRetryTimer);
    window.rehydrateCancelRetryTimer = null;
    window.rehydrateStartRetryTimer = null;
    window.pendingRehydratePayload = null;
    window.activeRehydrateJobId = null;
    window.rehydrateStopPending = false;
    window.lastRehydrateJob = job;
    renderRehydrateJob(job);

    const modal = document.getElementById('rehydrateModal');
    const newButton = document.getElementById('btn_rehydrate_new');
    if (
        modal && !modal.classList.contains('hidden') &&
        previouslyFocused && previouslyFocused.id === 'btn_rehydrate_cancel' &&
        newButton
    ) {
        newButton.focus();
    }

    const jobId = String(job.job_id || 'terminal');
    if (window.rehydrateTerminalToastJobId !== jobId) {
        window.rehydrateTerminalToastJobId = jobId;
        if (job.status === 'completed') {
            const summary = `Rehidratación completada: ${toRehydrateCount(job.updated)} actualizadas, ${toRehydrateCount(job.unchanged)} sin cambios, ${toRehydrateCount(job.partial)} parciales, ${toRehydrateCount(job.skipped_fresh)} frescas omitidas, ${toRehydrateCount(job.skipped)} otros omitidos y ${toRehydrateCount(job.failed)} fallidas.`;
            showToast(summary, toRehydrateCount(job.failed) === 0);
        } else if (job.status === 'cancelled') {
            showToast(`Rehidratación detenida. Quedaron ${toRehydrateCount(job.remaining_count)} fichas sin procesar.`);
        } else {
            showToast('La rehidratación terminó con un error. Consulta el detalle en la ventana.', false);
        }
    }
    loadCacheGrid();
}

/*
 * Objetivo: consultar el slot /active al cargar/reabrir y resolver carreras tras un
 * status desconocido. Recibe clearKnownJob: si true, un active vacío confirma que el
 * job se perdió; devuelve Promise<'found'|'none'|'error'>. Los fallos de red nunca
 * fuerzan idle y programan backoff; muta timers, lifecycle y UI. Depende de los
 * helpers stop/start de polling, renderRehydrateJob, handleRehydrateTerminalJob,
 * showRehydrateConfiguration y los helpers de aviso/foco.
 */
async function discoverActiveRehydrateJob(clearKnownJob) {
    if (!document.getElementById('rehydrateModal')) return 'none';
    if (window.rehydrateDiscoveryRetryTimer !== null) {
        clearTimeout(window.rehydrateDiscoveryRetryTimer);
        window.rehydrateDiscoveryRetryTimer = null;
    }

    try {
        const response = await fetch('/api/ui/cache/rehydrate/active');
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error('active_unavailable');

        window.rehydrateDiscoveryFailures = 0;
        const job = data.job && typeof data.job === 'object' ? data.job : null;
        if (job && job.job_id) {
            const discoveredJobId = String(job.job_id);
            const previousJobId = window.activeRehydrateJobId;
            const keepLocalStopState = previousJobId === discoveredJobId && window.rehydrateStopPending;
            if (previousJobId && previousJobId !== discoveredJobId) stopRehydratePolling();
            if (window.rehydrateStartRetryTimer !== null) clearTimeout(window.rehydrateStartRetryTimer);
            window.rehydrateStartRetryTimer = null;
            window.pendingRehydratePayload = null;
            window.activeRehydrateJobId = discoveredJobId;
            const backendMessage = typeof job.message === 'string' ? job.message.toLowerCase() : '';
            window.rehydrateStopPending = (
                keepLocalStopState ||
                job.cancel_requested === true ||
                job.status === 'stopping' ||
                backendMessage.startsWith('deteniendo')
            );
            renderRehydrateJob(job);
            if (isRehydrateTerminalStatus(job.status)) handleRehydrateTerminalJob(job);
            else startRehydratePolling(job.job_id);
            const modal = document.getElementById('rehydrateModal');
            if (modal && !modal.classList.contains('hidden')) focusInitialRehydrateControl();
            return 'found';
        }

        if (clearKnownJob) {
            stopRehydratePolling();
            window.activeRehydrateJobId = null;
            window.rehydrateStopPending = false;
            window.lastRehydrateJob = null;
            showRehydrateConfiguration(false);
            showRehydrateConfigurationError('El proceso anterior ya no está disponible. Puedes iniciar una nueva rehidratación.');
            showToast('No se pudo recuperar el proceso anterior; puede que el servidor se haya reiniciado.', false);
        } else if (!window.activeRehydrateJobId && !window.lastRehydrateJob && !window.pendingRehydratePayload) {
            showRehydrateConfiguration(false);
        }
        return 'none';
    } catch (error) {
        window.rehydrateDiscoveryFailures += 1;
        if (window.activeRehydrateJobId || window.pendingRehydratePayload) {
            renderRehydrateConnectionState('No se pudo comprobar el proceso activo. Se reintentará automáticamente.');
        } else {
            showRehydrateConfigurationError('No se pudo comprobar si existe una rehidratación activa. Se reintentará automáticamente.');
        }
        const delay = Math.min(15000, 1500 * (2 ** Math.min(window.rehydrateDiscoveryFailures - 1, 4)));
        window.rehydrateDiscoveryRetryTimer = setTimeout(discoverActiveRehydrateJob, delay, clearKnownJob);
        return 'error';
    }
}

/*
 * Objetivo: validar y preparar visualmente una nueva petición de rehidratación.
 * No recibe ni devuelve valor; evita dobles envíos, crea un request_id exactamente
 * una vez, lo guarda dentro del payload para todos los reintentos ambiguos y muestra
 * un snapshot provisional antes de delegar en sendRehydrateStartRequest. No abre
 * confirmaciones adicionales; errores de helpers se propagan. Depende de
 * validateRehydrateConfiguration, createRehydrateRequestId, renderRehydrateJob,
 * setRehydrateFooterState, focusInitialRehydrateControl y el sender asíncrono.
 */
function submitRehydrate() {
    if (window.activeRehydrateJobId || window.pendingRehydratePayload) return;
    const payload = validateRehydrateConfiguration();
    if (!payload) return;

    payload.request_id = createRehydrateRequestId();

    window.pendingRehydratePayload = payload;
    window.rehydrateStartFailures = 0;
    showRehydrateConfigurationError('');
    renderRehydrateJob({
        job_id: null,
        status: 'queued',
        mode: payload.mode,
        refresh_policy: payload.refresh_policy,
        older_than_days: payload.older_than_days,
        scope_total: payload.mode === 'selected' ? payload.guids.length : 0,
        total: 0,
        processed: 0,
        remaining_count: 0,
        percent_total: 0,
        message: 'Contactando con el servidor…'
    });
    setRehydrateFooterState('starting');
    focusInitialRehydrateControl();
    sendRehydrateStartRequest();
}

/*
 * Objetivo: enviar o reintentar el POST de creación usando el payload pendiente.
 * No recibe ni devuelve valor útil; al éxito adopta el UUID (created true o false)
 * y comienza polling. Errores lógicos vuelven al formulario con texto seguro; una
 * pérdida ambigua mantiene estado Reconectando y reutiliza el mismo request_id porque
 * el backend deduplica esa clave contra el activo y el último terminal. Hace red y
 * muta timers/lifecycle/UI; depende de los helpers de configuración/error, render,
 * renderRehydrateConnectionState y startRehydratePolling.
 */
async function sendRehydrateStartRequest() {
    const payload = window.pendingRehydratePayload;
    if (!payload || window.activeRehydrateJobId) return;
    if (window.rehydrateStartRetryTimer !== null) {
        clearTimeout(window.rehydrateStartRetryTimer);
        window.rehydrateStartRetryTimer = null;
    }

    try {
        const response = await fetch('/api/ui/cache/rehydrate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || !data.success || !data.job_id) {
            window.pendingRehydratePayload = null;
            window.lastRehydrateJob = null;
            showRehydrateConfiguration(false);
            showRehydrateConfigurationError(getRehydrateErrorMessage(data, 'No se pudo iniciar la rehidratación. Revisa los valores e inténtalo de nuevo.'));
            showToast('No se pudo iniciar la rehidratación.', false);
            return;
        }

        window.pendingRehydratePayload = null;
        window.rehydrateStartFailures = 0;
        const jobId = String(data.job_id);
        window.activeRehydrateJobId = jobId;
        const provisionalJob = {
            job_id: jobId,
            status: typeof data.status === 'string' ? data.status : 'queued',
            mode: payload.mode,
            refresh_policy: payload.refresh_policy,
            older_than_days: payload.older_than_days,
            scope_total: payload.mode === 'selected' ? payload.guids.length : 0,
            total: 0,
            processed: 0,
            remaining_count: 0,
            percent_total: 0,
            message: data.created === false ? 'Reenganchando con la rehidratación activa…' : 'Rehidratación en cola'
        };
        renderRehydrateJob(provisionalJob);
        startRehydratePolling(jobId);
    } catch (error) {
        if (!window.pendingRehydratePayload || window.activeRehydrateJobId) return;
        window.rehydrateStartFailures += 1;
        renderRehydrateConnectionState('No se pudo confirmar el inicio. Se reintentará automáticamente.');
        const delay = Math.min(15000, 1500 * (2 ** Math.min(window.rehydrateStartFailures - 1, 4)));
        window.rehydrateStartRetryTimer = setTimeout(sendRehydrateStartRequest, delay);
    }
}

/*
 * Objetivo: pasar inmediatamente el job activo al estado visual Deteniendo y lanzar
 * la petición cooperativa. No recibe ni devuelve valor; deshabilita el botón para
 * impedir dobles clics y conserva el polling. Si no hay UUID es un no-op; errores de
 * render se propagan. Depende de renderRehydrateJob, setRehydrateFooterState y
 * sendRehydrateCancelRequest.
 */
function cancelRehydrateJob() {
    if (!window.activeRehydrateJobId || window.rehydrateStopPending) return;
    window.rehydrateStopPending = true;
    window.rehydrateCancelFailures = 0;
    if (window.lastRehydrateJob) renderRehydrateJob(window.lastRehydrateJob);
    else setRehydrateFooterState('stopping');
    sendRehydrateCancelRequest();
}

/*
 * Objetivo: enviar/reintentar el POST idempotente de detención del UUID activo.
 * No recibe ni devuelve valor útil; un éxito mantiene Deteniendo hasta que /status
 * sea terminal. La red reintenta sin parar polling; un error lógico reactiva la
 * consulta de estado para resolver una finalización concurrente. Hace red y muta
 * timers/UI; depende de renderRehydrateConnectionState, renderRehydrateJob y
 * startRehydratePolling.
 */
async function sendRehydrateCancelRequest() {
    const jobId = window.activeRehydrateJobId;
    if (!jobId || !window.rehydrateStopPending) return;
    if (window.rehydrateCancelRetryTimer !== null) {
        clearTimeout(window.rehydrateCancelRetryTimer);
        window.rehydrateCancelRetryTimer = null;
    }

    try {
        const response = await fetch(`/api/ui/cache/rehydrate/${encodeURIComponent(jobId)}/cancel`, {
            method: 'POST'
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            window.rehydrateStopPending = false;
            renderRehydrateConnectionState('La ejecución pudo finalizar mientras se solicitaba detener. Comprobando estado…');
            startRehydratePolling(jobId);
            return;
        }

        window.rehydrateCancelFailures = 0;
        if (window.lastRehydrateJob) renderRehydrateJob(window.lastRehydrateJob);
        startRehydratePolling(jobId);
    } catch (error) {
        if (!window.activeRehydrateJobId || !window.rehydrateStopPending) return;
        window.rehydrateCancelFailures += 1;
        renderRehydrateConnectionState('No se pudo confirmar la detención. Se reintentará automáticamente.');
        const delay = Math.min(10000, 1000 * (2 ** Math.min(window.rehydrateCancelFailures - 1, 4)));
        window.rehydrateCancelRetryTimer = setTimeout(sendRehydrateCancelRequest, delay);
    }
}

/*
 * Objetivo: abandonar un resumen terminal y preparar un job completamente nuevo.
 * No recibe ni devuelve valor; solo actúa sin UUID activo, limpia snapshot/toast,
 * restaura valores recomendados y enfoca el ámbito. Nunca continúa pendientes del
 * job detenido; errores de UI se propagan. Depende de showRehydrateConfiguration y
 * focusInitialRehydrateControl.
 */
function startNewRehydrateConfiguration() {
    if (window.activeRehydrateJobId) return;
    window.lastRehydrateJob = null;
    window.rehydrateTerminalToastJobId = null;
    window.rehydrateStopPending = false;
    showRehydrateConfiguration(true);
    focusInitialRehydrateControl();
}
/*
 * Importa el archivo del input recibido y devuelve Promise<void>. Confirma la acción,
 * sube FormData, muestra resultados, limpia el input y puede recargar la página. Los
 * errores de red/JSON del POST se convierten en toast; fallos previos de evento,
 * confirmación o DOM se propagan. Depende de appConfirm, fetch, showToast y FormData.
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
 * Objetivo: inicializar la vista cuando el DOM está listo. No recibe ni devuelve
 * valores; carga la cuadrícula, prepara ambos ejes del formulario y consulta /active
 * para reenganchar un job aunque el modal permanezca cerrado. Los fallos de cache se
 * representan en su grid y los de discovery aplican su reintento; otros errores de
 * helpers se propagan. Depende de loadCacheGrid, updateRehydrateScopeUi,
 * updateRehydratePolicyUi y discoverActiveRehydrateJob.
 */
function initializeCacheView() {
    if (document.getElementById('cache-grid')) loadCacheGrid();
    if (document.getElementById('rehydrateModal')) {
        updateRehydrateScopeUi();
        updateRehydratePolicyUi();
        discoverActiveRehydrateJob(false);
    }
}

document.addEventListener('DOMContentLoaded', initializeCacheView);
