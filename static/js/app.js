/**
 * ==========================================
 * KITSUNARR - FRONTEND APP LOGIC
 * ==========================================
 * Archivo principal que maneja la interactividad de la interfaz,
 * llamadas a la API de FastAPI y manipulación del DOM.
 */

// ==========================================
// 1. VARIABLES GLOBALES Y UTILIDADES
// ==========================================
let logInterval = null;
let aiTestInterval = null;
let searchInterval = null;
let lastSearchQuery = ""; 
let currentCacheData = [];

function getCleanPosterUrl(url) {
    if (!url) return null;
    const encodedUrl = encodeURIComponent(url);
    return `/api/ui/poster?url=${encodedUrl}`;
}

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    
    let bgColor = type === 'error' ? 'bg-red-600' : (type === 'info' ? 'bg-blue-600' : 'bg-green-600');
    let icon = type === 'error' ? 'fa-circle-xmark' : (type === 'info' ? 'fa-circle-info' : 'fa-circle-check');

    toast.className = `flex items-center text-white text-sm font-bold px-5 py-3 rounded shadow-lg transition-all duration-300 transform -translate-y-10 opacity-0 ${bgColor} border border-white/20`;
    toast.innerHTML = `<i class="fa-solid ${icon} mr-3 text-lg"></i> ${message}`;
    
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.remove('-translate-y-10', 'opacity-0');
        toast.classList.add('translate-y-0', 'opacity-100');
    }, 10);

    setTimeout(() => {
        toast.classList.remove('translate-y-0', 'opacity-100');
        toast.classList.add('-translate-y-10', 'opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}


// ==========================================
// 2. CONFIGURACIÓN DE IA (MOTOR PRINCIPAL)
// ==========================================

function handleAIToggleClick(event) {
    if (event.target.checked) {
        event.target.checked = false; 
        document.getElementById('aiWarningModal').classList.remove('hidden');
    } else {
        saveAIConfig();
    }
}

function confirmAIEnable() {
    document.getElementById('aiWarningModal').classList.add('hidden');
    document.getElementById('ai_is_enabled').checked = true;
    saveAIConfig();
}

function cancelAIEnable() {
    document.getElementById('aiWarningModal').classList.add('hidden');
    document.getElementById('ai_is_enabled').checked = false;
}

function toggleAIFields() {
    const provider = document.getElementById("ai_provider").value;
    if (provider === "ollama") {
        document.getElementById("ai_key_container").classList.add("hidden");
        document.getElementById("ai_url_container").classList.remove("hidden");
    } else {
        document.getElementById("ai_key_container").classList.remove("hidden");
        document.getElementById("ai_url_container").classList.add("hidden");
    }
    document.getElementById("ping_provider").value = provider;
    togglePingFields();
}

async function saveAIConfig() {
    const payload = {
        is_enabled: document.getElementById("ai_is_enabled").checked,
        is_automated: document.getElementById("ai_is_automated").checked,
        provider: document.getElementById("ai_provider").value,
        model_name: document.getElementById("ai_model").value,
        api_key: document.getElementById("ai_key").value,
        base_url: document.getElementById("ai_url").value
    };
    const res = await fetch("/api/ui/ai/config", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
    });
    if (res.ok) {
        showToast("Ajustes de IA guardados", "success");
        document.getElementById("ping_model").value = payload.model_name;
        document.getElementById("ping_key").value = payload.api_key;
        document.getElementById("ping_url").value = payload.base_url;
    } else showToast("Error al guardar ajustes", "error");
}


// ==========================================
// 3. PING Y DIAGNÓSTICO IA
// ==========================================

function togglePingFields() {
    const provider = document.getElementById("ping_provider").value;
    if (provider === "ollama") {
        document.getElementById("ping_key_container").classList.add("hidden");
        document.getElementById("ping_url_container").classList.remove("hidden");
    } else {
        document.getElementById("ping_key_container").classList.remove("hidden");
        document.getElementById("ping_url_container").classList.add("hidden");
    }
}

async function runAIPing() {
    const btn = document.getElementById('btn_run_ping');
    const term = document.getElementById('ping_terminal');
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Conectando...';
    term.innerHTML = `<span class="text-purple-400 font-bold">[>] 🧠 Petición enviada:</span> "¿Estás escuchando?"\n<span class="text-gray-500 italic mt-1 block">Esperando respuesta...</span>`;
    
    const pingConfig = {
        is_enabled: true, is_automated: true,
        provider: document.getElementById("ping_provider").value,
        model_name: document.getElementById("ping_model").value,
        api_key: document.getElementById("ping_key").value,
        base_url: document.getElementById("ping_url").value
    };

    try {
        const res = await fetch("/api/ui/ai/ping", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ config: pingConfig })
        });
        const data = await res.json();
        
        if(data.success) {
            term.innerHTML = `<span class="text-purple-400 font-bold">[>] 🧠 Petición enviada:</span> "¿Estás escuchando?"\n<span class="text-green-400 font-bold mt-1 block">[<] ✨ Petición recibida:</span> "${data.result}"`;
            showToast("Conexión IA exitosa", "success");
        } else {
            term.innerHTML = `<span class="text-purple-400 font-bold">[>] 🧠 Petición enviada:</span> "¿Estás escuchando?"\n<span class="text-red-500 font-bold mt-1 block">[!] ❌ Error:</span> ${data.error}`;
            showToast("Fallo en la conexión", "error");
        }
    } catch(e) {
        term.innerHTML += `\n<span class="text-red-500 font-bold mt-1 block">[!] ❌ Error interno.</span>`;
        showToast("Error de red local", "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-wifi text-yellow-500 mr-2"></i> Testear Conexión';
    }
}


