/*
 * BLOQUE CARGA DE EDICION DE TORRENT
 */

/*
 * Funcion de carga de la ficha editable para completar los campos, tags, archivos y candidatos TVDB del torrent.
 */
async function loadEditData() {
    const guid = document.getElementById('edit_cache_guid').value;
    if (!guid) return;

    try {
        const res = await fetch(`/api/ui/cache/torrent/${guid}`);
        const data = await res.json();

        if (data.success) {
            const t = data.torrent;
            window.currentTorrent = t;

            const p = document.getElementById('edit_poster');
            p.src = t.poster_url ? `/api/ui/poster?url=${encodeURIComponent(t.poster_url)}` : '/static/img/Kitsunarr-logo-512x512.png';

            document.getElementById('edit_guid').innerText = t.guid;
            document.getElementById('edit_seeders').innerText = t.peers_seeds || '0';
            document.getElementById('edit_leechers').innerText = t.peers_leechs || '0';
            document.getElementById('edit_freeleech').innerText = t.is_freeleech ? 'Activo' : 'No activo';

            document.getElementById('edit_original_title').innerText = getCleanTorrentTitle(t);
            document.getElementById('edit_cache_title').value = t.ai_translated_title || getCleanTorrentTitle(t) || '';
            document.getElementById('edit_size').innerText = formatBytes(t.size_bytes);
            document.getElementById('edit_parsed_season').value = t.parsed_season !== null ? t.parsed_season : '';

            const aiStatusEl = document.getElementById('edit_ai_status');
            aiStatusEl.innerText = t.ai_status;
            aiStatusEl.className = `text-xs font-bold truncate ${t.ai_status === 'Listo' ? 'text-green-500' : 'text-yellow-500'}`;

            const tvdbStatusEl = document.getElementById('edit_tvdb_status');
            tvdbStatusEl.innerText = (t.tvdb_status === 'Listo' && t.tvdb_id) ? `TVDB-${t.tvdb_id}` : t.tvdb_status;
            tvdbStatusEl.className = `text-xs font-bold truncate ${t.tvdb_status === 'Listo' ? 'text-green-500' : 'text-blue-400'}`;

            window.currentTags = t.tags ? JSON.parse(t.tags) : [];
            renderEditTags();
            renderPalette();

            document.getElementById('edit_cache_description').value = t.description || '';
            renderEditFileList(t.raw_filenames, t.rename_mapping);

            loadTvdbCandidates(guid, t.tvdb_id);

        } else {
            showToast("Error cargando el torrent: " + data.error, false);
        }
    } catch (e) {
        showToast("Error de red al cargar datos de edición.", false);
    }
}

/*
 * BLOQUE EDICION DE TAGS
 */

/*
 * Funcion de renderizado de las etiquetas asignadas al torrent en la vista de edicion.
 */
function renderEditTags() {
    const container = document.getElementById('edit_tags_container');
    container.innerHTML = '';

    if (window.currentTags.length === 0) {
        container.innerHTML = '<span class="text-[10px] text-gray-600 italic py-1">Ninguna etiqueta asignada...</span>';
    }

    window.currentTags.forEach((tag, index) => {
        const span = document.createElement('span');
        const tagData = getTagData(tag);

        span.className = `flex items-center px-2 py-0.5 rounded border text-[9px] font-bold shadow-sm ${tagData.style}`;
        span.innerHTML = `
            <span class="mr-1.5">${tagData.icon}</span> ${tag}
            <button onclick="removeTag(${index})" class="ml-2 opacity-70 hover:opacity-100 hover:text-red-400 transition">
                <i class="fa-solid fa-circle-xmark"></i>
            </button>
        `;
        container.appendChild(span);
    });
}
/*
 * Funcion de renderizado de la paleta de etiquetas rapidas para enriquecer la ficha del torrent.
 */
