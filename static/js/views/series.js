window.currentTorrents = [];

/*
 * BLOQUE ESTANTERIA DE SERIE
 */

/*
 * Funcion para traducir estados TVDB en la estanteria de una serie.
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
 * BLOQUE TAGS DE LA SERIE
 */

/*
 * Funcion de renderizado de etiquetas dentro de las tarjetas de una serie.
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
 * BLOQUE CARGA DE SERIE
 */

/*
 * Funcion de carga de una serie TVDB y todos sus torrents vinculados.
 */
async function loadSeriesShelf() {
    const tvdbId = document.getElementById('current_tvdb_id').value;
    const container = document.getElementById('shelves_container');

    try {
        const res = await fetch(`/api/ui/cache/series/${tvdbId}`);
        const data = await res.json();

        if (data.success) {
            window.currentTorrents = data.torrents;
            renderHeroBanner(data.series);
            extractAndRenderSeriesTags(data.torrents);
            applyFiltersAndRender();
            toggleSeriesBatchSelection();
        } else {
            container.innerHTML = `<div class="text-center p-8 text-red-500"><i class="fa-solid fa-triangle-exclamation text-4xl mb-4"></i><br>${data.error || 'Error al cargar la serie.'}</div>`;
        }
    } catch (e) {
        console.error("Error al montar la estantería:", e);
        container.innerHTML = `<div class="text-center p-8 text-red-500"><i class="fa-solid fa-bug text-4xl mb-4"></i><br>Error interno de Javascript: <b>${e.message}</b></div>`;
    }
}
/*
 * Funcion para pintar el banner principal de la serie con metadatos TVDB.
 */
function renderHeroBanner(series) {
    const banner = document.getElementById('series_hero_banner');

    if (!series) {
        if (banner) banner.classList.add('hidden');
        return;
    }

    const posterUrl = series.poster_path ? `/api/ui/poster?url=${encodeURIComponent(series.poster_path)}` : '/static/img/Kitsunarr-logo-512x512.png';
    document.getElementById('hero_bg').src = series.banner_path ? `/api/ui/poster?url=${encodeURIComponent(series.banner_path)}` : posterUrl;
    document.getElementById('hero_poster').src = posterUrl;
    document.getElementById('hero_tvdb_checkbox').value = series.tvdb_id;

    document.getElementById('hero_title_es').innerText = series.series_name_es || series.series_name_original || 'Serie Desconocida';
    document.getElementById('hero_title_original').innerText = series.series_name_jp || series.series_name_original || '-';

    let statusText = translateTvdbStatus(series.status);
    let statusColor = "text-gray-400";
    if (series.status === 'Ended') { statusColor = "text-red-400"; }
    else if (series.status === 'Continuing') { statusColor = "text-green-400"; }

    const sBadge = document.getElementById('hero_status');
    sBadge.innerText = statusText;
    sBadge.className = `text-[9px] font-bold px-2 py-0.5 rounded border border-gray-600 bg-gray-900 ${statusColor}`;

    document.getElementById('hero_year').innerText = series.first_aired ? series.first_aired.substring(0, 4) : '----';

    const aliasesContainer = document.getElementById('hero_aliases');
    aliasesContainer.innerHTML = '';
    if (series.aliases) {
        try {
            const aliasList = JSON.parse(series.aliases);
            if (aliasList.length > 0) {
                aliasesContainer.classList.remove('hidden');
                aliasList.slice(0, 8).forEach(alias => {
                    const span = document.createElement('span');
                    span.className = "text-[8px] font-mono bg-purple-900/30 text-purple-300 border border-purple-800 px-1.5 py-0.5 rounded";
                    span.innerText = alias;
                    aliasesContainer.appendChild(span);
                });
            }
        } catch (e) {}
    }

    const synopsis = series.overview_es || 'No hay sinopsis oficial en español disponible para esta serie.';
    document.getElementById('hero_synopsis').innerText = synopsis;

    banner.classList.remove('hidden');
}

/*
 * BLOQUE FILTRADO DE TAGS EN SERIE
 */

/*
 * Funcion para extraer etiquetas de los torrents de la serie y crear filtros.
 */
