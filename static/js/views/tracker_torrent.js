/*
 * BLOQUE FICHA TECNICA DEL TORRENT
 */

/*
 * Funcion para ajustar la altura util de la ficha tecnica del torrent al viewport disponible.
 */
function adjustDynamicHeights() {
    const mainArea = document.getElementById('torrent_detail_layout');
    if (!mainArea) return;
    const rect = mainArea.getBoundingClientRect();
    const availableHeight = window.innerHeight - rect.top - 16;
    if (availableHeight > 300) mainArea.style.height = `${availableHeight}px`;
}
/*
 * Objetivo: cargar por el GUID oculto el snapshot remoto y telemetría local para
 * pintar la ficha. No recibe parámetros ni devuelve un valor útil; actualiza DOM,
 * estado global y puede iniciar polling qB. Depende del endpoint de detalle,
 * formatBytes y helpers de tags; errores HTTP/red conservan la vista y avisan.
 * Swarm y freeleech visibles pertenecen siempre a la última observación tracker.
 */
async function loadTorrentDetail() {
    const guid = document.getElementById('current_torrent_guid').value;

    try {
        const res = await fetch(`/api/ui/cache/torrent/${guid}`);
        const data = await res.json();

        if (data.success) {
            const t = data.torrent;
            window.currentTorrent = t;

            const posterUrl = t.poster_url ? `/api/ui/poster?url=${encodeURIComponent(t.poster_url)}` : '/static/img/Kitsunarr-logo-512x512.png';
            const posterEl = document.getElementById('detail_poster');
            /*
             * Sustituye una portada que no cargó por el asset local. Recibe el
             * evento implícito, no devuelve datos y solo muta src; no hace fetch manual.
             */
            posterEl.onerror = () => { posterEl.src = '/static/img/Kitsunarr-logo-512x512.png'; };
            posterEl.src = posterUrl;
            if (t.is_batch) document.getElementById('detail_batch_badge').classList.remove('hidden');
            else document.getElementById('detail_batch_badge').classList.add('hidden');

            const aiTitleEl = document.getElementById('detail_ai_title');
            const hasAiTitle = (t.ai_status === 'Listo' || t.ai_status === 'Manual') && t.ai_translated_title;
            const bestTitle = hasAiTitle
                ? t.ai_translated_title
                : (t.enriched_title || t.original_title || 'Sin título disponible');

            aiTitleEl.innerText = bestTitle;
            aiTitleEl.classList.remove('text-gray-500', 'italic');
            document.getElementById('detail_original_title').innerText = getCleanTorrentTitle(t);

            renderDetailTags(t.tags);

            document.getElementById('detail_guid').innerText = t.guid;

            let seasonText = "Desconocida";
            if (t.parsed_season === 0) seasonText = "S00 (Especiales)";
            else if (t.parsed_season !== null) seasonText = `S${t.parsed_season.toString().padStart(2, '0')}`;
            document.getElementById('detail_parsed_season').innerText = seasonText;

            let freeleechText = "No activo";
            if (t.is_freeleech) {
                if (t.freeleech_until) {
                    const flDate = new Date(t.freeleech_until);
                    freeleechText = `Hasta ${flDate.toLocaleDateString()} ${flDate.getHours()}:${flDate.getMinutes().toString().padStart(2, '0')}`;
                } else {
                    freeleechText = "Activo (sin vencimiento indicado)";
                }
                document.getElementById('detail_freeleech').classList.add('text-green-400');
            } else {
                document.getElementById('detail_freeleech').classList.remove('text-green-400');
            }
            document.getElementById('detail_freeleech').innerText = freeleechText;

            const aiStatus = document.getElementById('detail_ai_status');
            aiStatus.innerText = t.ai_status;
            aiStatus.className = `text-[10px] uppercase font-bold truncate ${t.ai_status === 'Listo' ? 'text-green-500' : 'text-yellow-500'}`;

            const tvdbStatus = document.getElementById('detail_tvdb_status');
            const tvdbDisplay = (t.tvdb_status === 'Listo' && t.tvdb_id) ? `TVDB-${t.tvdb_id}` : t.tvdb_status;
            tvdbStatus.innerText = tvdbDisplay;
            tvdbStatus.className = `text-[10px] uppercase font-bold truncate ${t.tvdb_status === 'Listo' ? 'text-green-500' : 'text-blue-400'}`;

            const backBtnText = document.getElementById('btn_detail_back_text');
            if (backBtnText) {
                backBtnText.innerText = t.tvdb_id ? 'Volver a Serie' : 'Volver a Caché';
            }

            document.getElementById('detail_size').innerText = formatBytes(t.size_bytes);
            document.getElementById('detail_seeders').innerText = t.tracker_seeders ?? '-';
            document.getElementById('detail_leechers').innerText = t.tracker_leechers ?? '-';

            document.getElementById('detail_description').innerText = t.description || 'El uploader no incluyó ninguna descripción para este torrent.';

            renderDetailFileList(t.raw_filenames, t.rename_mapping);
            updateTelemetryUI(t);
            adjustDynamicHeights();

        } else {
            showToast("No se pudo encontrar el torrent.", false);
        }
    } catch (e) {
        showToast("Error de red al cargar la ficha.", false);
    }
}