function renderPalette() {
    const container = document.getElementById('edit_palette_container');
    container.innerHTML = '';

    STANDARD_TAGS.forEach(tag => {
        const btn = document.createElement('button');
        const tagData = getTagData(tag);

        btn.className = `flex items-center text-[9px] font-bold px-2 py-0.5 rounded border shadow-sm hover:opacity-70 transition ${tagData.style}`;
        btn.innerHTML = `<span class="mr-1.5">${tagData.icon}</span> ${tag}`;

        btn.onclick = () => {
            if (!window.currentTags.includes(tag)) {
                window.currentTags.push(tag);
                renderEditTags();
            }
        };
        container.appendChild(btn);
    });
}
/*
 * Funcion para anadir una etiqueta personalizada al torrent desde el formulario de edicion.
 */
function addTag() {
    const input = document.getElementById('edit_new_tag');
    const val = input.value.trim();
    if (val && !window.currentTags.includes(val)) {
        window.currentTags.push(val);
        renderEditTags();
    }
    input.value = '';
    input.focus();
}
/*
 * Funcion para retirar una etiqueta del torrent durante la edicion manual.
 */
function removeTag(index) {
    window.currentTags.splice(index, 1);
    renderEditTags();
}

/*
 * BLOQUE ARCHIVOS Y RENOMBRADO
 */

/*
 * Funcion de renderizado de archivos del torrent con campos para definir nombres compatibles con Sonarr.
 */
function renderEditFileList(rawJson, mappingJson) {
    const list = document.getElementById('edit_file_list');
    const countBadge = document.getElementById('edit_file_count');
    list.innerHTML = '';

    if (!rawJson) {
        list.innerHTML = '<li class="p-6 text-center text-gray-600 text-xs italic">No hay archivos.</li>';
        return;
    }

    try {
        const files = JSON.parse(rawJson);
        let mapping = mappingJson ? JSON.parse(mappingJson) : {};
        countBadge.innerText = `${files.length} archivos`;

        files.forEach((filename, idx) => {
            const li = document.createElement('li');
            li.className = "p-3 flex flex-col gap-2 hover:bg-white/5 transition";

            const isVideo = filename.endsWith('.mkv') || filename.endsWith('.mp4');
            const renamedVal = mapping[filename] || '';

            let html = `
                <div class="flex items-start space-x-3">
                    <span class="text-[9px] font-mono text-gray-600 mt-1 shrink-0">${(idx + 1).toString().padStart(2, '0')}</span>
                    <span class="text-[11px] text-gray-500 break-all leading-tight font-mono">${filename}</span>
                </div>
            `;

            if (isVideo) {
                html += `
                    <div class="flex items-center space-x-3 ml-4">
                        <i class="fa-solid fa-arrow-turn-up fa-rotate-90 text-[10px] text-blue-500"></i>
                        <input type="text" data-original="${filename}" class="file-rename-input flex-1 bg-black border border-gray-700 focus:border-blue-500 rounded px-2 py-1 text-blue-300 font-mono text-[11px] outline-none" 
                               placeholder="Nombre para Sonarr..." value="${renamedVal}">
                    </div>
                `;
            }

            li.innerHTML = html;
            list.appendChild(li);
        });
    } catch (e) {
        list.innerHTML = '<li class="p-4 text-red-500 text-xs text-center">Error en JSON de archivos.</li>';
    }
}

/*
 * BLOQUE OMNIBOX TVDB
 */

/*
 * Funcion de carga de candidatos TVDB locales y sugeridos para vincular manualmente el torrent.
 */
async function loadTvdbCandidates(guid, currentTvdbId) {
    try {
        const [resLocal, resSpecific] = await Promise.all([
            fetch('/api/ui/tvdb/local_candidates'),
            fetch(`/api/ui/torrent/${guid}/candidates`)
        ]);
        const dataLocal = await resLocal.json();
        const dataSpecific = await resSpecific.json();

        if (dataLocal.success) window.currentLocalCandidates = dataLocal.results;
        if (dataSpecific.success) window.currentSpecificCandidates = dataSpecific.results;

        if (currentTvdbId) {
            const show = window.currentLocalCandidates.find(s => s.tvdb_id === currentTvdbId);
            if (show) selectOmniboxItem(show.tvdb_id, show.series_name_es || show.series_name_original);
            else selectOmniboxItem(currentTvdbId, `ID: ${currentTvdbId}`);
        }
    } catch (e) {}
}
/*
 * Funcion para mostrar el selector de candidatos TVDB de la ficha editable.
 */
