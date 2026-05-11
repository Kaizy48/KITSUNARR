/*
 * BLOQUE BUSQUEDA INTERACTIVA
 */

/*
 * Ejecuta una búsqueda manual en los indexadores y renderiza los resultados raspados.
 */
async function runInteractiveSearch() {
    const query = document.getElementById('interactiveSearchInput').value.trim();
    if(!query) return showToast("Escribe un término de búsqueda.", false);
    
    const btn = document.getElementById('btn_run_search');
    const grid = document.getElementById('search-results-grid');
    const progressContainer = document.getElementById('search_progress_container');
    const progressBar = document.getElementById('search_progress_bar');
    const progressText = document.getElementById('search_percentage');
    const statusText = document.getElementById('search_status_text');
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Buscando...';
    progressContainer.classList.remove('hidden');
    grid.innerHTML = '<div class="col-span-full p-8 text-center text-gray-500"><i class="fa-solid fa-circle-notch fa-spin mr-2 text-yellow-500"></i> Raspando páginas del foro...</div>';
    
    let progress = 8;
    progressBar.style.width = `${progress}%`;
    progressText.innerText = `${progress}%`;
    statusText.innerText = 'Conectando al Tracker y raspando páginas...';

    const fakeProgressInterval = setInterval(() => {
        if (progress < 90) {
            const remaining = 90 - progress;
            const increment = Math.max(0.25, remaining * 0.12);
            progress = Math.min(90, progress + increment);
            const progressValue = Math.floor(progress);
            progressBar.style.width = `${progressValue}%`;
            progressText.innerText = `${progressValue}%`;
        }
    }, 600);
    
    try {
        const res = await fetch(`/api/ui/search/interactive?q=${encodeURIComponent(query)}`);
        const data = await res.json();
        
        clearInterval(fakeProgressInterval);
        progressBar.style.width = '100%';
        progressText.innerText = '100%';
        statusText.innerText = '¡Metadatos extraídos!';
        
        setTimeout(() => progressContainer.classList.add('hidden'), 1500);
        
        if(data.success) {
            window.currentSearchData = data.results;
            window.activeTagFilters.clear();
            sessionStorage.setItem('interactiveSearchQuery', query);
            sessionStorage.setItem('interactiveSearchResults', JSON.stringify(window.currentSearchData || []));

            extractAndRenderSearchTags(window.currentSearchData);
            filterSearchGrid();
        } else {
            grid.innerHTML = `
                <div class="col-span-full p-8 text-center text-red-500 font-bold bg-red-900/10 border border-red-900/50 rounded-lg">
                    <i class="fa-solid fa-triangle-exclamation text-4xl mb-3 block"></i> 
                    Atención: ${data.error || "No se pudo realizar la búsqueda."}<br>
                    <span class="text-sm text-gray-400 font-normal mt-2 block">Ve a la pestaña 'Indexadores' y asegúrate de tener al menos uno añadido y habilitado.</span>
                </div>`;
            showToast(data.error, false);
        }
    } catch(e) {
        clearInterval(fakeProgressInterval);
        progressContainer.classList.add('hidden');
        grid.innerHTML = '<div class="col-span-full p-8 text-center text-red-500 font-bold">Error de red al conectar con el servidor interno.</div>';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-search mr-2"></i> Buscar en Tracker';
    }
}

/*
 * BLOQUE FILTRADO DE TAGS EN BUSQUEDA
 */

/*
 * Extrae etiquetas únicas de los resultados de búsqueda y crea los botones de filtro.
 */
function extractAndRenderSearchTags(torrents) {
    const container = document.getElementById('search_filter_tags_container');
    if (!container) return;

    const tagsSet = new Set();
    (torrents || []).forEach(t => {
        if (!t.tags) return;
        try {
            const parsed = JSON.parse(t.tags);
            if (Array.isArray(parsed)) parsed.forEach(tag => tagsSet.add(tag));
        } catch (e) {}
    });

    const tagsArray = Array.from(tagsSet).sort(sortTags);
    container.innerHTML = '';

    if (tagsArray.length === 0) {
        container.innerHTML = '<span class="text-[10px] text-gray-600 italic">Sin etiquetas detectadas en esta búsqueda</span>';
        return;
    }

    tagsArray.forEach(tag => {
        const tData = getTagData(tag);
        const btn = document.createElement('button');
        btn.className = `px-3 py-1 rounded-full text-[10px] font-bold border transition-all ${tData.style} opacity-50 hover:opacity-100`;
        btn.innerHTML = `${tData.icon} ${tag}`;
        btn.onclick = () => toggleSearchTagFilter(tag, btn);
        container.appendChild(btn);
    });
}