// ==========================================
// 4. LOTES MANUALES DE IA
// ==========================================

async function openBatchModal() {
    if(currentCacheData.length === 0) {
        showToast("Sincronizando base de datos...", "info");
        await fetchCacheDataSilently();
    }
    populateBatchList();
    document.getElementById('aiBatchModal').classList.remove('hidden');
}

function closeBatchModal() { document.getElementById('aiBatchModal').classList.add('hidden'); }

function populateBatchList() {
    const container = document.getElementById('batch_list_container');
    const pending = currentCacheData.filter(t => t.ai_status === 'Pendiente');
    container.innerHTML = '';

    if(pending.length === 0) {
        container.innerHTML = '<div class="text-center text-gray-500 p-4 font-bold border border-gray-800 rounded bg-black">No hay torrents pendientes de procesar. <i class="fa-solid fa-check text-green-500 ml-2"></i></div>';
        updateBatchCount();
        return;
    }

    pending.forEach(t => {
        const label = document.createElement('label');
        label.className = "flex items-start p-3 bg-black border border-gray-800 rounded hover:border-gray-600 cursor-pointer transition";
        label.innerHTML = `
            <input type="checkbox" value="${t.guid}" onchange="updateBatchCount()" class="batch-checkbox mt-1 mr-3 w-4 h-4 accent-yellow-500 rounded cursor-pointer">
            <div class="flex-1 overflow-hidden">
                <div class="text-sm font-bold text-white truncate" title="${t.original_title}">${t.original_title}</div>
                <div class="text-xs text-gray-500 truncate mt-1">${t.description || 'Sin descripción'}</div>
            </div>
        `;
        container.appendChild(label);
    });
    updateBatchCount();
}

function updateBatchCount() {
    const checkboxes = document.querySelectorAll('.batch-checkbox:checked');
    const count = checkboxes.length;
    document.getElementById('batch_selected_count').textContent = count;
    
    const btn = document.getElementById('btn_submit_batch');
    if(count > 0 && count <= 5) {
        btn.disabled = false;
        btn.classList.remove('opacity-50', 'cursor-not-allowed');
    } else {
        btn.disabled = true;
        btn.classList.add('opacity-50', 'cursor-not-allowed');
    }

    document.querySelectorAll('.batch-checkbox').forEach(cb => {
        if(!cb.checked && count >= 5) {
            cb.disabled = true;
            cb.parentElement.classList.add('opacity-50');
        } else {
            cb.disabled = false;
            cb.parentElement.classList.remove('opacity-50');
        }
    });
}

