// ==========================================
// LÓGICA DE LA VISTA: BÚSQUEDA THETVDB
// ==========================================

/**
 * Lanza una búsqueda directa a los servidores de TheTVDB y pinta
 * los resultados en una galería de "candidatos crudos".
 */
async function runTvdbSearch() {
    const query = document.getElementById('tvdbInteractiveSearchInput').value.trim();
    if(!query) return showToast("Escribe el nombre de una serie.", false);
    
    const btn = document.getElementById('btn_run_tvdb_search');
    const grid = document.getElementById('tvdb-search-results-grid');
    const progressContainer = document.getElementById('tvdb_search_progress_container');
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Buscando...';
    progressContainer.classList.remove('hidden');
    grid.innerHTML = '<div class="col-span-full p-8 text-center text-gray-500"><i class="fa-solid fa-circle-notch fa-spin mr-2 text-blue-500"></i> Consultando a TheTVDB...</div>';
    
    try {
        const res = await fetch(`/api/ui/tvdb/remote_search?q=${encodeURIComponent(query)}`);
        const data = await res.json();
        
        progressContainer.classList.add('hidden');
        
        if(data.success) {
            renderTvdbSearchResults(data.results || []);
        } else {
            grid.innerHTML = `<div class="col-span-full p-8 text-center text-red-500 font-bold"><i class="fa-solid fa-triangle-exclamation mr-2"></i> Error: ${data.error}</div>`;
            showToast(data.error, false);
        }
    } catch(e) {
        progressContainer.classList.add('hidden');
        grid.innerHTML = '<div class="col-span-full p-8 text-center text-red-500 font-bold">Error de red al conectar con la API.</div>';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-cloud-arrow-down mr-2"></i> Buscar en API';
    }
}

/**
 * Pinta las tarjetas de resultados de la búsqueda de TheTVDB.
 */
function renderTvdbSearchResults(results) {
    const grid = document.getElementById('tvdb-search-results-grid');
    if(!grid) return;
    grid.innerHTML = '';
    
    if(results.length === 0) {
        grid.innerHTML = '<div class="col-span-full p-8 text-center text-gray-500">No se encontraron resultados en TheTVDB.</div>';
        return;
    }
    
    results.forEach(r => {
        const card = document.createElement('div');
        card.className = "group flex flex-col relative bg-[#0a0f18] rounded-lg border border-blue-900/30 shadow-md hover:border-blue-500/50 transition-all duration-300";
        
        const posterUrl = r.image_url ? `/api/ui/poster?url=${encodeURIComponent(r.image_url)}` : '/static/img/Kitsunarr-logo-512x512.png';
        const year = r.year || '----';
        const status = r.status || 'Desconocido';

        card.innerHTML = `
            <div class="relative aspect-[2/3] rounded-t-lg overflow-hidden bg-black">
                <img src="${posterUrl}" alt="Poster" class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105 opacity-90 group-hover:opacity-100">
                <div class="absolute top-2 right-2 bg-black/80 text-blue-400 text-xs font-bold px-2 py-1 rounded backdrop-blur-md border border-blue-800 shadow-lg">
                    ID: ${r.tvdb_id}
                </div>
            </div>
            <div class="p-3 flex flex-col justify-between flex-1">
                <div>
                    <div class="text-white text-sm font-bold line-clamp-2 leading-tight mb-1" title="${r.name}">${r.name}</div>
                    <div class="text-xs text-gray-500 font-mono">${year} • ${status}</div>
                </div>
                <div class="mt-4 pt-3 border-t border-gray-800">
                    <button onclick="fetchTvdbMaster('${r.tvdb_id}', this)" class="w-full bg-blue-600/20 hover:bg-blue-600 text-blue-400 hover:text-white border border-blue-600/50 py-2 rounded text-xs font-bold transition flex justify-center items-center">
                        <i class="fa-solid fa-download mr-2"></i> Añadir a Biblioteca
                    </button>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });
}

/**
 * Solicita al backend que descargue la ficha completa (incluyendo capítulos) 
 * y la guarde en la base de datos local (Convierte a is_full_record=True).
 */
async function fetchTvdbMaster(tvdb_id, btnElement) {
    const originalHtml = btnElement.innerHTML;
    btnElement.disabled = true;
    btnElement.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Descargando...';
    btnElement.classList.replace('text-blue-400', 'text-white');
    
    try {
        const res = await fetch(`/api/ui/tvdb/fetch_master/${tvdb_id}`, { method: 'POST' });
        const data = await res.json();
        
        if(data.success) {
            btnElement.innerHTML = '<i class="fa-solid fa-check mr-2"></i> Añadida';
            btnElement.classList.replace('bg-blue-600/20', 'bg-green-600');
            btnElement.classList.replace('border-blue-600/50', 'border-green-600');
            btnElement.classList.replace('hover:bg-blue-600', 'hover:bg-green-700');
            showToast("Ficha maestra y episodios guardados en la biblioteca.");
        } else {
            btnElement.innerHTML = originalHtml;
            btnElement.disabled = false;
            showToast("Error: " + data.error, false);
        }
    } catch(e) {
        btnElement.innerHTML = originalHtml;
        btnElement.disabled = false;
        showToast("Error de red al descargar la ficha.", false);
    }
}