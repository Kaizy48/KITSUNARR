/*
 * BLOQUE BIBLIOTECA TVDB
 */

/*
 * Traduce el estado de TheTVDB a una etiqueta en español para mostrarla en la biblioteca.
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
 * Formatea fechas TVDB al formato día/mes/año usado en la interfaz.
 */
function formatDateDdMmYyyy(value) {
    if (!value) return 'Desconocido';
    const raw = String(value).trim();
    const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return `${m[3]}/${m[2]}/${m[1]}`;

    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return raw;
    return d.toLocaleDateString('es-ES', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric'
    });
}

/*
 * Carga la biblioteca TVDB local desde la API y actualiza la cuadrícula.
 */
async function loadTvdbCache() {
    const grid = document.getElementById('tvdb-grid');
    if (!grid) return;

    grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando biblioteca...</div>';

    try {
        const res = await fetch('/api/ui/tvdb_cache');
        const data = await res.json();
        
        if (data.tvdb_cache) {
            window.currentTvdbData = data.tvdb_cache;
            renderTvdbGrid(window.currentTvdbData);
            toggleTvdbBatchSelection();
        } else {
            grid.innerHTML = '<div class="col-span-full text-center p-8 text-red-500">Error al cargar la biblioteca TVDB.</div>';
        }
    } catch (e) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-red-500">Error de red.</div>';
    }
}

/*
 * Renderiza la cuadrícula de series TVDB guardadas en la biblioteca local.
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
        
        card.onclick = (e) => {
            if(e.target.tagName === 'INPUT') return;
            openTvdbModal(show.tvdb_id);
        };

        const posterUrl = show.poster_path ? `/api/ui/poster?url=${encodeURIComponent(show.poster_path)}` : '/static/img/Kitsunarr-logo-512x512.png';
        const statusColor = show.status === 'Ended' ? 'bg-red-500' : (show.status === 'Continuing' ? 'bg-green-500' : 'bg-gray-500');
        const statusLabel = translateTvdbStatus(show.status);

        card.innerHTML = `
            <div class="relative aspect-[2/3] rounded-lg overflow-hidden shadow-lg border border-gray-800">
                <div class="absolute top-2 left-2 z-20">
                    <input type="checkbox" value="${show.tvdb_id}" onchange="toggleTvdbBatchSelection()" class="tvdb-batch-checkbox w-5 h-5 accent-red-500 bg-black border-gray-700 rounded cursor-pointer shadow-[0_0_15px_rgba(0,0,0,1)] opacity-0 group-hover:opacity-100 checked:opacity-100 transition-opacity" onclick="event.stopPropagation()">
                </div>
                
                <img src="${posterUrl}" alt="Poster" class="w-full h-full object-cover">
                <div class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                    <i class="fa-solid fa-magnifying-glass-plus text-3xl text-white"></i>
                </div>
                <div class="absolute top-2 right-2 ${statusColor} w-3 h-3 rounded-full border border-black shadow-sm" title="${statusLabel}"></div>
            </div>
            <div class="mt-2 text-center">
                <h3 class="poster-card-title px-1">${show.series_name_es || show.series_name_original}</h3>
                <p class="poster-card-meta text-gray-500">${show.first_aired ? show.first_aired.substring(0,4) : '----'}</p>
            </div>
        `;
        grid.appendChild(card);
    });

    if (typeof refreshPosterLayouts === 'function') {
        refreshPosterLayouts();
    }
}

/*
 * Filtra la biblioteca TVDB por nombre, nombre original o alias.
 */
function filterTvdbGrid() {
    const q = document.getElementById('tvdbSearch').value.toLowerCase();
    const filtered = window.currentTvdbData.filter(show => {
        const matchEs = show.series_name_es && show.series_name_es.toLowerCase().includes(q);
        const matchOriginal = show.series_name_original && show.series_name_original.toLowerCase().includes(q);
        const matchAlias = show.aliases && show.aliases.toLowerCase().includes(q); 
        return matchEs || matchOriginal || matchAlias;
    });
    renderTvdbGrid(filtered);
    toggleTvdbBatchSelection();
}

/*
 * BLOQUE SELECCION Y BORRADO TVDB
 */

/*
 * Habilita o bloquea el borrado por lote según las fichas TVDB seleccionadas.
 */