async function submitBatchProcess() {
    const checkboxes = document.querySelectorAll('.batch-checkbox:checked');
    const guids = Array.from(checkboxes).map(cb => cb.value);
    if(guids.length === 0 || guids.length > 5) return;

    const btn = document.getElementById('btn_submit_batch');
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Procesando...';
    showToast(`Enviando ${guids.length} torrents a la IA...`, "info");
    
    try {
        const res = await fetch("/api/ui/ai/force_specific", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ guids: guids })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Lote procesado correctamente", "success");
            closeBatchModal();
            await fetchCacheDataSilently();
        } else showToast("El Motor General está desactivado", "error");
    } catch(e) {
        showToast("Error de conexión al servidor", "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-paper-plane mr-2"></i> Enviar a IA';
    }
}


// ==========================================
// 5. LABORATORIO DE IA (PRUEBAS INDIVIDUALES)
// ==========================================

async function populateAITestDropdown() {
    if(currentCacheData.length === 0) await fetchCacheDataSilently();
    const list = document.getElementById('test_ai_dropdown_list');
    list.innerHTML = '';
    currentCacheData.forEach(t => {
        const div = document.createElement('div');
        div.className = "p-3 border-b border-gray-800 hover:bg-gray-800 cursor-pointer text-sm text-gray-300 transition test-ai-option";
        div.innerHTML = `<span class="text-green-500 font-mono text-xs mr-2">[${t.guid}]</span> ${t.original_title}`;
        div.onclick = () => selectAITestTorrent(t.guid, t.original_title);
        list.appendChild(div);
    });
}

function filterAITestDropdown() {
    const input = document.getElementById('test_ai_search_input').value.toLowerCase();
    const options = document.getElementsByClassName('test-ai-option');
    for(let opt of options) opt.style.display = opt.textContent.toLowerCase().includes(input) ? "block" : "none";
    document.getElementById('test_ai_dropdown_list').classList.remove('hidden');
}

function showAITestDropdown() { document.getElementById('test_ai_dropdown_list').classList.remove('hidden'); }
function hideAITestDropdownDelayed() { setTimeout(() => document.getElementById('test_ai_dropdown_list').classList.add('hidden'), 200); }

function selectAITestTorrent(guid, title) {
    document.getElementById('test_ai_selected_guid').value = guid;
    document.getElementById('test_ai_search_input').value = title;
    document.getElementById('test_ai_dropdown_list').classList.add('hidden');
    updateAITestView();
}

function updateAITestView() {
    const guid = document.getElementById('test_ai_selected_guid').value;
    const t = currentCacheData.find(x => x.guid === guid);
    if(!t) return;

    document.getElementById('test_ai_original_title').textContent = t.original_title || '-';
    document.getElementById('test_ai_enriched_title').textContent = t.enriched_title || '-';
    document.getElementById('test_ai_description').textContent = t.description || 'Sin descripción original...';
    
    const posterBox = document.getElementById('test_ai_poster');
    const cleanUrl = getCleanPosterUrl(t.poster_url);
    if(cleanUrl) {
        posterBox.style.backgroundImage = `url('${cleanUrl}')`;
        posterBox.innerHTML = '';
    } else {
        posterBox.style.backgroundImage = 'none';
        posterBox.innerHTML = '<i class="fa-solid fa-image text-3xl text-gray-700"></i>';
    }

    document.getElementById('test_ai_result_title').textContent = 'Esperando orden...';
    document.getElementById('test_ai_result_title').className = "text-sm text-gray-500 font-mono bg-black border border-gray-800 p-4 rounded shadow-inner min-h-[5rem] flex items-center break-words";
    document.getElementById('test_ai_result_box').className = "bg-[#11051c] border border-gray-800 rounded-lg p-4 flex flex-col relative overflow-hidden transition-colors duration-500";
}