/*
 * BLOQUE RENDERIZADO DE COMPONENTES DEL TORRENT
 */

/*
 * Renderiza tags JSON dentro de la ficha. Recibe texto serializado, no devuelve
 * valor y reemplaza el contenedor; usa getTagData para estilo y escapeHtml para
 * texto tracker/manual. Datos ausentes/corruptos producen una lista vacía.
 */
function renderDetailTags(tagsJson) {
    const container = document.getElementById('detail_tags_container');
    container.innerHTML = '';
    if (!tagsJson) return;

    try {
        const tags = JSON.parse(tagsJson);
        /*
         * Convierte cada tag en un badge. Recibe el valor iterado, no devuelve valor,
         * añade un nodo y no modifica el array; depende de getTagData/escapeHtml.
         * Cualquier error queda absorbido por el try/catch de renderDetailTags.
         */
        tags.forEach(tag => {
            const span = document.createElement('span');
            const tagData = getTagData(tag);

            span.className = `flex items-center text-[10px] font-bold px-2 py-0.5 rounded border shadow-sm ${tagData.style}`;
            span.innerHTML = `<span class="mr-1.5">${tagData.icon}</span> ${escapeHtml(tag)}`;
            container.appendChild(span);
        });
    } catch (e) {}
}
/*
 * Renderiza archivos y mappings preparados para Sonarr. Recibe dos JSON, no
 * devuelve valor y reemplaza la lista/count; escapa nombres remotos/manuales antes
 * de usar markup. Ausencia muestra placeholder y JSON corrupto muestra error fijo.
 */
function renderDetailFileList(rawJson, mappingJson) {
    const list = document.getElementById('detail_file_list');
    const countBadge = document.getElementById('detail_file_count');
    list.innerHTML = '';

    if (!rawJson) {
        list.innerHTML = '<li class="p-6 text-center text-gray-600 text-xs italic">No hay información de archivos disponible.</li>';
        return;
    }

    try {
        const files = JSON.parse(rawJson);
        let mapping = {};
        if (mappingJson) mapping = JSON.parse(mappingJson);

        countBadge.innerText = `${files.length} archivos`;

        /*
         * Construye una fila por archivo. Recibe nombre/índice, no devuelve valor,
         * añade DOM y no modifica los JSON; depende de escapeHtml y del mapping ya
         * parseado. Sus errores quedan capturados por renderDetailFileList.
         */
        files.forEach((filename, idx) => {
            filename = String(filename ?? '');
            const li = document.createElement('li');
            li.className = "p-3 flex flex-col hover:bg-white/5 transition";

            let icon = 'fa-file';
            if (filename.endsWith('.mkv') || filename.endsWith('.mp4')) icon = 'fa-file-video text-blue-400';
            else if (filename.endsWith('.txt') || filename.endsWith('.nfo')) icon = 'fa-file-lines text-gray-500';

            const renamedName = mapping[filename];
            const safeFilename = escapeHtml(filename);
            const safeRenamedName = escapeHtml(renamedName);

            let html = `
                <div class="flex items-start space-x-3">
                    <span class="text-[9px] font-mono text-gray-600 mt-0.5 shrink-0">${(idx + 1).toString().padStart(2, '0')}</span>
                    <i class="fa-solid ${icon} text-[10px] mt-0.5 shrink-0"></i>
                    <span class="text-[11px] text-gray-400 break-all leading-tight font-mono">${safeFilename}</span>
                </div>
            `;

            if (filename.endsWith('.mkv') || filename.endsWith('.mp4')) {
                if (renamedName) {
                    html += `
                        <div class="flex items-start space-x-3 mt-1.5 ml-4 pl-3 border-l border-gray-700">
                            <i class="fa-solid fa-arrow-turn-up fa-rotate-90 text-[10px] text-green-500 shrink-0 mt-0.5"></i>
                            <span class="text-[11px] text-green-400 break-all leading-tight font-mono font-bold">${safeRenamedName}</span>
                        </div>
                    `;
                } else {
                    html += `
                        <div class="flex items-start space-x-3 mt-1.5 ml-4 pl-3 border-l border-gray-800">
                            <i class="fa-solid fa-arrow-turn-up fa-rotate-90 text-[10px] text-gray-600 shrink-0 mt-0.5"></i>
                            <span class="text-[10px] text-gray-600 break-all leading-tight font-mono italic">Pendiente de renombrado inteligente...</span>
                        </div>
                    `;
                }
            }

            li.innerHTML = html;
            list.appendChild(li);
        });
    } catch (e) {
        list.innerHTML = '<li class="p-4 text-red-500 text-[10px] text-center">Error procesando la lista de archivos (JSON corrupto).</li>';
    }
}