function toggleTvdbBatchSelection() {
    const checkedBoxes = document.querySelectorAll('.tvdb-batch-checkbox:checked');
    const btnDelete = document.getElementById('btn_batch_delete_tvdb');
    
    if(!btnDelete) return;

    if (checkedBoxes.length > 0) {
        btnDelete.disabled = false;
        btnDelete.classList.remove('opacity-50', 'cursor-not-allowed');
    } else {
        btnDelete.disabled = true;
        btnDelete.classList.add('opacity-50', 'cursor-not-allowed');
    }
}

/*
 * Borra las fichas TVDB seleccionadas usando la protección de vínculos con torrents.
 */
async function deleteSelectedTvdb() {
    const checkboxes = document.querySelectorAll('.tvdb-batch-checkbox:checked');
    const ids = Array.from(checkboxes).map(cb => cb.value);
    
    if(ids.length === 0) return;
    
    const accepted = await appConfirm(
        `¿Estás completamente seguro de ELIMINAR ${ids.length} fichas maestras de la base de datos?\n\nLa IA tendrá que volver a buscarlas y descargarlas la próxima vez.`,
        'Confirmar eliminación TVDB'
    );
    if (!accepted) return;

    showToast(`Borrando ${ids.length} fichas...`);
    
    try {
        let deleted = 0;
        for (const id of ids) {
            const ok = await deleteTvdbWithGuard(id);
            if (ok) deleted += 1;
        }

        showToast(`Proceso finalizado. Eliminadas ${deleted} de ${ids.length} fichas.`);
        loadTvdbCache();
        
    } catch(e) {
        showToast("Hubo un error de red borrando algunas fichas.", false);
        loadTvdbCache();
    }
}

/*
 * BLOQUE MODAL Y EPISODIOS TVDB
 */

/*
 * Abre la ficha maestra TVDB, carga sus datos locales y solicita sus episodios.
 */
