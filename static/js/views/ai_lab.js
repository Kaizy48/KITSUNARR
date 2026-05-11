/*
 * BLOQUE LABORATORIO DE IA
 */

let labCacheData = [];

/*
 * Escapa texto para mostrar respuestas de IA y metadatos sin inyectar HTML.
 */
function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/*
 * Convierte objetos o cadenas a un JSON legible para paneles de diagnóstico.
 */
function prettyJson(value) {
    try {
        if (typeof value === 'string') return value;
        return JSON.stringify(value ?? {}, null, 2);
    } catch {
        return String(value ?? '');
    }
}

/*
 * Obtiene el valor numérico de una ficha para ordenar torrents por ID del tracker.
 */
function getGuidNumericValue(t) {
    const raw = t?.source_guid || t?.guid || '';
    const cleaned = String(raw).replace(/^[A-Za-z]{2,5}-/, '');
    const parsed = parseInt(cleaned, 10);
    return Number.isNaN(parsed) ? 0 : parsed;
}

/*
 * Genera una cápsula resumen para la previsualización del resultado de IA.
 */
function buildSummaryBadge(label, value, colorClass = 'text-cyan-300') {
    return `
        <div class="bg-black/60 border border-gray-800 rounded px-3 py-2">
            <div class="text-[10px] uppercase tracking-wider text-gray-500">${escapeHtml(label)}</div>
            <div class="text-xs font-bold ${colorClass}">${escapeHtml(value ?? '-')}</div>
        </div>
    `;
}

/*
 * Renderiza la vista de éxito de una prueba de IA sin guardar cambios en la base.
 */
function renderAITestSuccess(data) {
    const preview = data.preview || {};
    const resolution = preview.tvdb_resolution || {};

    const translatedTitle = preview.ai_translated_title || data?.result?.translated_title || data?.result?.titulo_limpio || '-';
    const acceptedTvdb = resolution.accepted_tvdb_id || '-';
    const seasonText = preview.parsed_season !== undefined && preview.parsed_season !== null
        ? `S${String(preview.parsed_season).padStart(2, '0')}`
        : '-';
    const batchText = preview.is_batch ? 'Sí' : 'No';
    const tvdbStatus = preview.tvdb_status || '-';
    const rejection = resolution.rejection_reason || 'Ninguno';

    return `
        <div class="space-y-4 w-full">
            <div class="text-green-300 font-semibold">Previsualización del post-proceso IA (modo simulación, sin guardar en BD)</div>

            <div class="grid grid-cols-2 lg:grid-cols-5 gap-2">
                ${buildSummaryBadge('Título Final', translatedTitle, 'text-green-300')}
                ${buildSummaryBadge('TVDB Final', acceptedTvdb, acceptedTvdb === '-' ? 'text-yellow-300' : 'text-cyan-300')}
                ${buildSummaryBadge('Temporada', seasonText, 'text-purple-300')}
                ${buildSummaryBadge('Pack', batchText, 'text-blue-300')}
                ${buildSummaryBadge('Estado TVDB', tvdbStatus, tvdbStatus === 'Listo' ? 'text-green-300' : 'text-yellow-300')}
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <div class="bg-black/60 border border-gray-800 rounded p-3">
                    <div class="text-[11px] uppercase tracking-wider text-yellow-500 font-bold mb-2">Diagnóstico de Resolución TVDB</div>
                    <div class="text-xs text-gray-300 leading-relaxed">
                        <div><span class="text-gray-500">Sugerido por IA:</span> <span class="text-cyan-300 font-mono">${escapeHtml(resolution.suggested_tvdb_id || '-')}</span></div>
                        <div><span class="text-gray-500">Aceptado tras validación:</span> <span class="text-green-300 font-mono">${escapeHtml(resolution.accepted_tvdb_id || '-')}</span></div>
                        <div><span class="text-gray-500">Candidatos permitidos:</span> <span class="text-white font-mono">${escapeHtml((resolution.candidate_ids || []).join(', ') || '-')}</span></div>
                        <div><span class="text-gray-500">Rechazo:</span> <span class="text-red-300">${escapeHtml(rejection)}</span></div>
                    </div>
                </div>

                <div class="bg-black/60 border border-gray-800 rounded p-3">
                    <div class="text-[11px] uppercase tracking-wider text-purple-400 font-bold mb-2">JSON Devuelto por el Modelo</div>
                    <pre class="text-xs text-green-300 whitespace-pre-wrap break-words font-mono">${escapeHtml(prettyJson(data.result))}</pre>
                </div>
            </div>

            <div class="bg-black/60 border border-gray-800 rounded p-3">
                <div class="text-[11px] uppercase tracking-wider text-blue-400 font-bold mb-2">Vista Técnica Completa de la Simulación</div>
                <pre class="text-xs text-gray-300 whitespace-pre-wrap break-words font-mono">${escapeHtml(prettyJson(preview))}</pre>
            </div>
        </div>
    `;
}