/*
 * BLOQUE ACCIONES DEL TORRENT
 */

/*
 * Funcion para iniciar la descarga manual del archivo torrent original.
 */
function downloadTorrentFile() {
    const t = window.currentTorrent;
    if (!t) return;

    const url = `/api/download/${t.guid}_base`;
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();

    showToast("Descarga iniciada...");
}
/*
 * Envía la ficha actual al cliente configurado. No recibe parámetros ni devuelve
 * datos; deshabilita el botón, llama al endpoint que descarga el metainfo, calcula
 * el hash, lo añade a qBittorrent y persiste telemetría. Actualiza la ficha al
 * terminar y muestra errores de red/backend sin iniciar descargas duplicadas.
 */
async function sendTorrentToClient() {
    const t = window.currentTorrent;
    if (!t) return;

    const btn = document.getElementById('btn_send_to_client');
    const originalText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Enviando...';
    }

    showToast("Enviando el .torrent original al cliente...");
    try {
        const response = await fetch(`/api/ui/torrent/${t.guid}/send_to_client`, {
            method: 'POST'
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            showToast(data.error || "No se pudo enviar el torrent al cliente.", false);
            return;
        }
        t.info_hash = data.info_hash;
        showToast(data.already_in_client
            ? "El torrent ya estaba en el cliente. Telemetría actualizada."
            : "Torrent enviado al cliente correctamente.");
        await loadTorrentDetail();
    } catch (error) {
        showToast("Error de red enviando el torrent al cliente.", false);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}
/*
 * Funcion para enviar el torrent actual a un nuevo analisis de IA.
 */
async function forceAiProcessing() {
    const guid = document.getElementById('current_torrent_guid').value;
    const accepted = await appConfirm(
        '¿Quieres forzar a la IA para que vuelva a analizar este torrent ahora mismo?',
        'Confirmar reanálisis IA'
    );
    if (!accepted) return;

    showToast("Enviando a la IA...");
    try {
        const res = await fetch('/api/ui/ai/force_specific', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ guids: [guid] })
        });
        const data = await res.json();
        if (data.success) {
            showToast("IA en proceso. Recargando en 3 segundos...");
            setTimeout(() => loadTorrentDetail(), 3000);
        } else if (data.error) {
            showToast(data.error, false);
        }
    } catch (e) {
        showToast("Error de red.", false);
    }
}
/*
 * Funcion para eliminar el torrent actual de la cache local.
 */