async function runAITest() {
    const guid = document.getElementById('test_ai_selected_guid').value;
    if(!guid) return showToast("Selecciona un torrent de la lista", "error");

    const btn = document.getElementById('btn_run_test');
    btn.disabled = true;
    
    const currentConfig = {
        is_enabled: document.getElementById("ai_is_enabled").checked,
        is_automated: document.getElementById("ai_is_automated").checked,
        provider: document.getElementById("ai_provider").value,
        model_name: document.getElementById("ai_model").value,
        api_key: document.getElementById("ai_key").value,
        base_url: document.getElementById("ai_url").value
    };

    const pContainer = document.getElementById('test_ai_progress_container');
    const pBar = document.getElementById('test_ai_progress_bar');
    const pText = document.getElementById('test_ai_status_text');
    const pPercent = document.getElementById('test_ai_percentage');
    
    pContainer.classList.remove('hidden');
    pBar.style.width = '0%';
    pBar.className = "bg-yellow-500 h-2.5 rounded-full transition-all duration-500 ease-out";
    
    let progress = 0;
    clearInterval(aiTestInterval);
    aiTestInterval = setInterval(() => {
        let remaining = 95 - progress;
        progress += remaining * 0.08; 
        pBar.style.width = progress + '%';
        pPercent.textContent = Math.round(progress) + '%';
    }, 400);

    try {
        const res = await fetch("/api/ui/ai/test", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ guid: guid, config: currentConfig })
        });
        const data = await res.json();
        
        clearInterval(aiTestInterval);
        pBar.style.width = '100%';
        pPercent.textContent = '100%';
        
        const resultBox = document.getElementById('test_ai_result_title');
        if(data.success) {
            pBar.classList.replace('bg-yellow-500', 'bg-green-500');
            pText.textContent = "¡Parseo Completado!";
            resultBox.textContent = data.result;
            resultBox.className = "text-lg text-yellow-500 font-bold font-mono bg-black border border-yellow-500/50 p-4 rounded shadow-inner min-h-[5rem] flex items-center break-words";
            document.getElementById('test_ai_result_box').classList.replace('border-gray-800', 'border-yellow-500/50');
            showToast("Prueba IA Exitosa", "success");
        } else {
            pBar.classList.replace('bg-yellow-500', 'bg-red-500');
            pText.textContent = "Error en la petición";
            resultBox.textContent = data.error;
            resultBox.className = "text-sm text-red-400 font-mono bg-black border border-red-900/50 p-4 rounded shadow-inner min-h-[5rem] flex items-center break-words";
            showToast("Error en el modelo o clave API", "error");
        }
        setTimeout(() => { pContainer.classList.add('hidden'); }, 2000);
    } catch (e) {
        clearInterval(aiTestInterval);
        pContainer.classList.add('hidden');
        showToast("Error del servidor local", "error");
    } finally {
        btn.disabled = false;
    }
}


// ==========================================
// 6. GESTIÓN DE CACHÉ (TABLA PRINCIPAL)
// ==========================================

async function fetchCacheDataSilently() {
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        currentCacheData = data.torrents;
    } catch(e) { console.error("Error obteniendo caché:", e); }
}

async function loadCacheTable() {
    const tbody = document.getElementById('cache-tbody');
    if(tbody) {
        tbody.innerHTML = '<tr><td colspan="5" class="p-8 text-center text-gray-500"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando base de datos...</td></tr>';
        await fetchCacheDataSilently();
        renderCacheTable(currentCacheData);
    }
}