function showOmnibox() {
    filterOmnibox();
    document.getElementById('omnibox_dropdown').classList.remove('hidden');
}
/*
 * Funcion para ocultar el selector TVDB despues de permitir la seleccion del usuario.
 */
function hideOmniboxDelayed() {
    setTimeout(() => {
        const d = document.getElementById('omnibox_dropdown');
        if (d) d.classList.add('hidden');
    }, 200);
}
/*
 * Funcion de filtrado del selector TVDB por nombre, alias o identificador.
 */
function filterOmnibox() {
    const q = document.getElementById('edit_tvdb_search').value.toLowerCase().trim();
    const dropdown = document.getElementById('omnibox_dropdown');
    dropdown.innerHTML = '';
    let hasResults = false;

    /*
     * Funcion interna para encontrar coincidencias de alias TVDB dentro del omnibox.
     */
    const findAliasMatch = (show) => {
        const aliases = extractShowAliases(show.aliases);
        if (!q) return null;
        return aliases.find(a => a.toLowerCase().includes(q)) || null;
    };

    /*
     * Funcion interna para decidir si una serie TVDB encaja con la busqueda del usuario.
     */
    const matchesShowQuery = (show) => {
        const nameMatch = !q ||
            show.series_name_es?.toLowerCase().includes(q) ||
            show.series_name_original?.toLowerCase().includes(q) ||
            show.tvdb_id?.includes(q);

        const aliasMatch = findAliasMatch(show);
        return {
            matches: Boolean(nameMatch || aliasMatch),
            aliasMatch,
        };
    };

    const filteredSpecific = window.currentSpecificCandidates
        .map(s => {
            const meta = matchesShowQuery(s);
            return { ...s, _aliasMatch: meta.aliasMatch, _matches: meta.matches };
        })
        .filter(s => s._matches);
    if (filteredSpecific.length > 0) {
        dropdown.innerHTML += `<div class="px-3 py-1 bg-yellow-900/40 text-yellow-500 text-[9px] font-bold uppercase border-b border-yellow-700/50 sticky top-0">Sugerencias IA</div>`;
        filteredSpecific.forEach(s => dropdown.appendChild(createOmniboxItem(s, true)));
        hasResults = true;
    }

    const specificIds = window.currentSpecificCandidates.map(s => s.tvdb_id);
    const filteredLocal = window.currentLocalCandidates
        .filter(s => !specificIds.includes(s.tvdb_id))
        .map(s => {
            const meta = matchesShowQuery(s);
            return { ...s, _aliasMatch: meta.aliasMatch, _matches: meta.matches };
        })
        .filter(s => s._matches);
    if (filteredLocal.length > 0) {
        dropdown.innerHTML += `<div class="px-3 py-1 bg-blue-900/40 text-blue-400 text-[9px] font-bold uppercase border-b border-blue-800/50 sticky top-0">Biblioteca Local</div>`;
        filteredLocal.slice(0, 10).forEach(s => dropdown.appendChild(createOmniboxItem(s, false)));
        hasResults = true;
    }

    if (/^\d+$/.test(q)) {
        const div = document.createElement('div');
        div.className = "p-3 bg-gray-800 text-white text-xs font-bold hover:bg-gray-700 cursor-pointer border-t border-gray-600 transition flex items-center";
        div.innerHTML = `<i class="fa-solid fa-bolt mr-2 text-yellow-500"></i> Forzar ID Manual: ${q}`;
        div.onclick = () => selectOmniboxItem(q, `ID Forzado: ${q}`);
        dropdown.appendChild(div);
        hasResults = true;
    }

    if (!hasResults) dropdown.innerHTML = '<div class="p-4 text-xs text-gray-500 italic text-center">Sin resultados.</div>';
}
/*
 * Funcion de renderizado de una opcion TVDB dentro del selector de vinculacion.
 */
