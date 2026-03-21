// ==========================================
// LÓGICA DE LA VISTA: BIBLIOTECA TVDB
// ==========================================

/**
 * Carga la lista de series conocidas desde la API y las renderiza en formato galería.
 */
async function loadTvdbCache() {
    const grid = document.getElementById('tvdb-grid');
    if (!grid) return;

    grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando biblioteca...</div>';

    try {
        const res = await fetch('/api/ui/tvdb_cache');
        const data = await res.json();
        window.currentTvdbData = data.tvdb_cache;
        renderTvdbGrid(window.currentTvdbData);
    } catch (e) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-red-500">Error al cargar la biblioteca TVDB.</div>';
    }
}

/**
 * Pinta las tarjetas de las series (con sus pósters y metadatos) en el DOM.
 */
function renderTvdbGrid(data) {
    const grid = document.getElementById('tvdb-grid');
    if (!grid) return;
    grid.innerHTML = '';

    if (data.length === 0) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500 flex flex-col items-center"><i class="fa-solid fa-folder-open text-4xl mb-3"></i><p>La biblioteca está vacía.</p><p class="text-xs mt-2">Procesa torrents con la IA para que Kitsunarr empiece a aprender.</p></div>';
        return;
    }

    data.forEach(show => {
        const card = document.createElement('div');
        card.className = "group cursor-pointer flex flex-col relative transition-transform transform hover:scale-105 hover:z-10";
        card.onclick = () => openTvdbModal(show.tvdb_id);

        const posterUrl = show.poster_path ? `/api/ui/poster?url=${encodeURIComponent(show.poster_path)}` : '/static/img/Kitsunarr-logo-512x512.png';
        const statusColor = show.status === 'Ended' ? 'bg-red-500' : (show.status === 'Continuing' ? 'bg-green-500' : 'bg-gray-500');

        card.innerHTML = `
            <div class="relative aspect-[2/3] rounded-lg overflow-hidden shadow-lg border border-gray-800">
                <img src="${posterUrl}" alt="Poster" class="w-full h-full object-cover">
                <div class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                    <i class="fa-solid fa-magnifying-glass-plus text-3xl text-white"></i>
                </div>
                <div class="absolute top-2 right-2 ${statusColor} w-3 h-3 rounded-full border border-black shadow-sm" title="${show.status || 'Desconocido'}"></div>
            </div>
            <div class="mt-2 text-center">
                <h3 class="text-white text-sm font-bold truncate px-1">${show.series_name_es || show.series_name_en}</h3>
                <p class="text-gray-500 text-xs">${show.first_aired ? show.first_aired.substring(0,4) : '----'}</p>
            </div>
        `;
        grid.appendChild(card);
    });
}

/**
 * Filtra la galería en tiempo real buscando coincidencias en el título en español, 
 * título en inglés y en la lista de alias japoneses.
 */
function filterTvdbGrid() {
    const q = document.getElementById('tvdbSearch').value.toLowerCase();
    const filtered = window.currentTvdbData.filter(show => {
        const matchEs = show.series_name_es && show.series_name_es.toLowerCase().includes(q);
        const matchEn = show.series_name_en && show.series_name_en.toLowerCase().includes(q);
        const matchAlias = show.aliases && show.aliases.toLowerCase().includes(q); 
        return matchEs || matchEn || matchAlias;
    });
    renderTvdbGrid(filtered);
}


// ==========================================
// MODAL Y EPISODIOS
// ==========================================

/**
 * Abre el modal con la información detallada de la serie, inyecta los datos visuales
 * y solicita a la API local los episodios descargados correspondientes a ese ID.
 */
async function openTvdbModal(tvdb_id) {
    const show = window.currentTvdbData.find(x => x.tvdb_id === tvdb_id);
    if (!show) return;

    document.getElementById('modal_tvdb_poster').src = show.poster_path ? `/api/ui/poster?url=${encodeURIComponent(show.poster_path)}` : '';
    document.getElementById('modal_tvdb_title_es').innerText = show.series_name_es || 'Sin título ES';
    document.getElementById('modal_tvdb_title_en').innerText = show.series_name_en || 'Sin título EN';
    document.getElementById('modal_tvdb_status').innerText = show.status || 'Desconocido';
    document.getElementById('modal_tvdb_aired').innerText = show.first_aired || 'Desconocido';
    document.getElementById('modal_tvdb_id').innerText = show.tvdb_id;
    document.getElementById('modal_tvdb_overview').innerText = show.overview_es || show.overview_en || 'No hay sinopsis disponible.';

    if (show.last_updated) {
        const dateObj = new Date(show.last_updated + "Z"); 
        document.getElementById('modal_tvdb_last_updated').innerText = `Sincronizado: ${dateObj.toLocaleDateString()} ${dateObj.toLocaleTimeString()}`;
    } else {
        document.getElementById('modal_tvdb_last_updated').innerText = `Sincronizado: Desconocido`;
    }

    let seasonsText = "Desconocidas";
    if (show.seasons_data) {
        try {
            const parsedSeasons = JSON.parse(show.seasons_data);
            const seasonKeys = Object.keys(parsedSeasons);
            if (seasonKeys.length > 0) {
                seasonsText = `T${seasonKeys.join(', T')}`;
            }
        } catch(e) {}
    }
    document.getElementById('modal_tvdb_seasons').innerText = seasonsText;

    const aliasesContainer = document.getElementById('modal_tvdb_aliases');
    aliasesContainer.innerHTML = '';
    if (show.aliases) {
        try {
            const aliasArr = JSON.parse(show.aliases);
            if (aliasArr.length === 0) aliasesContainer.innerHTML = '<span class="text-xs text-gray-600 italic">No hay alias registrados.</span>';
            aliasArr.slice(0, 10).forEach(alias => {
                const span = document.createElement('span');
                span.className = "bg-gray-800 border border-gray-700 text-gray-300 text-xs px-2 py-1 rounded";
                span.innerText = alias;
                aliasesContainer.appendChild(span);
            });
        } catch(e) {}
    }

    const btnDelete = document.getElementById('btn_delete_tvdb');
    btnDelete.onclick = () => deleteTvdbCacheEntry(show.tvdb_id);

    const epContainer = document.getElementById('modal_tvdb_episodes_container');
    epContainer.innerHTML = '<div class="text-xs text-gray-500 italic flex items-center justify-center h-32"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando episodios de la base de datos...</div>';
    
    document.getElementById('tvdbInfoModal').classList.remove('hidden');

    try {
        const res = await fetch(`/api/ui/tvdb_cache/${show.tvdb_id}/episodes`);
        const data = await res.json();
        if(data.success && data.episodes.length > 0) {
            renderTvdbEpisodes(data.episodes, epContainer);
        } else {
            epContainer.innerHTML = '<div class="text-xs text-red-400 italic p-2 bg-red-900/20 border border-red-900/50 rounded">No se encontraron episodios locales. Utiliza el botón "Re-escanear TVDB" desde la ficha del torrent.</div>';
        }
    } catch(e) {
        epContainer.innerHTML = '<div class="text-xs text-red-500 italic">Error de red cargando episodios.</div>';
    }
}

