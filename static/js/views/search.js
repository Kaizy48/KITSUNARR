// ==========================================
// LÓGICA DE LA VISTA: BÚSQUEDA INTERACTIVA
// ==========================================

/**
 * Ejecuta una consulta directa al indexador configurado forzando el raspado 
 * de la página web remota y renderiza los resultados en la galería.
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
    
    let progress = 10;
    progressBar.style.width = `${progress}%`;
    progressText.innerText = `${progress}%`;
    statusText.innerText = 'Conectando al Tracker y raspando páginas...';
    
    const fakeProgressInterval = setInterval(() => {
        if (progress < 85) {
            progress += Math.floor(Math.random() * 8) + 2; 
            if(progress > 85) progress = 85;
            progressBar.style.width = `${progress}%`;
            progressText.innerText = `${progress}%`;
        }
    }, 800);
    
    try {
        const res = await fetch(`/api/ui/search?q=${encodeURIComponent(query)}`);
        const data = await res.json();
        
        clearInterval(fakeProgressInterval);
        progressBar.style.width = '100%';
        progressText.innerText = '100%';
        statusText.innerText = '¡Metadatos extraídos!';
        
        setTimeout(() => progressContainer.classList.add('hidden'), 1500);
        
        if(data.success) {
            window.currentSearchData = data.results;
            renderSearchResults(window.currentSearchData);
        } else {
            grid.innerHTML = `<div class="col-span-full p-8 text-center text-red-500 font-bold"><i class="fa-solid fa-triangle-exclamation mr-2"></i> Error: ${data.error}</div>`;
            showToast(data.error, false);
        }
    } catch(e) {
        clearInterval(fakeProgressInterval);
        progressContainer.classList.add('hidden');
        grid.innerHTML = '<div class="col-span-full p-8 text-center text-red-500 font-bold">Error de red al conectar con el servidor interno.</div>';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-search mr-2"></i> Buscar en Foro';
    }
}

/**
 * Pinta en el HTML la galería de resultados devueltos por la búsqueda interactiva.
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
        if (t.ai_status === 'Listo') iaIcon = '<i class="fa-solid fa-check text-green-500" title="IA Lista"></i>';
        else if (t.ai_status === 'Manual') iaIcon = '<i class="fa-solid fa-user-pen text-purple-400" title="IA Editada Manualmente"></i>';
        else if (t.ai_status === 'Error') iaIcon = '<i class="fa-solid fa-xmark text-red-500" title="Error IA"></i>';
        else iaIcon = '<i class="fa-solid fa-robot text-gray-600" title="IA Pendiente"></i>';

        let tvdbIcon = '';
        if (t.tvdb_status === 'Listo') tvdbIcon = '<i class="fa-solid fa-check text-green-500" title="TVDB Validado"></i>';
        else if (t.tvdb_status === 'Revisión Manual' || t.tvdb_status === 'Manual') tvdbIcon = '<i class="fa-solid fa-user-pen text-purple-400" title="TVDB Editado Manualmente"></i>';
        else if (t.tvdb_status === 'Error' || t.tvdb_status === 'No Encontrado') tvdbIcon = '<i class="fa-solid fa-xmark text-red-500" title="Error TVDB"></i>';
        else tvdbIcon = '<i class="fa-solid fa-tv text-gray-600" title="TVDB Pendiente / Candidatos"></i>';

        const posterUrl = t.poster_url ? `/api/ui/poster?url=${encodeURIComponent(t.poster_url)}` : '/static/img/Kitsunarr-logo-512x512.png';

        card.innerHTML = `
            <div class="relative aspect-[2/3] rounded-t-lg overflow-hidden cursor-pointer bg-black" onclick="openInfoModalFromSearch('${t.guid}')">
                <img src="${posterUrl}" alt="Poster" class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-110 opacity-90 group-hover:opacity-100">
                <div class="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                    <i class="fa-solid fa-eye text-3xl text-white"></i>
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
 * Busca un torrent por su GUID en la variable global de resultados de búsqueda 
 * y abre el modal de Ficha Dual con su información.
 */
function openInfoModalFromSearch(guid) {
    const t = window.currentSearchData.find(x => x.guid === guid);
    if(t) populateAndOpenDualModal(t);
}