function renderCacheTable(torrents) {
    const tbody = document.getElementById('cache-tbody');
    if(!tbody) return;
    tbody.innerHTML = '';
    
    if(torrents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="p-8 text-center text-gray-500">No hay torrents guardados en la base de datos todavía.</td></tr>';
        return;
    }

    torrents.forEach(t => {
        const tr = document.createElement('tr');
        tr.className = "hover:bg-gray-800/30 transition border-b border-gray-800 search-row";
        
        const statusColor = t.ai_status === 'Listo' ? 'text-green-400' : (t.ai_status === 'Manual' ? 'text-blue-400' : (t.ai_status === 'Error' ? 'text-red-500' : 'text-yellow-500'));
        const statusIcon = t.ai_status === 'Pendiente' ? 'fa-hourglass-half' : (t.ai_status === 'Error' ? 'fa-xmark' : 'fa-check');
        
        const finalTitle = t.ai_translated_title ? t.ai_translated_title : t.enriched_title;
        const desc = t.description ? t.description : 'Sin descripción disponible para este torrent.';
        
        tr.innerHTML = `
            <td class="p-3 text-xs text-gray-500 text-center font-mono">${t.guid}</td>
            <td class="p-3">
                <div class="text-sm font-bold text-white mb-1">${t.original_title}</div>
                <div class="text-xs text-gray-500 line-clamp-3 max-w-lg cursor-help" title="${desc}">${desc}</div>
            </td>
            <td class="p-3 text-sm text-yellow-100 font-mono">${finalTitle}</td>
            <td class="p-3 text-center">
                <span class="px-2 py-1 rounded text-xs font-bold bg-black border border-gray-700 ${statusColor} uppercase">
                    <i class="fa-solid ${statusIcon} mr-1"></i> ${t.ai_status}
                </span>
            </td>
            <td class="p-3 text-right space-x-3 whitespace-nowrap">
                <button onclick="openInfoCache('${t.guid}')" class="text-blue-400 hover:text-white transition" title="Ver Datos Técnicos"><i class="fa-solid fa-circle-info"></i></button>
                <button onclick="openEditCache('${t.guid}')" class="text-yellow-500 hover:text-white transition" title="Editar Título"><i class="fa-solid fa-pen"></i></button>
                <button onclick="deleteCacheEntry('${t.guid}')" class="text-red-500 hover:text-red-400 transition" title="Borrar"><i class="fa-solid fa-trash"></i></button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function filterCacheTable() {
    const input = document.getElementById("cacheSearch").value.toLowerCase();
    const rows = document.getElementsByClassName("search-row");
    for (let i = 0; i < rows.length; i++) {
        let text = rows[i].textContent.toLowerCase();
        rows[i].style.display = text.includes(input) ? "" : "none";
    }
}


// ==========================================
// 7. MODALES DE INFORMACIÓN Y EDICIÓN (CACHÉ Y BÚSQUEDA)
// ==========================================

function openInfoCache(guid) {
    const t = currentCacheData.find(x => x.guid === guid);
    if(!t) return;
    
    document.getElementById('info_original_title').textContent = t.original_title || '-';
    document.getElementById('info_enriched_title').textContent = t.enriched_title || '-';
    document.getElementById('info_ai_title').textContent = t.ai_translated_title || 'No procesado (Fallback a Enriquecido)';
    document.getElementById('info_guid').textContent = t.guid;
    document.getElementById('info_desc').textContent = t.description || 'Sin descripción disponible.';
    
    let sizeText = "0 MB";
    if (t.size_bytes > 1024**3) sizeText = (t.size_bytes / 1024**3).toFixed(2) + " GB";
    else if (t.size_bytes > 1024**2) sizeText = (t.size_bytes / 1024**2).toFixed(2) + " MB";
    document.getElementById('info_size').textContent = sizeText;

    const statusColors = { 'Pendiente': 'text-yellow-500', 'Listo': 'text-green-400', 'Manual': 'text-blue-400', 'Error': 'text-red-500' };
    document.getElementById('info_status').innerHTML = `<span class="${statusColors[t.ai_status] || 'text-white'} font-bold uppercase">${t.ai_status}</span>`;

    let freeleechHtml = '<span class="text-gray-400">No</span>';
    if (t.freeleech_until) {
        const expireDate = new Date(t.freeleech_until);
        if (expireDate > new Date()) {
            freeleechHtml = `<span class="text-green-500 font-bold"><i class="fa-solid fa-gift"></i> Sí (hasta el ${expireDate.toLocaleString()})</span>`;
        } else {
            freeleechHtml = `<span class="text-red-400">Expirado (${expireDate.toLocaleString()})</span>`;
        }
    }
    
    const freeleechDiv = document.getElementById('info_freeleech');
    if (freeleechDiv) freeleechDiv.innerHTML = freeleechHtml;

    const posterDiv = document.getElementById('info_poster');
    const cleanUrl = getCleanPosterUrl(t.poster_url);
    if (cleanUrl) {
        posterDiv.style.backgroundImage = `url('${cleanUrl}')`;
        posterDiv.innerHTML = '';
    } else {
        posterDiv.style.backgroundImage = 'none';
        posterDiv.innerHTML = '<i class="fa-solid fa-image text-5xl text-gray-700"></i><p class="text-gray-500 text-xs mt-2 absolute bottom-4">Sin Imagen</p>';
    }

    document.getElementById('infoCacheModal').classList.remove('hidden');
}
function closeInfoModal() { document.getElementById('infoCacheModal').classList.add('hidden'); }

function openEditCache(guid) {
    const t = currentCacheData.find(x => x.guid === guid);
    if(!t) return;
    
    document.getElementById('edit_cache_guid').value = guid;
    document.getElementById('edit_original_title').textContent = t.original_title || '-';
    document.getElementById('edit_enriched_title').textContent = t.enriched_title || '-';
    document.getElementById('edit_cache_title').value = t.ai_translated_title || t.enriched_title || '';
    document.getElementById('edit_cache_description').value = t.description || '';
    
    document.getElementById('edit_id_size').textContent = `${t.guid}  |  ${(t.size_bytes / 1024**2).toFixed(0)} MB`;

    const posterDiv = document.getElementById('edit_poster');
    const cleanUrl = getCleanPosterUrl(t.poster_url);
    if (cleanUrl) {
        posterDiv.style.backgroundImage = `url('${cleanUrl}')`;
        posterDiv.innerHTML = '';
    } else {
        posterDiv.style.backgroundImage = 'none';
        posterDiv.innerHTML = '<i class="fa-solid fa-image text-5xl text-gray-700"></i>';
    }

    document.getElementById('editCacheModal').classList.remove('hidden');
}
function closeCacheModal() { document.getElementById('editCacheModal').classList.add('hidden'); }

async function saveCacheEdit() {
    const guid = document.getElementById('edit_cache_guid').value;
    const newTitle = document.getElementById('edit_cache_title').value;
    const newDesc = document.getElementById('edit_cache_description').value;
    
    const res = await fetch(`/api/ui/cache/${guid}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ ai_translated_title: newTitle, description: newDesc })
    });
    
    if(res.ok) {
        closeCacheModal();
        showToast("Ficha actualizada manualmente", "success");
        loadCacheTable();
    } else showToast("Error al guardar la edición", "error");
}