function extractAndRenderSeriesTags(torrents) {
    const allTagsSet = new Set();

    torrents.forEach(t => {
        if (t.tags) {
            try { JSON.parse(t.tags).forEach(tag => allTagsSet.add(tag)); } catch (e) {}
        }
        if (t.is_freeleech) allTagsSet.add("Freeleech 🎁");
    });

    const container = document.getElementById('filter_tags_container');
    if (!container) return;
    container.innerHTML = '';

    const sortedTags = Array.from(allTagsSet).sort(sortTags);

    Array.from(window.activeTagFilters).forEach(tag => {
        if (!allTagsSet.has(tag)) window.activeTagFilters.delete(tag);
    });

    if (sortedTags.length === 0) {
        container.innerHTML = '<span class="text-[10px] text-gray-600 italic">No hay etiquetas activas en esta serie</span>';
        return;
    }

    sortedTags.forEach(tag => {
        const btn = document.createElement('button');
        const tagData = getTagData(tag);

        btn.className = "flex items-center px-3 py-1 text-[9px] font-bold rounded-full border border-gray-700 bg-gray-900 text-gray-400 hover:bg-gray-800 hover:text-white transition shadow-sm whitespace-nowrap";
        btn.innerHTML = `<span class="mr-1.5 grayscale opacity-60">${tagData.icon}</span> ${tag}`;
        btn.dataset.activeClasses = tagData.style;

        if (window.activeTagFilters.has(tag)) {
            btn.classList.remove('bg-gray-900', 'text-gray-400', 'border-gray-700');
            btn.classList.add(...tagData.style.split(' '));
            btn.querySelector('span').classList.remove('grayscale', 'opacity-60');
        }

        btn.onclick = () => toggleSeriesTagFilter(tag, btn);
        container.appendChild(btn);
    });
}
/*
 * Funcion para activar o desactivar filtros de etiqueta en la estanteria.
 */
function toggleSeriesTagFilter(tag, btnElement) {
    const activeClasses = btnElement.dataset.activeClasses.split(' ');
    const inactiveClasses = ['bg-gray-900', 'text-gray-400', 'border-gray-700'];
    const iconSpan = btnElement.querySelector('span');

    if (window.activeTagFilters.has(tag)) {
        window.activeTagFilters.delete(tag);
        btnElement.classList.remove(...activeClasses);
        btnElement.classList.add(...inactiveClasses);
        if (iconSpan) iconSpan.classList.add('grayscale', 'opacity-60');
    } else {
        window.activeTagFilters.add(tag);
        btnElement.classList.remove(...inactiveClasses);
        btnElement.classList.add(...activeClasses);
        if (iconSpan) iconSpan.classList.remove('grayscale', 'opacity-60');
    }
    applyFiltersAndRender();
}
/*
 * Funcion para aplicar filtros activos y refrescar las estanterias visibles.
 */
function applyFiltersAndRender() {
    let filtered = window.currentTorrents;

    if (window.activeTagFilters.size > 0) {
        filtered = window.currentTorrents.filter(t => {
            let tTags = [];
            if (t.tags) { try { tTags = JSON.parse(t.tags); } catch (e) {} }
            if (t.is_freeleech) tTags.push("Freeleech 🎁");

            return Array.from(window.activeTagFilters).every(filterTag => tTags.includes(filterTag));
        });
    }
    renderShelves(filtered);
}

/*
 * BLOQUE RENDERIZADO DE ESTANTERIAS
 */

/*
 * Funcion para agrupar torrents por temporada y pintar las estanterias.
 */
function renderShelves(torrents) {
    const container = document.getElementById('shelves_container');
    container.innerHTML = '';

    if (!torrents || torrents.length === 0) {
        container.innerHTML = '<div class="text-center p-8 text-gray-500 italic"><i class="fa-solid fa-ghost text-4xl mb-3"></i><br>Ningún torrent coincide con los filtros actuales.</div>';
        return;
    }

    const groups = { 'unidentified': [], 'specials': [], 'seasons': {} };

    torrents.forEach(t => {
        if (t.parsed_season === null || t.parsed_season === undefined) groups.unidentified.push(t);
        else if (t.parsed_season === 0) groups.specials.push(t);
        else {
            if (!groups.seasons[t.parsed_season]) groups.seasons[t.parsed_season] = [];
            groups.seasons[t.parsed_season].push(t);
        }
    });

    Object.keys(groups.seasons).sort((a, b) => parseInt(a) - parseInt(b)).forEach(seasonNum => {
        const title = `Temporada ${seasonNum} <span class="text-gray-500 text-[10px] ml-2 font-mono">(S${seasonNum.toString().padStart(2, '0')})</span>`;
        container.appendChild(createShelfElement(title, groups.seasons[seasonNum], 'fa-tv', 'text-blue-400'));
    });

    if (groups.specials.length > 0) {
        const title = `Especiales / OVAs / Películas <span class="text-gray-500 text-[10px] ml-2 font-mono">(S00)</span>`;
        container.appendChild(createShelfElement(title, groups.specials, 'fa-star', 'text-yellow-500'));
    }

    if (groups.unidentified.length > 0) {
        const title = `Sin Temporada Identificada <span class="text-gray-500 text-[10px] ml-2 italic">Requiere revisión manual</span>`;
        container.appendChild(createShelfElement(title, groups.unidentified, 'fa-circle-question', 'text-red-400'));
    }

    if (typeof refreshPosterLayouts === 'function') {
        refreshPosterLayouts();
    }
}
/*
 * Funcion para crear una estanteria con las tarjetas de torrents de una temporada.
 */