/*
 * Ejecuta un ping contra el proveedor de IA configurado y muestra el resultado en la terminal del laboratorio.
 */
async function runAIPing() {
    const term = document.getElementById('ping_terminal');
    const btn = document.getElementById('btn_run_ping');
    const provider = document.getElementById('ping_provider').value;
    const model = document.getElementById('ping_model').value;
    const key = document.getElementById('ping_key').value;
    const url = document.getElementById('ping_url').value;

    term.innerHTML = `<div class="text-yellow-400 mb-1">> Iniciando ping al proveedor: ${provider}...</div>`;
    term.innerHTML += `<div class="text-gray-500 mb-2">> Modelo objetivo: ${model}</div>`;

    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Conectando...';

    try {
        const start = performance.now();
        const res = await fetch('/api/ui/ai/ping', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                config: { provider: provider, model_name: model, api_key: key, base_url: url, is_enabled: true, is_automated: false }
            })
        });
        const data = await res.json();
        const ms = (performance.now() - start).toFixed(0);

        if (data.success) {
            term.innerHTML += `<div class="text-green-400">> Respuesta recibida en ${ms}ms:</div>`;
            term.innerHTML += `<div class="text-green-300 ml-4 border-l-2 border-green-800 pl-2 my-2 whitespace-pre-wrap break-words">"${data.result}"</div>`;
            term.innerHTML += `<div class="text-blue-400 font-bold mt-2">> ESTADO: CONEXIÓN ESTABLECIDA.</div>`;
        } else {
            term.innerHTML += `<div class="text-red-400 mt-2">> ERROR: ${data.error}</div>`;
            if ((data.error || '').toLowerCase().includes('motor de ia está desactivado')) {
                showToast('Activa el switch del motor de IA en Configuración para usar Ping.', false);
            }
            if ((data.error || '').toLowerCase().includes('alta demanda')) {
                showToast('Proveedor IA con alta demanda. Reintenta en unos minutos.', false);
            }
            if ((data.error || '').toLowerCase().includes('modelo ia no disponible')) {
                showToast('El modelo no está disponible para este proveedor. Revisa modelo/API.', false);
            }
        }
    } catch (e) {
        term.innerHTML += `<div class="text-red-400 mt-2">> FALLO CRÍTICO: No se pudo alcanzar el servidor interno.</div>`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-wifi text-yellow-500 mr-2"></i> Testear Conexión';
        term.scrollTop = term.scrollHeight;
    }
}

/*
 * Carga las fichas torrent disponibles para probar una normalización de IA.
 */
async function populateAITestDropdown() {
    if (!document.getElementById('test_ai_dropdown_list')) return;
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();

        if (data.success) {
            labCacheData = data.torrents;

            labCacheData.sort((a, b) => {
                const weights = { 'Pendiente': 3, 'Error': 2, 'Manual': 1, 'Listo': 0 };
                const wA = weights[a.ai_status] || 0;
                const wB = weights[b.ai_status] || 0;

                if (wB !== wA) return wB - wA;
                return getGuidNumericValue(b) - getGuidNumericValue(a);
            });

            renderAITestDropdown(labCacheData);
        }
    } catch (e) {
        console.error("Error cargando caché para IA:", e);
    }
}

/*
 * Renderiza el desplegable de torrents disponibles para pruebas de IA.
 */
function renderAITestDropdown(torrents) {
    const list = document.getElementById('test_ai_dropdown_list');
    if (!list) return;
    list.innerHTML = '';

    torrents.slice(0, 50).forEach(t => {
        const div = document.createElement('div');
        div.className = "p-3 border-b border-gray-800 hover:bg-gray-800 cursor-pointer text-sm text-gray-300 flex justify-between items-center transition w-full";

        let statusBadge = '';
        if (t.ai_status === 'Listo') statusBadge = '<span class="text-xs bg-green-900/50 text-green-400 px-2 py-0.5 rounded border border-green-700">Listo</span>';
        else if (t.ai_status === 'Pendiente') statusBadge = '<span class="text-xs bg-yellow-900/50 text-yellow-400 px-2 py-0.5 rounded border border-yellow-700">Pendiente</span>';
        else if (t.ai_status === 'Error') statusBadge = '<span class="text-xs bg-red-900/50 text-red-400 px-2 py-0.5 rounded border border-red-700">Error</span>';
        else if (t.ai_status === 'Manual') statusBadge = '<span class="text-xs bg-blue-900/50 text-blue-400 px-2 py-0.5 rounded border border-blue-700">Manual</span>';

        div.innerHTML = `<span class="truncate flex-1 min-w-0 pr-4">${t.enriched_title}</span> <div class="shrink-0">${statusBadge}</div>`;
        div.onclick = () => selectAITestTorrent(t);
        list.appendChild(div);
    });
}

/*
 * Filtra el desplegable de torrents del laboratorio de IA por título o GUID.
 */