async function deleteCacheEntry(guid) {
    if(confirm("¿Seguro que quieres borrar este torrent de la caché?")) {
        const res = await fetch(`/api/ui/cache/${guid}`, { method: 'DELETE' });
        if(res.ok) { showToast("Entrada borrada", "success"); loadCacheTable(); }
    }
}


// ==========================================
// 8. IMPORTACIÓN Y EXPORTACIÓN
// ==========================================

function exportCache() {
    window.location.href = "/api/ui/cache/export";
    showToast("Exportando base de datos...", "info");
}

async function handleImportCache(event) {
    const file = event.target.files[0];
    if(!file) return;
    
    const formData = new FormData();
    formData.append("file", file);
    document.getElementById('cache-tbody').innerHTML = '<tr><td colspan="5" class="p-8 text-center text-yellow-500">Importando y fusionando base de datos...</td></tr>';
    
    try {
        const res = await fetch('/api/ui/cache/import', { method: 'POST', body: formData });
        const data = await res.json();
        if(data.success) { showToast(`Importación exitosa: ${data.count} añadidos`, "success"); loadCacheTable(); }
        else { showToast("Error en formato de archivo", "error"); loadCacheTable(); }
    } catch(e) {
        showToast("Error crítico al importar", "error"); loadCacheTable();
    }
    event.target.value = ""; 
}