function createShelfElement(titleHtml, torrentsList, iconClass, colorClass) {
    const shelfWrapper = document.createElement('div');
    shelfWrapper.className = "w-full border border-gray-800 rounded-lg bg-black/40 overflow-hidden shadow-lg mb-8";

    const header = document.createElement('div');
    header.className = "bg-gray-900/80 px-5 py-3 border-b border-gray-800 flex items-center justify-between";
    header.innerHTML = `
        <h3 class="text-white font-bold text-sm flex items-center">
            <i class="fa-solid ${iconClass} ${colorClass} mr-3"></i> ${titleHtml}
        </h3>
        <span class="bg-black text-gray-400 text-[10px] font-bold px-2 py-1 rounded border border-gray-700">${torrentsList.length} items</span>
    `;

    const grid = document.createElement('div');
    grid.className = "p-5 responsive-poster-grid";

    torrentsList.forEach(t => {
        const card = document.createElement('div');
        card.onclick = (e) => {
            if (e.target.closest('button') || e.target.closest('a') || e.target.tagName === 'INPUT') return;
            window.location.href = `/cache/torrent/${t.guid}`;
        };
        card.className = "k-card group relative flex flex-col justify-between overflow-hidden shadow-lg border border-gray-800 hover:border-yellow-500/50 transition-all bg-[#0d0415] cursor-pointer";

        const isFreeleech = t.freeleech_until && new Date(t.freeleech_until) > new Date();
        const displayName = getCleanTorrentTitle(t);
        const posterUrl = t.poster_url ? `/api/ui/poster?url=${encodeURIComponent(t.poster_url)}` : '/static/img/Kitsunarr-logo-512x512.png';
        const fallbackImg = "this.onerror=null; this.src='/static/img/Kitsunarr-logo-512x512.png';";

        const iconsHtml = generateWorkerIconsHtml(t);
        const fansubName = t.fansub_name || 'TRACKER';
        const fansubOverlayHtml = `
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
        `;

        let trackerBadge = isFreeleech ? `<div class="absolute top-2 left-2 bg-yellow-500 text-black text-[10px] font-black px-2 py-0.5 rounded shadow-lg z-30 tracking-wider">FREELEECH</div>` : '';
        if (t.is_batch && !isFreeleech) {
            trackerBadge = `<div class="absolute top-2 left-2 bg-blue-600 text-white text-[9px] font-bold px-2 py-0.5 rounded shadow-lg z-30 uppercase tracking-wider">LOTE</div>`;
        }

        card.innerHTML = `
            ${trackerBadge}
            
            <div class="absolute top-2 right-2 z-30">
                <input type="checkbox" value="${t.guid}" data-type="torrent" onchange="toggleSeriesBatchSelection()" class="series-batch-checkbox w-5 h-5 accent-yellow-500 bg-gray-900 border-gray-600 rounded cursor-pointer shadow-md" onclick="event.stopPropagation()">
            </div>
            
            <div class="relative aspect-[2/3] w-full overflow-hidden block bg-[#05080c]">
                <img src="${posterUrl}" onerror="${fallbackImg}" alt="Poster" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105 opacity-90 group-hover:opacity-100">
                
                ${fansubOverlayHtml}

                <div class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center space-x-6 z-20 backdrop-blur-[2px]">
                    ${''}
                    <a href="/cache/torrent/${t.guid}" title="Ver Ficha" class="text-white hover:text-yellow-500 transition transform hover:scale-125" onclick="event.stopPropagation()">
                        <i class="fa-solid fa-eye text-4xl drop-shadow-lg"></i>
                    </a>
                    <button onclick="event.stopPropagation(); openEditModal('${t.guid}')" title="Editar Manualmente" class="text-white hover:text-blue-500 transition transform hover:scale-125 bg-transparent border-none">
                        <i class="fa-solid fa-pen text-4xl drop-shadow-lg"></i>
                    </button>
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
                        ${iconsHtml}
                    </div>
                </div>

                <div class="flex flex-wrap gap-1 pt-3 border-t border-gray-800/60">
                    ${renderCardTags(t.tags)}
                </div>
            </div>
        `;
        grid.appendChild(card);
    });

    shelfWrapper.appendChild(header);
    shelfWrapper.appendChild(grid);
    return shelfWrapper;
}