function filterAITestDropdown() {
    const q = document.getElementById('test_ai_search_input').value.toLowerCase();
    if (!q) {
        renderAITestDropdown(labCacheData);
        return;
    }
    const filtered = labCacheData.filter(t =>
        (t.enriched_title && t.enriched_title.toLowerCase().includes(q)) ||
        (t.guid && t.guid.includes(q))
    );
    renderAITestDropdown(filtered);
}

/*
 * Muestra el desplegable de selección de torrents del laboratorio de IA.
 */
function showAITestDropdown() { document.getElementById('test_ai_dropdown_list').classList.remove('hidden'); }

/*
 * Oculta el desplegable de selección de torrents tras permitir el clic de selección.
 */
function hideAITestDropdownDelayed() {
    setTimeout(() => {
        const l = document.getElementById('test_ai_dropdown_list');
        if (l) l.classList.add('hidden');
    }, 200);
}

/*
 * Selecciona una ficha torrent y rellena la previsualización de entrada para la IA.
 */
function selectAITestTorrent(t) {
    document.getElementById('test_ai_search_input').value = t.enriched_title;
    document.getElementById('test_ai_selected_guid').value = t.guid;
    document.getElementById('test_ai_original_title').innerText = t.original_title;
    document.getElementById('test_ai_enriched_title').innerText = t.enriched_title;
    document.getElementById('test_ai_description').innerText = t.description || 'Sin sinopsis proporcionada...';

    const poster = document.getElementById('test_ai_poster');
    if (t.poster_url) {
        poster.style.backgroundImage = `url('/api/ui/poster?url=${encodeURIComponent(t.poster_url)}')`;
        poster.innerHTML = '';
    } else {
        poster.style.backgroundImage = 'none';
        poster.innerHTML = '<i class="fa-solid fa-image text-4xl text-gray-800"></i>';
    }

    document.getElementById('test_ai_result_title').innerHTML = '<span class="text-gray-500">Pulsa "Procesar Ficha" para ver la simulación completa del post-proceso IA.</span>';
}

/*
 * Ejecuta la prueba de normalización de IA sobre la ficha seleccionada.
 */
async function runAITest() {
    const guid = document.getElementById('test_ai_selected_guid').value;
    if (!guid) return showToast("Selecciona un torrent.", false);

    const provider = document.getElementById('ping_provider').value;
    const model = document.getElementById('ping_model').value;
    const key = document.getElementById('ping_key').value;
    const url = document.getElementById('ping_url').value;

    const btn = document.getElementById('btn_run_test');
    const resultBox = document.getElementById('test_ai_result_title');
    const progressContainer = document.getElementById('test_ai_progress_container');
    const progressBar = document.getElementById('test_ai_progress_bar');
    const progressText = document.getElementById('test_ai_percentage');

    btn.disabled = true;
    btn.classList.add('opacity-50');
    progressContainer.classList.remove('hidden');
    resultBox.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin text-yellow-500 mr-2"></i> Analizando metadatos y comunicando con IA...';
    progressBar.style.width = "30%";
    progressText.innerText = "30%";

    try {
        const res = await fetch('/api/ui/ai/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                guid: guid,
                config: { provider, model_name: model, api_key: key, base_url: url, is_enabled: true, is_automated: false }
            })
        });
        progressBar.style.width = "80%";
        progressText.innerText = "80%";
        const data = await res.json();
        progressBar.style.width = "100%";
        progressText.innerText = "100%";
        setTimeout(() => { progressContainer.classList.add('hidden'); progressBar.style.width = "0%"; }, 1000);

        if (data.success) {
            resultBox.innerHTML = renderAITestSuccess(data);
        } else {
            const rawBlock = data.raw ? `<pre class="mt-2 text-xs text-red-300 whitespace-pre-wrap break-words font-mono">${escapeHtml(prettyJson(data.raw))}</pre>` : '';
            resultBox.innerHTML = `<span class="text-red-500 font-bold">Error:</span> <span class="text-red-400">${escapeHtml(data.error || 'Error desconocido')}</span>${rawBlock}`;
            if ((data.error || '').toLowerCase().includes('motor de ia está desactivado')) {
                showToast('Activa el switch del motor de IA en Configuración para procesar fichas.', false);
            }
            if ((data.error || '').toLowerCase().includes('alta demanda')) {
                showToast('Proveedor IA con alta demanda. Reintenta en unos minutos.', false);
            }
            if ((data.error || '').toLowerCase().includes('modelo ia no disponible')) {
                showToast('El modelo no está disponible para este proveedor. Revisa modelo/API.', false);
            }
        }
    } catch (e) {
        progressContainer.classList.add('hidden');
        resultBox.innerHTML = `<span class="text-red-500 font-bold">Error de Red. Revisa si el LLM local está encendido.</span>`;
    } finally {
        btn.disabled = false;
        btn.classList.remove('opacity-50');
    }
}

/*
 * BLOQUE INICIALIZACION DEL LABORATORIO DE IA
 */

/*
 * Inicializa el listado de torrents cuando la vista del laboratorio está cargada.
 */
document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById('test_ai_dropdown_list')) {
        populateAITestDropdown();
    }
});