async function deleteCurrentTorrent() {
    const guid = document.getElementById('current_torrent_guid').value;
    const accepted = await appConfirm(
        '¿Borrar permanentemente este torrent de la base de datos?\nEsta acción no se puede deshacer.',
        'Confirmar eliminación'
    );
    if (!accepted) return;

    try {
        const res = await fetch(`/api/ui/cache/${guid}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast("Torrent eliminado.");
            window.location.href = '/cache';
        }
    } catch (e) {
        showToast("Error de red.", false);
    }
}
/*
 * Funcion para abrir la edicion manual desde la ficha tecnica.
 */
function openEditModalFromDetail() {
    if (window.currentTorrent) window.location.href = `/cache/edit/${window.currentTorrent.guid}`;
}
/*
 * Funcion de retorno inteligente desde la ficha tecnica hacia la vista anterior o la cache.
 */
function goBackSmart() {
    if (window.history.length > 1) {
        window.history.back();
        return;
    }
    window.location.href = '/cache';
}

/*
 * BLOQUE TELEMETRIA QBITTORRENT
 */

/*
 * Pinta estado, progreso, velocidades, ratio y hash de qBittorrent. Recibe la
 * ficha actual y no devuelve datos; alterna telemetría/aviso y gestiona el poller
 * según exista info_hash. Solo modifica DOM/timers y depende de formatBytes,
 * formatTime, formatRatio, renderQbStatusLabel y start/stopTelemetryPolling.
 */
function updateTelemetryUI(t) {
    const container = document.getElementById('telemetry_container');
    const missing = document.getElementById('telemetry_missing');

    if (t.info_hash) {
        container.classList.remove('hidden');
        missing.classList.add('hidden');

        const progressRatio = Math.max(0, Math.min(1, Number(t.progress || 0)));
        const progressPercent = progressRatio * 100;
        document.getElementById('info_qb_progress_bar').style.width = `${progressPercent.toFixed(1)}%`;
        document.getElementById('info_qb_progress_text').innerText = `${progressPercent.toFixed(1)}%`;
        document.getElementById('info_qb_status').innerText = renderQbStatusLabel(t.client_status, t.exists_in_client);
        document.getElementById('info_qb_dlspeed').innerText = formatBytes(t.download_speed || 0) + '/s';
        document.getElementById('info_qb_upspeed').innerText = formatBytes(t.upload_speed || 0) + '/s';
        document.getElementById('info_qb_ratio').innerText = formatRatio(t.ratio);
        document.getElementById('info_qb_eta').innerText = (t.eta && t.eta > 8000000) ? '∞' : formatTime(t.eta || 0);

        const hashEl = document.getElementById('info_qb_hash');
        if (hashEl) hashEl.innerText = t.info_hash;
        startTelemetryPolling(t.guid);
    } else {
        container.classList.add('hidden');
        missing.classList.remove('hidden');
        stopTelemetryPolling();
    }
}
/*
 * Funcion para normalizar el ratio de qBittorrent en la ficha del torrent.
 */
function formatRatio(value) {
    const ratio = Number(value);
    if (!Number.isFinite(ratio) || ratio < 0) return '0.00';
    return ratio.toFixed(2);
}
/*
 * Funcion para traducir estados internos de qBittorrent a etiquetas visibles.
 */
function renderQbStatusLabel(status, existsInClient = true) {
    if (!existsInClient || status === 'not_found') return 'No encontrado';
    const labels = {
        downloading: 'Descargando',
        stalledDL: 'Sin actividad',
        stalledUP: 'Compartiendo',
        uploading: 'Compartiendo',
        pausedDL: 'Pausado',
        pausedUP: 'Pausado',
        queuedDL: 'En cola',
        queuedUP: 'En cola',
        checkingDL: 'Comprobando',
        checkingUP: 'Comprobando',
        checkingResumeData: 'Comprobando',
        forcedDL: 'Descarga forzada',
        forcedUP: 'Subida forzada',
        moving: 'Moviendo',
        error: 'Error',
        missingFiles: 'Archivos no encontrados',
        unknown: 'Desconocido'
    };
    return labels[status] || status || 'Desconocido';
}
/*
 * Funcion para iniciar el refresco periodico de telemetria del torrent.
 */
function startTelemetryPolling(guid) {
    if (window.currentTelemetryInterval) return;
    window.currentTelemetryInterval = setInterval(() => fetchTelemetryData(guid), 5000);
}
/*
 * Funcion para detener el refresco periodico de telemetria al salir de la ficha.
 */
function stopTelemetryPolling() {
    if (window.currentTelemetryInterval) {
        clearInterval(window.currentTelemetryInterval);
        window.currentTelemetryInterval = null;
    }
}
/*
 * Consulta por GUID la telemetría viva de qBittorrent y devuelve Promise<void>.
 * Realiza fetch, actualiza window.currentTorrent y el panel mediante
 * updateTelemetryUI sin sobrescribir el swarm tracker. Errores HTTP/JSON/red se
 * capturan: conserva los últimos valores y comunica el fallo en el estado qB.
 */
async function fetchTelemetryData(guid) {
    try {
        const res = await fetch(`/api/ui/torrent/${guid}/telemetry`);
        const data = await res.json();
        if (!data.success || !data.telemetry) {
            const statusEl = document.getElementById('info_qb_status');
            if (statusEl) statusEl.innerText = data.error || 'Sin telemetría';
            return;
        }

        const telemetry = data.telemetry;
        window.currentTorrent = {
            ...(window.currentTorrent || {}),
            ...telemetry,
            exists_in_client: data.exists_in_client,
        };
        updateTelemetryUI(window.currentTorrent);

    } catch (e) {
        const statusEl = document.getElementById('info_qb_status');
        if (statusEl) statusEl.innerText = 'Error de conexión';
    }
}

/*
 * BLOQUE INICIALIZACION
 */

/*
 * Funcion de inicializacion de la ficha tecnica del torrent y sus ajustes responsive.
 */
document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById('current_torrent_guid')) {
        loadTorrentDetail();
        adjustDynamicHeights();
    }
});

/*
 * Funcion de limpieza de telemetria al abandonar la ficha tecnica.
 */
window.addEventListener('beforeunload', stopTelemetryPolling);

/*
 * Funcion de reajuste visual de la ficha tecnica cuando cambia el tamano de ventana.
 */
window.addEventListener('resize', () => {
    if (document.getElementById('current_torrent_guid')) adjustDynamicHeights();
});