function createOmniboxItem(s, isSuggested) {
    const div = document.createElement('div');
    div.className = `p-2 cursor-pointer border-b border-gray-800 transition ${isSuggested ? 'hover:bg-yellow-900/20' : 'hover:bg-blue-900/20'}`;
    div.innerHTML = `
        <div class="text-xs text-white font-bold truncate">${s.series_name_es || s.series_name_original}</div>
        <div class="text-[10px] text-gray-500 font-mono mt-1">ID: ${s.tvdb_id} • ${s.first_aired ? s.first_aired.substring(0, 4) : '----'}</div>
        ${s._aliasMatch ? `<div class="text-[10px] text-cyan-300 font-bold mt-1"><i class="fa-solid fa-link mr-1"></i> Coincidencia por alias: ${s._aliasMatch}</div>` : ''}
    `;
    div.onclick = () => selectOmniboxItem(s.tvdb_id, s.series_name_es || s.series_name_original);
    return div;
}
/*
 * Funcion de normalizacion de alias TVDB para que el selector pueda buscar en diferentes formatos guardados.
 */
function extractShowAliases(rawAliases) {
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
 * Funcion para aplicar el candidato TVDB elegido a la ficha editable del torrent.
 */
function selectOmniboxItem(tvdbId, displayName) {
    document.getElementById('edit_tvdb_id').value = tvdbId;
    document.getElementById('edit_tvdb_search').value = '';
    document.getElementById('omnibox_selected_text').innerText = displayName;
    document.getElementById('omnibox_selected_badge').classList.remove('hidden');
    document.getElementById('omnibox_dropdown').classList.add('hidden');
}
/*
 * Funcion para limpiar la vinculacion TVDB seleccionada en el formulario.
 */
function clearOmniboxSelection() {
    document.getElementById('edit_tvdb_id').value = '';
    document.getElementById('omnibox_selected_badge').classList.add('hidden');
}
/*
 * Funcion de retorno inteligente desde la edicion hacia la vista anterior o la cache.
 */
function goBackSmart() {
    if (window.history.length > 1) {
        window.history.back();
        return;
    }
    window.location.href = '/cache';
}

/*
 * BLOQUE GUARDADO DE EDICION
 */

/*
 * Funcion de guardado de cambios manuales del torrent en la cache local de KITSUNARR.
 */
async function saveTorrentEdit() {
    const guid = document.getElementById('edit_cache_guid').value;
    const title = document.getElementById('edit_cache_title').value.trim();
    const season = document.getElementById('edit_parsed_season').value;
    const description = document.getElementById('edit_cache_description').value.trim();
    const tvdbId = document.getElementById('edit_tvdb_id').value;

    if (!title) return showToast("El título no puede estar vacío.", false);

    const renameMapping = {};
    document.querySelectorAll('.file-rename-input').forEach(input => {
        const original = input.getAttribute('data-original');
        const renamed = input.value.trim();
        if (renamed) renameMapping[original] = renamed;
    });

    const btn = document.getElementById('btn_save_edit');
    const ogHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Guardando...';

    try {
        const res = await fetch(`/api/ui/cache/${guid}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ai_translated_title: title,
                parsed_season: season === '' ? null : parseInt(season),
                description: description,
                tvdb_id: tvdbId || null,
                tags: JSON.stringify(window.currentTags),
                rename_mapping: JSON.stringify(renameMapping)
            })
        });

        const data = await res.json();
        if (data.success) {
            showToast("Torrent actualizado correctamente.");

            if (tvdbId) {
                setTimeout(() => {
                    window.location.href = '/cache';
                }, 700);
                return;
            }

            setTimeout(() => {
                window.location.href = `/cache/torrent/${guid}`;
            }, 700);
        } else {
            showToast("Error: " + data.error, false);
            btn.disabled = false;
            btn.innerHTML = ogHtml;
        }
    } catch (e) {
        showToast("Error de red.", false);
        btn.disabled = false;
        btn.innerHTML = ogHtml;
    }
}

/*
 * BLOQUE INICIALIZACION
 */

/*
 * Funcion de inicializacion de la vista de edicion cuando existe una ficha de torrent cargable.
 */
document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById('edit_cache_guid')) loadEditData();
});