// ==========================================
// 9. BÚSQUEDA INTERACTIVA (MANUAL)
// ==========================================

async function runInteractiveSearch() {
    const input = document.getElementById('interactiveSearchInput').value.trim();
    if (!input) return showToast("Escribe algo para buscar", "error");

    if (input.toLowerCase() === lastSearchQuery.toLowerCase()) {
        showToast(`Ya estás viendo los resultados de '${input}'`, "info");
        return; 
    }

    const btn = document.getElementById('btn_run_search');
    const tbody = document.getElementById('search-results-tbody');
    const pContainer = document.getElementById('search_progress_container');
    const pBar = document.getElementById('search_progress_bar');
    const pText = document.getElementById('search_status_text');
    const pPercent = document.getElementById('search_percentage');
    
    btn.disabled = true;
    pContainer.classList.remove('hidden');
    pBar.style.width = '0%';
    pBar.classList.replace('bg-green-500', 'bg-yellow-500');
    pBar.classList.replace('bg-red-500', 'bg-yellow-500');
    pText.textContent = "Conectando con el tracker...";
    
    let progress = 0;
    clearInterval(searchInterval);
    searchInterval = setInterval(() => {
        let remaining = 95 - progress;
        progress += remaining * 0.08; 
        pBar.style.width = progress + '%';
        pPercent.textContent = Math.round(progress) + '%';
    }, 400);

    try {
        const res = await fetch(`/api/ui/search?q=${encodeURIComponent(input)}`);
        const data = await res.json();
        
        clearInterval(searchInterval);
        
        if (data.success) {
            lastSearchQuery = input; 
            pBar.style.width = '100%';
            pPercent.textContent = '100%';
            pBar.classList.replace('bg-yellow-500', 'bg-green-500');
            pText.textContent = "¡Resultados obtenidos!";
            
            renderSearchTable(data.results);
            showToast(`Búsqueda completada`, "success");
        } else {
            pBar.style.width = '100%';
            pBar.classList.replace('bg-yellow-500', 'bg-red-500');
            pText.textContent = data.error;
            showToast(data.error, "error");
        }
    } catch (e) {
        clearInterval(searchInterval);
        showToast("Error de red local", "error");
    } finally {
        btn.disabled = false;
        setTimeout(() => { pContainer.classList.add('hidden'); }, 4000);
    }
}