/*
 * BLOQUE ACCIONES DE SERIE
 */

/*
 * Funcion para abrir la edicion manual de un torrent vinculado a la serie.
 */
function openEditModal(guid) {
    window.location.href = `/cache/edit/${guid}`;
}
/*
 * Funcion para eliminar un torrent concreto desde la estanteria de serie.
 */
async function deleteCacheEntry(guid) {
    const accepted = await appConfirm(
        '¿Estás seguro de que quieres eliminar este torrent de la caché local?',
        'Confirmar eliminación'
    );
    if (!accepted) return;
    try {
        const res = await fetch(`/api/ui/cache/${guid}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast("Torrent eliminado.");
            window.activeTagFilters.clear();
            loadSeriesShelf();
        } else {
            showToast("Error al eliminar: " + data.error, false);
        }
    } catch (e) {
        showToast("Error de red.", false);
    }
}

/*
 * BLOQUE SELECCION Y BORRADO EN SERIE
 */

/*
 * Funcion para seleccionar o deseleccionar la serie completa desde el banner.
 */
function toggleHeroCheckbox(e) {
    if (e.target.tagName === 'INPUT') return;
    const cb = document.getElementById('hero_tvdb_checkbox');
    cb.checked = !cb.checked;
    toggleSeriesBatchSelection();
}
/*
 * Funcion para habilitar acciones en lote segun la seleccion de la serie.
 */
function toggleSeriesBatchSelection() {
    const checkedBoxes = document.querySelectorAll('.series-batch-checkbox:checked');
    const btnDelete = document.getElementById('btn_batch_delete_series');
    if (!btnDelete) return;

    if (checkedBoxes.length > 0) {
        btnDelete.disabled = false;
        btnDelete.classList.remove('opacity-50', 'cursor-not-allowed');
    } else {
        btnDelete.disabled = true;
        btnDelete.classList.add('opacity-50', 'cursor-not-allowed');
    }
}
/*
 * Funcion para eliminar torrents o la serie TVDB seleccionada desde la estanteria.
 */
async function deleteSelectedItems() {
    const checkedBoxes = document.querySelectorAll('.series-batch-checkbox:checked');
    if (checkedBoxes.length === 0) return;

    const tvdbIds = [];
    const torrentGuids = [];

    checkedBoxes.forEach(cb => {
        if (cb.getAttribute('data-type') === 'tvdb') tvdbIds.push(cb.value);
        if (cb.getAttribute('data-type') === 'torrent') torrentGuids.push(cb.value);
    });

    let msg = `¿Estás seguro de eliminar los elementos seleccionados?\n`;
    if (tvdbIds.length > 0) msg += `- La Serie Maestra completa (TVDB).\n`;
    if (torrentGuids.length > 0) msg += `- ${torrentGuids.length} Torrents del tracker.\n`;
    msg += `\n¡Esta acción no se puede deshacer!`;

    const accepted = await appConfirm(msg, 'Confirmar eliminación en lote');
    if (!accepted) return;

    showToast("Borrando elementos...");

    try {
        if (torrentGuids.length > 0) {
            const torrentPromises = torrentGuids.map(guid => fetch(`/api/ui/cache/${guid}`, { method: 'DELETE' }));
            await Promise.all(torrentPromises);
        }

        if (tvdbIds.length > 0) {
            const tvdbPromises = tvdbIds.map(id => fetch(`/api/ui/tvdb_cache/${id}`, { method: 'DELETE' }));
            await Promise.all(tvdbPromises);

            showToast("Serie maestra eliminada. Redirigiendo...");
            setTimeout(() => window.location.href = '/cache', 1000);
            return;
        }

        showToast("Elementos eliminados correctamente.");
        document.querySelectorAll('.series-batch-checkbox').forEach(cb => cb.checked = false);
        window.activeTagFilters.clear();
        loadSeriesShelf();

    } catch (e) {
        showToast("Error de red al borrar elementos.", false);
        loadSeriesShelf();
    }
}

/*
 * BLOQUE INICIALIZACION
 */

/*
 * Funcion de inicializacion de la estanteria cuando existe una serie cargable.
 */
document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById('shelves_container')) loadSeriesShelf();
});