/**
 * Renderiza un acordeón interactivo agrupando la lista plana de episodios por su temporada.
 */
function renderTvdbEpisodes(episodes, container) {
    container.innerHTML = '';
    
    const seasons = {};
    episodes.forEach(ep => {
        if(!seasons[ep.season_number]) seasons[ep.season_number] = [];
        seasons[ep.season_number].push(ep);
    });

    Object.keys(seasons).sort((a,b) => parseInt(a) - parseInt(b)).forEach(seasonNum => {
        const seasonDiv = document.createElement('div');
        seasonDiv.className = "border border-gray-800 rounded bg-gray-900/30 overflow-hidden mb-2";
        
        const header = document.createElement('div');
        header.className = "px-3 py-2 bg-gray-800/50 hover:bg-gray-800 cursor-pointer flex justify-between items-center transition select-none";
        header.onclick = () => toggleSeasonAccordion(seasonNum);
        header.innerHTML = `
            <span class="text-sm font-bold text-blue-400">Temporada ${seasonNum}</span>
            <div class="flex items-center space-x-3">
                <span class="text-[10px] bg-black border border-gray-700 px-1.5 py-0.5 rounded text-gray-400 font-mono">${seasons[seasonNum].length} eps</span>
                <i id="icon_season_${seasonNum}" class="fa-solid fa-chevron-down text-gray-500 text-xs transition-transform duration-300"></i>
            </div>
        `;
        
        const body = document.createElement('div');
        body.id = `body_season_${seasonNum}`;
        body.className = "hidden flex-col divide-y divide-gray-800 border-t border-gray-800";
        
        seasons[seasonNum].forEach(ep => {
            const epRow = document.createElement('div');
            epRow.className = "px-3 py-3 flex justify-between items-center hover:bg-gray-800/30 transition border-b border-gray-800/50 last:border-0";
            epRow.innerHTML = `
                <div class="flex items-center space-x-3 w-full pr-4">
                    <span class="text-xs font-mono text-gray-600 bg-black px-1.5 py-0.5 rounded border border-gray-800 w-8 text-center shrink-0">${ep.episode_number}</span>
                    <span class="text-xs text-gray-300 leading-relaxed break-words whitespace-normal py-1" title="${ep.name_es}">${ep.name_es}</span>
                </div>
                <span class="text-[10px] text-gray-600 font-mono ml-2 shrink-0">${ep.air_date || 'Sin fecha'}</span>
            `;
            body.appendChild(epRow);
        });
        
        seasonDiv.appendChild(header);
        seasonDiv.appendChild(body);
        container.appendChild(seasonDiv);
    });
}

/**
 * Muestra u oculta la lista de episodios de una temporada específica (acordeón) 
 * y anima el giro del icono chevron de indicación.
 */
function toggleSeasonAccordion(seasonNum) {
    const body = document.getElementById(`body_season_${seasonNum}`);
    const icon = document.getElementById(`icon_season_${seasonNum}`);
    if (body.classList.contains('hidden')) {
        body.classList.remove('hidden');
        body.classList.add('flex');
        icon.classList.add('rotate-180');
    } else {
        body.classList.add('hidden');
        body.classList.remove('flex');
        icon.classList.remove('rotate-180');
    }
}

/**
 * Cierra el modal de la ficha extendida de TheTVDB.
 */
function closeTvdbModal() {
    document.getElementById('tvdbInfoModal').classList.add('hidden');
}

/**
 * Elimina una ficha de la base de conocimientos y todos sus episodios asociados.
 */
async function deleteTvdbCacheEntry(tvdb_id) {
    if(!confirm("¿Borrar esta serie de la base de conocimientos? (La IA tendrá que volver a buscarla la próxima vez).")) return;
    
    try {
        const res = await fetch(`/api/ui/tvdb_cache/${tvdb_id}`, { method: 'DELETE' });
        const data = await res.json();
        if(data.success) {
            showToast("Serie eliminada de la biblioteca.");
            closeTvdbModal();
            loadTvdbCache();
        } else {
            showToast("Error al eliminar.", false);
        }
    } catch(e) {
        showToast("Error de red.", false);
    }
}

// Al cargar la página, se pinta la cuadrícula de la biblioteca automáticamente
document.addEventListener("DOMContentLoaded", () => {
    if(document.getElementById('tvdb-grid')) {
        loadTvdbCache();
    }
});