function renderSearchTable(torrents) {
    const tbody = document.getElementById('search-results-tbody');
    if(!tbody) return;
    tbody.innerHTML = '';
    
    if(torrents.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="p-8 text-center text-gray-500">No se encontraron resultados en el tracker.</td></tr>';
        return;
    }

    if (typeof currentCacheData === 'undefined') window.currentCacheData = [];
    torrents.forEach(t => {
        if(!currentCacheData.find(x => x.guid === t.guid)) currentCacheData.push(t);
    });

    torrents.forEach(t => {
        const tr = document.createElement('tr');
        tr.className = "hover:bg-gray-800/30 transition border-b border-gray-800";
        
        const finalTitle = t.ai_translated_title ? t.ai_translated_title : t.enriched_title;
        const statusColor = t.ai_status === 'Listo' ? 'text-green-400' : (t.ai_status === 'Manual' ? 'text-blue-400' : (t.ai_status === 'Error' ? 'text-red-500' : 'text-yellow-500'));
        
        const tvdbIcon = t.tvdb_id 
            ? `<span class="ml-2 text-blue-400" title="TVDB ID: ${t.tvdb_id}"><i class="fa-solid fa-tv"></i></span>` 
            : `<span class="ml-2 text-gray-600" title="Pendiente TVDB"><i class="fa-solid fa-tv"></i></span>`;

        tr.innerHTML = `
            <td class="p-3 text-xs text-gray-500 text-center font-mono">${t.guid}</td>
            <td class="p-3">
                <div class="text-sm font-bold text-white mb-1">${t.original_title}</div>
                <div class="text-xs text-yellow-100 font-mono">${finalTitle}</div>
            </td>
            <td class="p-3 text-center whitespace-nowrap">
                <span class="px-2 py-1 rounded text-[10px] font-bold bg-black border border-gray-700 ${statusColor} uppercase">
                    ${t.ai_status}
                </span>
                ${tvdbIcon}
            </td>
            <td class="p-3 text-right">
                <button onclick="openInfoCache('${t.guid}')" class="text-blue-400 hover:text-white transition" title="Ver Ficha Completa"><i class="fa-solid fa-circle-info text-lg"></i></button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}


// ==========================================
// 10. INDEXADORES, LOGS Y SISTEMA
// ==========================================

async function clearLogs() {
    if(confirm("¿Seguro que quieres vaciar la terminal de eventos?")) {
        const res = await fetch('/api/ui/logs', { method: 'DELETE' });
        if(res.ok) { document.getElementById('log-console').textContent = "Borrando consola..."; fetchLogs(); }
    }
}

async function fetchLogs() {
    const consoleEl = document.getElementById('log-console');
    if(!consoleEl) return;
    try {
        const res = await fetch('/api/ui/logs');
        const data = await res.json();
        const isAtBottom = consoleEl.scrollHeight - consoleEl.scrollTop <= consoleEl.clientHeight + 50;
        consoleEl.textContent = data.logs;
        if (isAtBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
    } catch (e) { consoleEl.textContent = "Error conectando al log."; }
}

function copyApiKey() {
    navigator.clipboard.writeText(document.getElementById('api_key_display').value);
    showToast("¡Clave API copiada!", "success");
}

function openRestartModal() { document.getElementById('restartModal').classList.remove('hidden'); }
function closeRestartModal() { document.getElementById('restartModal').classList.add('hidden'); }

async function executeRegenerate(restart) {
    closeRestartModal();
    const res = await fetch('/api/ui/system/apikey/regenerate', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
        document.getElementById('api_key_display').value = data.new_key;
        if (restart) {
            showToast("Reiniciando servidor...", "info");
            await fetch('/api/ui/system/restart', { method: 'POST' });
        } else showToast("Nueva clave generada.", "success");
    }
}

function openSelectorModal() { document.getElementById("selectorModal").classList.remove("hidden"); }
function openConfigModal() { document.getElementById("selectorModal").classList.add("hidden"); document.getElementById("configModal").classList.remove("hidden"); }
function closeModals() { document.getElementById("selectorModal").classList.add("hidden"); document.getElementById("configModal").classList.add("hidden"); }

async function deleteIndexer(identifier) {
    if(confirm("¿Eliminar Union Fansub?")) {
        const res = await fetch(`/api/ui/indexer/${identifier}`, { method: 'DELETE' });
        if(res.ok) window.location.reload(); 
    }
}

async function saveIndexer() {
  const btn = document.getElementById("saveBtn");
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Probando...';
  const payload = {
    auth_type: document.getElementById("auth_type_input").value,
    cookie_string: document.getElementById("cookie_val").value,
    username: document.getElementById("user_val").value,
    password: document.getElementById("pass_val").value,
  };
  const response = await fetch("/api/ui/indexer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (response.ok) window.location.reload(); 
  else { showToast("Error de configuración", "error"); btn.innerHTML = "Guardar y Probar"; }
}

async function testIndexer(identifier, name) {
    const icon = document.getElementById(`status-icon-${identifier}`);
    icon.innerHTML = `<i class="fa-solid fa-arrows-rotate fa-spin text-accent mr-3"></i> ${name}`;
    try {
        const response = await fetch(`/api/ui/indexer/test/${identifier}`, { method: 'POST' });
        const data = await response.json();
        if (data.status === 'ok') {
            icon.innerHTML = `<i class="fa-solid fa-earth-americas text-green-500 mr-3"></i> ${name}`;
            showToast("Tracker OK", "success");
        } else {
            icon.innerHTML = `<i class="fa-solid fa-earth-americas text-red-500 mr-3"></i> ${name}`;
            showToast("Error de tracker", "error");
        }
    } catch (e) { showToast("Error de red", "error"); }
}