async function openTvdbModal(tvdb_id) {
    const show = window.currentTvdbData.find(x => x.tvdb_id === tvdb_id);
    if (!show) return;

    document.getElementById('modal_tvdb_poster').src = show.poster_path ? `/api/ui/poster?url=${encodeURIComponent(show.poster_path)}` : '/static/img/Kitsunarr-logo-512x512.png';
    document.getElementById('modal_tvdb_title_es').innerText = show.series_name_es || 'Sin título ES';
    document.getElementById('modal_tvdb_title_original').innerText = show.series_name_original || 'Sin título original';
    document.getElementById('modal_tvdb_status').innerText = translateTvdbStatus(show.status);
    document.getElementById('modal_tvdb_aired').innerText = formatDateDdMmYyyy(show.first_aired);
    document.getElementById('modal_tvdb_id').innerText = show.tvdb_id;
    document.getElementById('modal_tvdb_overview').innerText = show.overview_es || show.overview_original || show.overview_basic || 'No hay sinopsis disponible.';

    if (show.last_updated) {
        let dateStr = show.last_updated;
        if (!dateStr.endsWith("Z") && !dateStr.includes("+")) {
            dateStr += "Z";
        }
        const dateObj = new Date(dateStr);
        const datePart = formatDateDdMmYyyy(dateObj.toISOString());
        const timePart = dateObj.toLocaleTimeString('en-US', {
            hour: 'numeric',
            minute: '2-digit',
            second: '2-digit',
            hour12: true
        });
        document.getElementById('modal_tvdb_last_updated').innerText = `Sincronizado: ${datePart} ${timePart}`;
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
    if (btnDelete) btnDelete.onclick = () => deleteTvdbCacheEntry(show.tvdb_id);

    const btnRefresh = document.getElementById('btn_refresh_tvdb');
    if (btnRefresh) btnRefresh.onclick = () => refreshTvdbEntry(show.tvdb_id);

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

/*
 * Refresca una ficha TVDB concreta y vuelve a abrir el modal con los datos actualizados.
 */
async function refreshTvdbEntry(tvdb_id) {
    const btnRefresh = document.getElementById('btn_refresh_tvdb');
    if (!btnRefresh) return;

    const originalHtml = btnRefresh.innerHTML;
    btnRefresh.disabled = true;
    btnRefresh.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Actualizando';

    try {
        const res = await fetch(`/api/ui/tvdb_cache/${tvdb_id}/refresh`, { method: 'POST' });
        const data = await res.json();

        if (!data.success) {
            showToast('No se pudo actualizar la ficha TVDB.', false);
            return;
        }

        showToast('Ficha maestra y episodios actualizados.');
        await loadTvdbCache();
        await openTvdbModal(tvdb_id);
    } catch (e) {
        showToast('Error de red actualizando TVDB.', false);
    } finally {
        btnRefresh.disabled = false;
        btnRefresh.innerHTML = originalHtml;
    }
}

/*
 * Renderiza episodios TVDB agrupados por temporada dentro del modal.
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

/*
 * Abre o cierra el acordeón de episodios de una temporada TVDB.
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

/*
 * Cierra el modal de ficha maestra TVDB.
 */
function closeTvdbModal() {
    document.getElementById('tvdbInfoModal').classList.add('hidden');
}

/*
 * Solicita el borrado de una ficha maestra TVDB individual.
 */
async function deleteTvdbCacheEntry(tvdb_id) {
    const accepted = await appConfirm(
        '¿Borrar esta serie de la base de conocimientos? La IA tendrá que volver a buscarla la próxima vez.',
        'Confirmar eliminación TVDB'
    );
    if (!accepted) return;

    try {
        const deleted = await deleteTvdbWithGuard(tvdb_id);
        if (deleted) {
            showToast("Serie eliminada de la biblioteca.");
            closeTvdbModal();
            loadTvdbCache();
        }
    } catch(e) {
        showToast("Error de red.", false);
    }
}

/*
 * Borra una ficha TVDB respetando la protección de torrents vinculados y permitiendo cascada si el usuario confirma.
 */
async function deleteTvdbWithGuard(tvdb_id) {
    const res = await fetch(`/api/ui/tvdb_cache/${tvdb_id}`, { method: 'DELETE' });
    const data = await res.json();

    if (data.success) {
        return true;
    }

    if (data.blocked) {
        const linkedCount = data.linked_torrents_count || 0;
        showToast(
            `No se puede borrar solo la ficha maestra. Tiene ${linkedCount} ficha(s) torrent vinculada(s).`,
            false
        );

        const acceptedCascade = await appConfirm(
            `La ficha maestra TVDB tiene ${linkedCount} ficha(s) torrent vinculada(s). ` +
            `No se puede borrar de forma aislada.\n\n` +
            `Si continúas, se borrará la ficha maestra junto con todas las fichas torrent vinculadas.`,
            'Confirmar borrado en cascada'
        );

        if (!acceptedCascade) return false;

        const cascadeRes = await fetch(
            `/api/ui/tvdb_cache/${tvdb_id}?cascade_linked_torrents=true`,
            { method: 'DELETE' }
        );
        const cascadeData = await cascadeRes.json();

        if (cascadeData.success) {
            const deletedCount = cascadeData.deleted_linked_torrents || 0;
            showToast(`Borrado en cascada completado. Torrents eliminados: ${deletedCount}.`);
            return true;
        }

        showToast(cascadeData.error || 'Error al borrar en cascada.', false);
        return false;
    }

    showToast(data.error || 'Error al eliminar.', false);
    return false;
}

/*
 * BLOQUE IMPORTACION Y EXPORTACION TVDB
 */

/*
 * Funcion para abrir el modal de exportacion desde la biblioteca TVDB.
 */
function openExportModal() {
    document.getElementById('exportModal').classList.remove('hidden');
}

/*
 * Funcion para cerrar el modal de exportacion desde la biblioteca TVDB.
 */
function closeExportModal() {
    document.getElementById('exportModal').classList.add('hidden');
}

/*
 * Funcion para iniciar la descarga del JSON seleccionado desde la biblioteca TVDB.
 */
function submitExport() {
    const selected = document.querySelector('input[name="export_type"]:checked');
    const module = selected ? selected.value : 'bundle';

    window.location.href = `/api/ui/cache/export?module=${module}`;
    closeExportModal();
    showToast("Generando y descargando el archivo...");
}

/*
 * Funcion para importar un JSON de Kitsunarr desde la biblioteca TVDB.
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
 * Inicializa la biblioteca TVDB cuando la vista está cargada.
 */
document.addEventListener("DOMContentLoaded", () => {
    if(document.getElementById('tvdb-grid')) {
        loadTvdbCache();
    }
});