/*
 * Activa o desactiva un filtro de etiqueta en la búsqueda interactiva.
 */
function toggleSearchTagFilter(tag, btnElement) {
    if (window.activeTagFilters.has(tag)) {
        window.activeTagFilters.delete(tag);
        btnElement.classList.remove('opacity-100', 'ring-2', 'ring-white');
        btnElement.classList.add('opacity-50');
    } else {
        window.activeTagFilters.add(tag);
        btnElement.classList.remove('opacity-50');
        btnElement.classList.add('opacity-100', 'ring-2', 'ring-white');
    }

    filterSearchGrid();
}

/*
 * Aplica los filtros activos de etiquetas sobre los resultados de búsqueda.
 */
function filterSearchGrid() {
    const filtered = (window.currentSearchData || []).filter(t => {
        if (window.activeTagFilters.size === 0) return true;
        if (!t.tags) return false;

        try {
            const tTags = JSON.parse(t.tags);
            if (!Array.isArray(tTags)) return false;

            for (let activeTag of window.activeTagFilters) {
                if (!tTags.includes(activeTag)) return false;
            }
            return true;
        } catch (e) {
            return false;
        }
    });

    renderSearchResults(filtered);
}

/*
 * Renderiza etiquetas en las tarjetas de resultados de búsqueda.
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
 * BLOQUE RENDERIZADO DE RESULTADOS DE BUSQUEDA
 */

/*
 * Renderiza la galería de torrents devueltos por la búsqueda interactiva.
 */
function renderSearchResults(results) {
    const grid = document.getElementById('search-results-grid');
    if(!grid) return;
    grid.innerHTML = '';
    
    if(results.length === 0) {
        grid.innerHTML = '<div class="col-span-full p-8 text-center text-gray-500">No se encontraron resultados en el tracker.</div>';
        return;
    }
    
    results.forEach(t => {
        const isFreeleech = t.freeleech_until && new Date(t.freeleech_until) > new Date();
        const card = document.createElement('div');
        card.className = "k-card group relative flex flex-col justify-between overflow-hidden shadow-lg border border-gray-800 hover:border-yellow-500/50 transition-all bg-[#0d0415]";
        
        const displayName = getCleanTorrentTitle(t);
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
        const trackerBadge = isFreeleech
            ? '<div class="absolute top-2 left-2 bg-yellow-500 text-black text-[10px] font-black px-2 py-0.5 rounded shadow-lg z-30 tracking-wider">FREELEECH</div>'
            : '';

        const posterUrl = t.poster_url ? `/api/ui/poster?url=${encodeURIComponent(t.poster_url)}` : '/static/img/Kitsunarr-logo-512x512.png';
        const fallbackImg = "this.onerror=null; this.src='/static/img/Kitsunarr-logo-512x512.png';";

        card.innerHTML = `
            ${trackerBadge}

            <div class="relative aspect-[2/3] w-full overflow-hidden block bg-[#05080c]">
                <img src="${posterUrl}" onerror="${fallbackImg}" alt="Poster" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105 opacity-90 group-hover:opacity-100">

                ${fansubOverlayHtml}

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
                    <div class="shrink-0 bg-black/30 px-1.5 py-0.5 rounded border border-gray-800">
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

    if (typeof refreshPosterLayouts === 'function') {
        refreshPosterLayouts();
    }
}

/*
 * Abre la ficha detallada de un resultado de búsqueda usando su GUID.
 */
function openInfoModalFromSearch(guid) {
    const t = window.currentSearchData.find(x => x.guid === guid);
    if (t) window.location.href = `/cache/torrent/${t.guid}`;
}

/*
 * BLOQUE RESTAURACION DE BUSQUEDA
 */

/*
 * Restaura la última búsqueda interactiva guardada al volver a la vista.
 */
document.addEventListener('DOMContentLoaded', () => {
    const input = document.getElementById('interactiveSearchInput');
    if (!input) return;

    const savedQuery = sessionStorage.getItem('interactiveSearchQuery') || '';
    const savedResultsRaw = sessionStorage.getItem('interactiveSearchResults');

    if (savedQuery) input.value = savedQuery;

    if (!savedResultsRaw) return;
    try {
        const savedResults = JSON.parse(savedResultsRaw);
        if (!Array.isArray(savedResults) || savedResults.length === 0) return;

        window.currentSearchData = savedResults;
        extractAndRenderSearchTags(window.currentSearchData);
        filterSearchGrid();
    } catch (e) {}
});
