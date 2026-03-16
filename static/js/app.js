// ==========================================
// KITSUNARR - LÓGICA PRINCIPAL (APP.JS)
// ==========================================

window.currentCacheData = [];
window.currentSearchData = [];
window.currentActiveTorrent = null;
window.currentLocalCandidates = [];

// ==========================================
// 1. UTILIDADES Y HELPERS
// ==========================================

/**
 * Convierte un tamaño numérico en bytes a una cadena de texto legible 
 * en formatos escalados (KB, MB, GB, etc.).
 */
function formatBytes(bytes, decimals = 2) {
    if (!+bytes) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

/**
 * Muestra una notificación emergente (Toast) temporal en la esquina de la pantalla.
 */
function showToast(message, isSuccess = true) {
    const container = document.getElementById('toast-container');
    if (!container) return alert(message);

    const toast = document.createElement('div');
    const colorClass = isSuccess ? 'bg-green-600' : 'bg-red-600';
    const iconClass = isSuccess ? 'fa-check-circle' : 'fa-triangle-exclamation';

    toast.className = `${colorClass} text-white px-4 py-2 rounded shadow-lg font-bold text-sm transform transition-all duration-300 translate-y-[-20px] opacity-0 mb-2 flex items-center`;
    toast.innerHTML = `<i class="fa-solid ${iconClass} mr-2"></i> ${message}`;
    
    container.appendChild(toast);
    
    setTimeout(() => { toast.classList.remove('translate-y-[-20px]', 'opacity-0'); }, 10);
    setTimeout(() => { 
        toast.classList.add('translate-y-[-20px]', 'opacity-0'); 
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ==========================================
// 2. GESTIÓN DE MODALES
// ==========================================

/**
 * Oculta todos los modales genéricos y de configuración de la interfaz 
 * añadiendo la clase 'hidden' de Tailwind.
 */
function closeModals() {
    const modals = ['selectorModal', 'configModal', 'restartModal', 'aiWarningModal', 'aiBatchModal'];
    modals.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });
}

/**
 * Oculta específicamente el modal de Información Dual (Tracker/TVDB).
 */
function closeInfoModal() {
    const el = document.getElementById('infoCacheModal');
    if (el) el.classList.add('hidden');
}

/**
 * Oculta específicamente el modal de Edición Manual de la caché.
 */
function closeCacheModal() {
    const el = document.getElementById('editCacheModal');
    if (el) el.classList.add('hidden');
}

// ==========================================
// 3. LÓGICA DE LA FICHA DUAL (INFO MODAL)
// ==========================================

/**
 * Alterna la vista del modal de información entre los datos crudos del tracker 
 * y los metadatos enriquecidos por TheTVDB/IA. Cambia estilos, insignias y el póster.
 */
function switchInfoTab(tabName) {
    const viewTracker = document.getElementById('info_view_tracker');
    const viewTvdb = document.getElementById('info_view_tvdb');
    const btnTracker = document.getElementById('btn_tab_tracker');
    const btnTvdb = document.getElementById('btn_tab_tvdb');
    const badge = document.getElementById('info_poster_badge');
    const poster = document.getElementById('info_poster');
    const t = window.currentActiveTorrent;

    if (!viewTracker || !viewTvdb || !t) return;

    if (tabName === 'tracker') {
        viewTracker.classList.remove('hidden'); viewTvdb.classList.add('hidden');
        btnTracker.className = "flex-1 py-2 text-sm font-bold text-black rounded-md shadow-sm bg-yellow-500";
        btnTvdb.className = "flex-1 py-2 text-sm font-bold text-gray-400 hover:text-white rounded-md";
        badge.innerText = "TRACKER"; badge.className = "absolute top-8 left-8 bg-black/80 text-yellow-500 text-xs font-bold px-2 py-1 rounded backdrop-blur-md border border-gray-700 z-10 uppercase tracking-widest shadow-lg";
        poster.style.backgroundImage = t.poster_url ? `url('/api/ui/poster?url=${encodeURIComponent(t.poster_url)}')` : 'none';
    } else {
        viewTracker.classList.add('hidden'); viewTvdb.classList.remove('hidden');
        btnTracker.className = "flex-1 py-2 text-sm font-bold text-gray-400 hover:text-white rounded-md";
        btnTvdb.className = "flex-1 py-2 text-sm font-bold text-black rounded-md shadow-sm bg-blue-500";
        badge.innerText = "THETVDB"; badge.className = "absolute top-8 left-8 bg-black/80 text-blue-400 text-xs font-bold px-2 py-1 rounded backdrop-blur-md border border-blue-700 z-10 uppercase tracking-widest shadow-lg";
        
        let tvdbImg = null;
        if (t.tvdb_status === 'Listo' && t.tvdb_poster_path) {
            tvdbImg = t.tvdb_poster_path;
        } else if(t.tvdb_candidates) {
            try {
                const cands = JSON.parse(t.tvdb_candidates);
                const match = cands.find(c => c.tvdb_id == t.tvdb_id) || cands[0];
                if(match && match.image_url) tvdbImg = match.image_url;
            } catch(e) {}
        }
        poster.style.backgroundImage = tvdbImg ? `url('/api/ui/poster?url=${encodeURIComponent(tvdbImg)}')` : 'none';
    }
}

/**
 * Recibe un objeto torrent completo, inyecta todos sus datos técnicos, estados de IA 
 * y de TheTVDB en el DOM del modal de Ficha Dual, y finalmente lo muestra en pantalla.
 */
function populateAndOpenDualModal(torrentObj) {
    window.currentActiveTorrent = torrentObj;
    if (!torrentObj) return;

    document.getElementById('info_original_title').innerText = torrentObj.original_title || '-';
    document.getElementById('info_enriched_title').innerText = torrentObj.enriched_title || '-';
    document.getElementById('info_guid').innerText = torrentObj.guid || '-';
    document.getElementById('info_size').innerText = formatBytes(torrentObj.size_bytes);
    document.getElementById('info_desc_tracker').innerText = torrentObj.description || 'Sin sinopsis disponible del tracker.';
    
    let flText = '<span class="text-gray-500">No activo</span>';
    if (torrentObj.freeleech_until) {
        const flDate = new Date(torrentObj.freeleech_until);
        const now = new Date();
        if (flDate > now) {
            const diffMs = flDate - now;
            const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
            const diffHrs = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
            flText = `<span class="text-green-400 font-bold"><i class="fa-solid fa-gift mr-1 text-pink-500"></i> Sí (${diffDays}d ${diffHrs}h restantes)</span>`;
        }
    }
    document.getElementById('info_freeleech').innerHTML = flText;

    document.getElementById('info_ai_title').innerText = torrentObj.ai_translated_title || 'Pendiente de procesar...';
    
    let aiStatusHtml = `<span class="text-gray-500">${torrentObj.ai_status}</span>`;
    if (torrentObj.ai_status === 'Listo') aiStatusHtml = `<span class="text-green-500 font-bold"><i class="fa-solid fa-check mr-1"></i> Listo</span>`;
    else if (torrentObj.ai_status === 'Pendiente') aiStatusHtml = `<span class="text-yellow-500 font-bold"><i class="fa-solid fa-clock mr-1"></i> Pendiente</span>`;
    else if (torrentObj.ai_status === 'Manual') aiStatusHtml = `<span class="text-purple-400 font-bold"><i class="fa-solid fa-user-pen mr-1"></i> Editado Manual</span>`;
    else if (torrentObj.ai_status === 'Error') aiStatusHtml = `<span class="text-red-500 font-bold"><i class="fa-solid fa-xmark mr-1"></i> Error</span>`;
    document.getElementById('info_ai_status').innerHTML = aiStatusHtml;

    document.getElementById('info_tvdb_id').innerText = torrentObj.tvdb_id || 'No asignado';
    let tvdbStatusHtml = `<span class="text-gray-500">${torrentObj.tvdb_status}</span>`;
    if (torrentObj.tvdb_status === 'Listo') tvdbStatusHtml = `<span class="text-blue-400 font-bold"><i class="fa-solid fa-check-circle mr-1"></i> Validado</span>`;
    else if (torrentObj.tvdb_status === 'Candidatos') tvdbStatusHtml = `<span class="text-purple-400 font-bold"><i class="fa-solid fa-list-ul mr-1"></i> Candidatos Encontrados</span>`;
    else if (torrentObj.tvdb_status === 'Revisión Manual') tvdbStatusHtml = `<span class="text-orange-400 font-bold"><i class="fa-solid fa-triangle-exclamation mr-1"></i> Requiere Revisión</span>`;
    document.getElementById('info_tvdb_status').innerHTML = tvdbStatusHtml;

    const linkTvdb = document.getElementById('link_tvdb_external');
    if (torrentObj.tvdb_id && torrentObj.tvdb_status === 'Listo') {
        linkTvdb.href = `https://thetvdb.com/dereferrer/series/${torrentObj.tvdb_id}`;
        linkTvdb.classList.remove('pointer-events-none', 'opacity-50');
        linkTvdb.classList.add('hover:text-white');
    } else {
        linkTvdb.href = "#";
        linkTvdb.classList.add('pointer-events-none', 'opacity-50');
    }

    const btnForceAi = document.getElementById('btn_force_ai');
    btnForceAi.onclick = () => forceSingleAIProcess(torrentObj.guid);

    const btnForceTvdb = document.getElementById('btn_force_tvdb');
    btnForceTvdb.onclick = () => forceSingleTVDBProcess(torrentObj.guid);

    const posterContainer = document.getElementById('info_poster');
    const placeholder = document.getElementById('info_poster_placeholder');
    if (torrentObj.poster_url) {
        posterContainer.style.backgroundImage = `url('/api/ui/poster?url=${encodeURIComponent(torrentObj.poster_url)}')`;
        placeholder.classList.add('hidden');
    } else {
        posterContainer.style.backgroundImage = `url('/static/img/Kitsunarr-logo-512x512.png')`;
        placeholder.classList.add('hidden');
    }

    switchInfoTab('tracker');
    document.getElementById('infoCacheModal').classList.remove('hidden');
}


// ==========================================
// 4. CONFIGURACIÓN (SISTEMA, TVDB, SWITCHES IA)
// ==========================================

/**
 * Copia la Clave API generada de Kitsunarr al portapapeles del usuario 
 * para que pueda pegarla en la configuración de Sonarr/Prowlarr.
 */
function copyApiKey() {
    const input = document.getElementById('api_key_display');
    if(!input) return;
    input.select();
    document.execCommand('copy');
    showToast("Clave API copiada al portapapeles");
}

/**
 * Muestra el modal de advertencia que indica que el servidor se va a reiniciar.
 */
function openRestartModal() {
    const m = document.getElementById('restartModal');
    if(m) m.classList.remove('hidden');
}

/**
 * Llama al backend para regenerar una nueva Clave API de Torznab.
 * Opcionalmente, envía una señal de reinicio forzado a Uvicorn si se requiere.
 */
async function executeRegenerate(restart) {
    try {
        const res = await fetch('/api/ui/system/apikey/regenerate', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast("Clave generada. " + (restart ? "Reiniciando..." : "Actualiza la vista."));
            if (restart) {
                await fetch('/api/ui/system/restart', { method: 'POST' });
                setTimeout(() => window.location.reload(), 3000);
            } else {
                window.location.reload();
            }
        }
    } catch (e) {
        showToast("Error de red.", false);
    }
}

/**
 * Guarda las credenciales y el estado (activado/desactivado) de TheTVDB 
 * en la base de datos del sistema.
 */
async function saveTVDB() {
    const apiKey = document.getElementById('tvdb_api_key_input').value.trim();
    const toggle = document.getElementById('tvdb_is_enabled');
    const isEnabled = toggle ? toggle.checked : false;
    const btn = document.getElementById('btn_save_tvdb');
    const originalText = btn.innerHTML;
    
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i>...'; btn.disabled = true;
    try {
        const res = await fetch('/api/ui/system/tvdb', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ tvdb_api_key: apiKey, tvdb_is_enabled: isEnabled })
        });
        const data = await res.json();
        if(data.success) {
            btn.innerHTML = '<i class="fa-solid fa-check text-green-200 mr-2"></i> Guardado';
            setTimeout(() => { btn.innerHTML = originalText; btn.disabled = false; window.location.reload(); }, 1500);
            showToast("Configuración TVDB guardada.");
        }
    } catch (e) { showToast("Error", false); btn.innerHTML = originalText; btn.disabled = false; }
}

/**
 * Realiza una prueba de conexión contra los servidores de la API v4 de TheTVDB 
 * utilizando la clave proporcionada, informando del éxito o del error en la UI.
 */
async function testTVDB() {
    const apiKey = document.getElementById('tvdb_api_key_input').value.trim();
    const btn = document.getElementById('btn_test_tvdb');
    const msgBox = document.getElementById('tvdb_status_msg');
    const originalText = btn.innerHTML;
    
    if (!apiKey) {
        msgBox.classList.remove('hidden');
        msgBox.className = "mt-4 text-sm p-3 rounded bg-red-900/20 border border-red-500 text-red-400";
        msgBox.innerHTML = '<i class="fa-solid fa-triangle-exclamation mr-2"></i> Introduce la API Key antes de testear.';
        return;
    }

    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Probando...';
    btn.disabled = true;
    msgBox.classList.add('hidden');
    
    try {
        const res = await fetch('/api/ui/system/tvdb/test', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ tvdb_api_key: apiKey })
        });
        const data = await res.json();
        
        msgBox.classList.remove('hidden');
        if(data.success) {
            msgBox.className = "mt-4 text-sm p-3 rounded bg-green-900/20 border border-green-500 text-green-400 font-medium";
            msgBox.innerHTML = '<i class="fa-solid fa-circle-check mr-2"></i> <strong>Conexión exitosa.</strong> Token de sesión recibido.';
        } else {
            msgBox.className = "mt-4 text-sm p-3 rounded bg-red-900/20 border border-red-500 text-red-400";
            msgBox.innerHTML = `<i class="fa-solid fa-circle-xmark mr-2"></i> <strong>Fallo en la prueba:</strong> ${data.error}`;
        }
    } catch (e) {
        msgBox.classList.remove('hidden');
        msgBox.className = "mt-4 text-sm p-3 rounded bg-red-900/20 border border-red-500 text-red-400";
        msgBox.innerHTML = '<i class="fa-solid fa-plug-circle-xmark mr-2"></i> <strong>Error interno de red.</strong>';
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

/**
 * Maneja el clic en el interruptor principal de activación de la IA.
 * Si se intenta activar, intercepta la acción para mostrar advertencias sobre TVDB.
 */
async function handleAIToggleClick(event) {
    const checkbox = event.target;
    if (checkbox.checked) {
        checkbox.checked = false; 
        const m = document.getElementById('aiWarningModal');
        const warningBox = document.getElementById('ai_tvdb_warning');
        
        if (warningBox) {
            warningBox.classList.remove('hidden');
            warningBox.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Comprobando estado del sistema...';
            
            try {
                const res = await fetch('/api/ui/system/status');
                const data = await res.json();
                
                if (data.tvdb_is_enabled) {
                    warningBox.className = "text-sm font-bold leading-relaxed border p-3 rounded mb-6 text-left border-green-500/50 bg-green-500/10 text-green-400";
                    warningBox.innerHTML = "<i class='fa-solid fa-circle-check mr-2 text-green-500'></i> <strong>TheTVDB está activado.</strong> La IA cruzará los datos con la base oficial, reduciendo el riesgo de errores en temporadas y nombres.";
                } else {
                    warningBox.className = "text-sm font-bold leading-relaxed border p-3 rounded mb-6 text-left border-yellow-500/50 bg-yellow-500/10 text-yellow-500";
                    warningBox.innerHTML = "<i class='fa-solid fa-triangle-exclamation mr-2 text-yellow-500 text-lg'></i> <strong>TheTVDB NO está configurado.</strong> Si no configuras la API para TVDB, la IA puede sufrir alucinaciones de temporadas o errores al identificar series, y Sonarr podría descartar el torrent (Luego no digas que no te he avisado).";
                }
            } catch(e) {
                warningBox.classList.add('hidden');
            }
        }
        
        if(m) m.classList.remove('hidden');
    } else {
        saveAdvancedSettings(); 
    }
}

/**
 * Callback de confirmación del modal de advertencia de IA.
 * Marca el checkbox como activado y guarda los ajustes.
 */
function confirmAIEnable() {
    const m = document.getElementById('aiWarningModal');
    if(m) m.classList.add('hidden');
    document.getElementById('ai_is_enabled').checked = true;
    saveAdvancedSettings();
}

/**
 * Callback de cancelación del modal de advertencia de IA.
 * Mantiene la IA apagada.
 */
function cancelAIEnable() {
    const m = document.getElementById('aiWarningModal');
    if(m) m.classList.add('hidden');
    document.getElementById('ai_is_enabled').checked = false;
}

/**
 * Envía la configuración de los switches de IA (Activado/Automatizado) al servidor.
 */
async function saveAdvancedSettings() {
    const isEnabled = document.getElementById('ai_is_enabled').checked;
    const isAutomated = document.getElementById('ai_is_automated').checked;

    try {
        const res = await fetch('/api/ui/system/advanced', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ is_enabled: isEnabled, is_automated: isAutomated })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Ajustes avanzados de procesamiento guardados.");
        } else {
            showToast(data.error, false);
        }
    } catch (e) {
        showToast("Error de red guardando ajustes.", false);
    }
}


// ==========================================
// 5. AJUSTES TÉCNICOS Y LABORATORIO IA
// ==========================================

/**
 * Alterna dinámicamente la visualización del campo "Clave API" vs "URL Base"
 * en el formulario de configuración principal según el proveedor de IA elegido.
 */
function toggleAIFields() {
    const provider = document.getElementById('ai_provider');
    if(!provider) return;
    
    const keyContainer = document.getElementById('ai_key_container');
    const urlContainer = document.getElementById('ai_url_container');
    const limitsContainer = document.getElementById('ai_limits_container');
    
    if (provider.value === 'ollama') {
        keyContainer.classList.add('hidden');
        urlContainer.classList.remove('hidden');
        if(limitsContainer) limitsContainer.classList.add('hidden');
    } else {
        keyContainer.classList.remove('hidden');
        urlContainer.classList.add('hidden');
        if(limitsContainer) limitsContainer.classList.remove('hidden');
    }
}

/**
 * Alterna dinámicamente la visualización del campo "Clave API" vs "URL Base"
 * en el formulario de la herramienta de Ping.
 */
function togglePingFields() {
    const provider = document.getElementById('ping_provider');
    if(!provider) return;
    
    const keyContainer = document.getElementById('ping_key_container');
    const urlContainer = document.getElementById('ping_url_container');
    
    if (provider.value === 'ollama') {
        keyContainer.classList.add('hidden');
        urlContainer.classList.remove('hidden');
    } else {
        keyContainer.classList.remove('hidden');
        urlContainer.classList.add('hidden');
    }
}

/**
 * Guarda las credenciales principales del proveedor de IA en la base de datos 
 * y sincroniza automáticamente los campos del panel de diagnóstico (Ping).
 */
async function saveAIConfig() {
    const provider = document.getElementById('ai_provider').value;
    const model = document.getElementById('ai_model').value;
    const key = document.getElementById('ai_key').value;
    const url = document.getElementById('ai_url').value;
    
    const rpm = parseInt(document.getElementById('ai_rpm').value) || 5;
    const tpm = parseInt(document.getElementById('ai_tpm').value) || 250000;
    const rpd = parseInt(document.getElementById('ai_rpd').value) || 20;

    const btn = document.querySelector('button[onclick="saveAIConfig()"]');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Guardando...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/ui/ai/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                provider: provider, 
                model_name: model, 
                api_key: key, 
                base_url: url,
                rpm_limit: rpm,
                tpm_limit: tpm,
                rpd_limit: rpd
            })
        });
        const data = await res.json();
        
        if(data.success) {
            if(document.getElementById('ping_provider')) {
                document.getElementById('ping_provider').value = provider;
                document.getElementById('ping_model').value = model;
                document.getElementById('ping_key').value = key;
                document.getElementById('ping_url').value = url;
                togglePingFields();
            }
            showToast("Conexión de IA y límites guardados.");
            btn.innerHTML = '<i class="fa-solid fa-check mr-2"></i> Guardado';
            setTimeout(() => { btn.innerHTML = originalText; btn.disabled = false; }, 2000);
        } else {
            showToast(data.error, false);
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    } catch (e) {
        showToast("Error de red", false);
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

/**
 * Ejecuta un ping de prueba contra el proveedor de Inteligencia Artificial 
 * mostrando los resultados en líneas separadas para mejorar la lectura.
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
            headers: {'Content-Type': 'application/json'},
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
        }
    } catch (e) {
        term.innerHTML += `<div class="text-red-400 mt-2">> FALLO CRÍTICO: No se pudo alcanzar el servidor interno.</div>`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-wifi text-yellow-500 mr-2"></i> Testear Conexión';
        term.scrollTop = term.scrollHeight;
    }
}

let labCacheData = [];

/**
 * Consulta la API interna para obtener los torrents cacheados y los carga 
 * en el menú desplegable del Laboratorio de IA para realizar pruebas.
 */
async function populateAITestDropdown() {
    if(!document.getElementById('test_ai_dropdown_list')) return;
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        labCacheData = data.torrents;
        renderAITestDropdown(labCacheData);
    } catch (e) { console.error(e); }
}

/**
 * Renderiza el DOM del menú desplegable del Laboratorio IA con los datos proporcionados.
 */
function renderAITestDropdown(torrents) {
    const list = document.getElementById('test_ai_dropdown_list');
    if(!list) return;
    list.innerHTML = '';
    torrents.slice(0, 50).forEach(t => {
        const div = document.createElement('div');
        div.className = "p-3 border-b border-gray-800 hover:bg-gray-800 cursor-pointer text-sm text-gray-300 flex justify-between items-center transition";
        let statusBadge = '';
        if(t.ai_status === 'Listo') statusBadge = '<span class="text-xs bg-green-900/50 text-green-400 px-2 py-0.5 rounded border border-green-700">Listo</span>';
        else if(t.ai_status === 'Pendiente') statusBadge = '<span class="text-xs bg-yellow-900/50 text-yellow-400 px-2 py-0.5 rounded border border-yellow-700">Pend</span>';
        
        div.innerHTML = `<span class="truncate pr-4">${t.enriched_title}</span> ${statusBadge}`;
        div.onclick = () => selectAITestTorrent(t);
        list.appendChild(div);
    });
}

/**
 * Filtra los elementos del menú desplegable en tiempo real según el texto 
 * introducido por el usuario en el input de búsqueda del Laboratorio IA.
 */
function filterAITestDropdown() {
    const q = document.getElementById('test_ai_search_input').value.toLowerCase();
    const filtered = labCacheData.filter(t => t.enriched_title.toLowerCase().includes(q) || t.guid.includes(q));
    renderAITestDropdown(filtered);
}

/**
 * Muestra visualmente la lista de resultados del buscador del Laboratorio IA.
 */
function showAITestDropdown() { document.getElementById('test_ai_dropdown_list').classList.remove('hidden'); }

/**
 * Oculta la lista del buscador con un ligero retraso para permitir 
 * que el evento de clic en un elemento de la lista sea registrado primero.
 */
function hideAITestDropdownDelayed() { setTimeout(() => { const l = document.getElementById('test_ai_dropdown_list'); if(l) l.classList.add('hidden'); }, 200); }

/**
 * Selecciona un torrent específico de la lista y rellena el panel del 
 * Laboratorio de IA con sus metadatos e imágenes preparados para el test.
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
    
    document.getElementById('test_ai_result_title').innerHTML = '<span class="text-gray-500">Pulsa "Procesar Ficha" para ver el resultado...</span>';
}

/**
 * Ejecuta una prueba de normalización completa usando la IA sobre el torrent 
 * seleccionado en el Laboratorio, mostrando el JSON devuelto en la interfaz.
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

    btn.disabled = true; btn.classList.add('opacity-50');
    progressContainer.classList.remove('hidden');
    resultBox.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin text-yellow-500 mr-2"></i> Analizando metadatos y comunicando con IA...';
    progressBar.style.width = "30%"; progressText.innerText = "30%"; 

    try {
        const res = await fetch('/api/ui/ai/test', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                guid: guid,
                config: { provider, model_name: model, api_key: key, base_url: url, is_enabled: true, is_automated: false }
            })
        });
        progressBar.style.width = "80%"; progressText.innerText = "80%";
        const data = await res.json();
        progressBar.style.width = "100%"; progressText.innerText = "100%";
        setTimeout(() => { progressContainer.classList.add('hidden'); progressBar.style.width = "0%"; }, 1000);

        if (data.success) {
            resultBox.innerHTML = `<pre class="text-green-400 font-mono text-xs whitespace-pre-wrap">${data.result}</pre>`;
        } else {
            resultBox.innerHTML = `<span class="text-red-500 font-bold">Error:</span> <span class="text-red-400">${data.error}</span>`;
        }
    } catch (e) {
        progressContainer.classList.add('hidden');
        resultBox.innerHTML = `<span class="text-red-500 font-bold">Error de Red. Revisa si el LLM local está encendido.</span>`;
    } finally { 
        btn.disabled = false; btn.classList.remove('opacity-50'); 
    }
}

/**
 * Abre el modal de procesamiento por lotes. Consulta la base de datos 
 * para mostrar únicamente los torrents que tienen estado "Pendiente" de IA.
 */
async function openBatchModal() {
    const list = document.getElementById('batch_list_container');
    const btn = document.getElementById('btn_submit_batch');
    const count = document.getElementById('batch_selected_count');
    
    list.innerHTML = '<div class="text-center text-gray-500 py-4"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando BD...</div>';
    document.getElementById('aiBatchModal').classList.remove('hidden');
    
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        const pending = data.torrents.filter(t => t.ai_status === 'Pendiente');
        
        list.innerHTML = ''; count.innerText = '0';
        btn.disabled = true; btn.classList.add('opacity-50', 'cursor-not-allowed');
        
        if(pending.length === 0) return list.innerHTML = '<div class="text-center text-gray-500 py-4">No hay torrents pendientes.</div>';
        
        pending.forEach(t => {
            const div = document.createElement('div');
            div.className = "flex items-center p-3 border border-gray-800 rounded bg-black hover:bg-gray-900 transition cursor-pointer";
            div.innerHTML = `
                <input type="checkbox" value="${t.guid}" class="batch-checkbox w-4 h-4 text-yellow-500 bg-gray-800 border-gray-600 rounded cursor-pointer" onchange="updateBatchCount()">
                <div class="ml-3 overflow-hidden">
                    <div class="text-sm text-white font-bold truncate">${t.enriched_title}</div>
                </div>`;
            div.onclick = (e) => { if(e.target.tagName !== 'INPUT') { const cb = div.querySelector('input'); cb.checked = !cb.checked; updateBatchCount(); } };
            list.appendChild(div);
        });
    } catch (e) { list.innerHTML = '<div class="text-center text-red-500">Error de red.</div>'; }
}

/**
 * Actualiza el contador de elementos seleccionados en el modal de procesamiento 
 * por lotes y bloquea el botón si se excede el límite máximo de envíos simultáneos.
 */
function updateBatchCount() {
    const checked = document.querySelectorAll('.batch-checkbox:checked').length;
    const btn = document.getElementById('btn_submit_batch');
    document.getElementById('batch_selected_count').innerText = checked;
    
    if (checked > 0 && checked <= 5) {
        btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed');
    } else {
        btn.disabled = true; btn.classList.add('opacity-50', 'cursor-not-allowed');
        if(checked > 5) showToast("Máximo 5 elementos por lote", false);
    }
}

/**
 * Envía la lista de GUIDs seleccionados en el modal de lotes al backend 
 * para forzar su procesamiento mediante la IA.
 */
async function submitBatchProcess() {
    const guids = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value);
    if(guids.length === 0 || guids.length > 5) return;
    
    closeModals(); showToast(`Enviando ${guids.length} torrents a procesar...`);
    try {
        const res = await fetch('/api/ui/ai/force_specific', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ guids })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Procesamiento enviado. Revisa la consola.");
            if(window.location.pathname.includes('cache')) loadCacheGrid();
        }
    } catch(e) { showToast("Error de red.", false); }
}

/**
 * Cierra el modal de procesamiento por lotes.
 */
function closeBatchModal() { document.getElementById('aiBatchModal').classList.add('hidden'); }


// ==========================================
// 6. GESTIÓN DE LA CACHÉ (GALERÍA VISUAL)
// ==========================================

async function loadCacheGrid() {
    const grid = document.getElementById('cache-grid');
    if(!grid) return;
    
    grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500"><i class="fa-solid fa-spinner fa-spin mr-2"></i> Cargando base de datos...</div>';
    
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        window.currentCacheData = data.torrents;
        renderCacheGrid(window.currentCacheData);
    } catch (e) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-red-500">Error al cargar la caché.</div>';
    }
}

function renderCacheGrid(data) {
    const grid = document.getElementById('cache-grid');
    if(!grid) return;
    grid.innerHTML = '';
    
    if (data.length === 0) {
        grid.innerHTML = '<div class="col-span-full text-center p-8 text-gray-500 flex flex-col items-center"><i class="fa-solid fa-box-open text-4xl mb-3"></i><p>La caché está vacía.</p></div>';
        return;
    }

    data.forEach(t => {
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
            <div class="relative aspect-[2/3] rounded-t-lg overflow-hidden cursor-pointer bg-black" onclick="openInfoModalFromCache('${t.guid}')">
                <img src="${posterUrl}" alt="Poster" class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-110 opacity-90 group-hover:opacity-100">
                <div class="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                    <i class="fa-solid fa-eye text-3xl text-white"></i>
                </div>
                <div class="absolute top-2 right-2 flex flex-col gap-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                    <button onclick="event.stopPropagation(); openEditModal('${t.guid}')" class="bg-black/80 hover:bg-yellow-500 text-white p-2 rounded border border-gray-600 transition" title="Editar Manualmente">
                        <i class="fa-solid fa-pen text-xs"></i>
                    </button>
                    <button onclick="event.stopPropagation(); deleteCacheEntry('${t.guid}')" class="bg-black/80 hover:bg-red-500 text-white p-2 rounded border border-gray-600 transition" title="Eliminar">
                        <i class="fa-solid fa-trash text-xs"></i>
                    </button>
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

function filterCacheGrid() {
    const q = document.getElementById('cacheSearch').value.toLowerCase();
    const filtered = window.currentCacheData.filter(t => 
        t.enriched_title.toLowerCase().includes(q) || 
        t.guid.includes(q) || 
        (t.ai_translated_title && t.ai_translated_title.toLowerCase().includes(q)) ||
        (t.fansub_name && t.fansub_name.toLowerCase().includes(q)) ||
        (t.tvdb_series_name_es && t.tvdb_series_name_es.toLowerCase().includes(q))
    );
    renderCacheGrid(filtered);
}

function openInfoModalFromCache(guid) {
    const t = window.currentCacheData.find(x => x.guid === guid);
    if(t) populateAndOpenDualModal(t);
}

async function openEditModal(guid) {
    const t = window.currentCacheData.find(x => x.guid === guid);
    if(!t) return;
    document.getElementById('edit_cache_guid').value = t.guid;
    document.getElementById('edit_original_title').innerText = t.original_title;
    document.getElementById('edit_enriched_title').innerText = t.enriched_title;
    document.getElementById('edit_cache_title').value = t.ai_translated_title || t.enriched_title;
    document.getElementById('edit_cache_description').value = t.description || '';
    
    document.getElementById('edit_tvdb_search').value = '';
    document.getElementById('edit_tvdb_id').value = '';
    document.getElementById('omnibox_selected_badge').classList.add('hidden');
    
    document.getElementById('edit_id_size').innerText = `${t.guid} | ${formatBytes(t.size_bytes)}`;
    
    const p = document.getElementById('edit_poster');
    p.style.backgroundImage = t.poster_url ? `url('/api/ui/poster?url=${encodeURIComponent(t.poster_url)}')` : 'none';
    
    try {
        const res = await fetch('/api/ui/tvdb/local_candidates');
        const data = await res.json();
        if(data.success) {
            window.currentLocalCandidates = data.results;
            
            if (t.tvdb_id) {
                const existingShow = window.currentLocalCandidates.find(s => s.tvdb_id === t.tvdb_id);
                if (existingShow) {
                    selectOmniboxItem(existingShow.tvdb_id, existingShow.series_name_es);
                } else {
                    selectOmniboxItem(t.tvdb_id, `ID: ${t.tvdb_id}`);
                }
            }
        }
    } catch(e) { console.error("Error cargando candidatos del Omnibox"); }

    document.getElementById('editCacheModal').classList.remove('hidden');
}

function showOmnibox() { 
    filterOmnibox();
    document.getElementById('omnibox_dropdown').classList.remove('hidden'); 
}

function hideOmniboxDelayed() { 
    setTimeout(() => { 
        const d = document.getElementById('omnibox_dropdown');
        if(d) d.classList.add('hidden'); 
    }, 200); 
}

function filterOmnibox() {
    const q = document.getElementById('edit_tvdb_search').value.toLowerCase().trim();
    const dropdown = document.getElementById('omnibox_dropdown');
    dropdown.innerHTML = '';
    
    const filtered = window.currentLocalCandidates.filter(s => {
        const matchEs = s.series_name_es && s.series_name_es.toLowerCase().includes(q);
        const matchAlias = s.aliases && s.aliases.toLowerCase().includes(q);
        const matchId = s.tvdb_id && s.tvdb_id.includes(q);
        return matchEs || matchAlias || matchId;
    });
    
    if (filtered.length > 0) {
        filtered.slice(0, 15).forEach(s => {
            const div = document.createElement('div');
            div.className = "flex items-center p-2 hover:bg-blue-900/50 cursor-pointer border-b border-gray-800 transition";
            
            const badge = s.is_full_record 
                ? '<span class="ml-2 text-[10px] bg-green-900/50 text-green-400 px-1 rounded border border-green-700">Ficha Maestra</span>'
                : '<span class="ml-2 text-[10px] bg-purple-900/50 text-purple-400 px-1 rounded border border-purple-700">Candidato</span>';
                
            div.innerHTML = `
                <div class="flex-1 overflow-hidden">
                    <div class="text-sm text-white font-bold truncate">${s.series_name_es} ${badge}</div>
                    <div class="text-xs text-gray-500 font-mono">ID: ${s.tvdb_id} • Año: ${s.first_aired || '----'}</div>
                </div>
            `;
            div.onclick = () => selectOmniboxItem(s.tvdb_id, s.series_name_es);
            dropdown.appendChild(div);
        });
    }
    
    if (/^\d+$/.test(q)) {
        const manualDiv = document.createElement('div');
        manualDiv.className = "p-3 bg-yellow-900/20 text-yellow-500 text-xs font-bold hover:bg-yellow-900/40 cursor-pointer border-t border-yellow-700/50 transition";
        manualDiv.innerHTML = `<i class="fa-solid fa-cloud-arrow-down mr-2"></i> Forzar ID Manual: ${q}`;
        manualDiv.onclick = () => selectOmniboxItem(q, `ID Forzado: ${q}`);
        dropdown.appendChild(manualDiv);
    }
    
    if(dropdown.innerHTML === '') {
        dropdown.innerHTML = '<div class="p-3 text-xs text-gray-500 italic text-center">No se encontraron coincidencias locales.</div>';
    }
}

function selectOmniboxItem(tvdbId, displayName) {
    document.getElementById('edit_tvdb_id').value = tvdbId;
    document.getElementById('edit_tvdb_search').value = ''; 
    
    document.getElementById('omnibox_selected_text').innerText = displayName;
    document.getElementById('omnibox_selected_badge').classList.remove('hidden');
    document.getElementById('omnibox_dropdown').classList.add('hidden');
}

function clearOmniboxSelection() {
    document.getElementById('edit_tvdb_id').value = '';
    document.getElementById('omnibox_selected_badge').classList.add('hidden');
    document.getElementById('edit_tvdb_search').focus();
}

async function saveCacheEdit() {
    const guid = document.getElementById('edit_cache_guid').value;
    const title = document.getElementById('edit_cache_title').value.trim();
    const desc = document.getElementById('edit_cache_description').value.trim();
    const tvdbId = document.getElementById('edit_tvdb_id').value.trim(); 
    
    if(!title) return showToast("El título no puede estar vacío.", false);
    try {
        const res = await fetch(`/api/ui/cache/${guid}`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ai_translated_title: title, description: desc, tvdb_id: tvdbId })
        });
        const data = await res.json();
        if(data.success) { closeCacheModal(); showToast("Caché actualizada."); loadCacheGrid(); }
    } catch(e) { showToast("Error de red.", false); }
}

async function deleteCacheEntry(guid) {
    if(!confirm("¿Estás seguro de que quieres eliminar este torrent de la caché local?")) return;
    try {
        const res = await fetch(`/api/ui/cache/${guid}`, { method: 'DELETE' });
        const data = await res.json();
        if(data.success) {
            showToast("Torrent eliminado.");
            loadCacheGrid();
        }
    } catch(e) { showToast("Error de red.", false); }
}

async function exportCache() {
    window.location.href = '/api/ui/cache/export';
}

async function handleImportCache(event) {
    const file = event.target.files[0];
    if(!file) return;
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        showToast("Importando base de datos...");
        const res = await fetch('/api/ui/cache/import', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if(data.success) {
            showToast(`Importación exitosa. ${data.count} torrents añadidos.`);
            loadCacheGrid();
        } else {
            showToast("Error: " + data.error, false);
        }
    } catch(e) { showToast("Error de red durante la importación.", false); }
    
    event.target.value = ''; 
}


// ==========================================
// 7. BÚSQUEDA INTERACTIVA
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
        
        // Lógica dual de nombres
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

// ==========================================
// 8. ACCIONES RÁPIDAS (Botón Forzar IA)
// ==========================================

/**
 * Envía la orden a la API para procesar un único torrent saltándose la cola.
 * Utilizado desde el modal de la ficha dual.
 */
async function forceSingleAIProcess(guid) {
    showToast("Enviando petición a la IA...");
    try {
        const res = await fetch('/api/ui/ai/force_specific', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ guids: [guid] })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Procesamiento completado. Actualiza la vista.");
        } else {
            showToast(data.error, false);
        }
    } catch(e) {
        showToast("Error de red forzando IA.", false);
    }
}

async function forceSingleTVDBProcess(guid) {
    showToast("Forzando búsqueda de candidatos en TVDB...");
    try {
        const res = await fetch('/api/ui/tvdb/force_specific', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ guids: [guid] })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Búsqueda en TVDB finalizada. Refresca la vista.");
        } else { showToast(data.error, false); }
    } catch(e) { showToast("Error de red forzando TVDB.", false); }
}

// ==========================================
// 9. GESTIÓN DE INDEXADORES (TRACKERS)
// ==========================================

/**
 * Muestra el modal genérico de selección para agregar un nuevo indexador.
 */
function openSelectorModal() {
    const m = document.getElementById('selectorModal');
    if (m) m.classList.remove('hidden');
}

/**
 * Oculta el modal de selección de indexador y abre el modal de configuración detallada.
 */
function openConfigModal() {
    document.getElementById('selectorModal').classList.add('hidden');
    document.getElementById('configModal').classList.remove('hidden');
}

/**
 * Gestiona el cambio de pestañas entre "Cookie" y "Usuario/Contraseña" dentro 
 * del modal de configuración de un indexador.
 */
function switchTab(tabId) {
    document.getElementById('auth_type_input').value = tabId;
    
    const tabCookie = document.getElementById('tab-cookie');
    const tabLogin = document.getElementById('tab-login');
    const formCookie = document.getElementById('form-cookie');
    const formLogin = document.getElementById('form-login');
    
    if (tabId === 'cookie') {
        tabCookie.className = "flex-1 py-2 text-sm font-bold text-black transition-all duration-300";
        tabCookie.style.backgroundColor = "var(--accent-yellow)";
        tabLogin.className = "flex-1 py-2 text-sm font-medium text-gray-400 hover:text-white transition-all duration-300";
        tabLogin.style.backgroundColor = "transparent";
        formCookie.classList.remove('hidden');
        formLogin.classList.add('hidden');
    } else if (tabId === 'login') {
        tabLogin.className = "flex-1 py-2 text-sm font-bold text-black transition-all duration-300";
        tabLogin.style.backgroundColor = "var(--accent-yellow)";
        tabCookie.className = "flex-1 py-2 text-sm font-medium text-gray-400 hover:text-white transition-all duration-300";
        tabCookie.style.backgroundColor = "transparent";
        formLogin.classList.remove('hidden');
        formCookie.classList.add('hidden');
    }
}

/**
 * Envía las credenciales y el tipo de autenticación del indexador a la base de datos 
 * y efectúa una prueba de conexión en caliente.
 */
async function saveIndexer() {
    const authType = document.getElementById('auth_type_input').value;
    const cookieVal = document.getElementById('cookie_val').value.trim();
    const userVal = document.getElementById('user_val').value.trim();
    const passVal = document.getElementById('pass_val').value.trim();
    
    const btn = document.getElementById('saveBtn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Probando...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/ui/indexer', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                auth_type: authType, cookie_string: cookieVal, username: userVal, password: passVal
            })
        });
        const data = await res.json();
        
        if (data.success && data.status === 'ok') {
            showToast("Indexador guardado y conectado con éxito.");
            closeModals();
            setTimeout(() => window.location.reload(), 1000);
        } else {
            const errorMsg = data.error ? data.error : "Error desconocido al conectar con el tracker.";
            showToast(errorMsg, false);
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    } catch (e) {
        showToast("Error de red al conectar con el servidor.", false);
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

/**
 * Pide a la API que compruebe la conexión del indexador enviando su huella digital. 
 * Modifica el icono de estado de la tabla según el resultado.
 */
async function testIndexer(identifier, name) {
    const iconTd = document.getElementById(`status-icon-${identifier}`);
    
    iconTd.innerHTML = `<i class="fa-solid fa-arrows-rotate fa-spin text-yellow-500 mr-3 text-lg"></i> ${name}`;
    showToast("Probando conexión con el tracker...");

    try {
        const res = await fetch(`/api/ui/indexer/test/${identifier}`, { method: 'POST' });
        const data = await res.json();
        
        if(data.success && data.status === 'ok') {
            iconTd.innerHTML = `<i class="fa-solid fa-earth-americas text-green-500 mr-3 text-lg" title="Estado: Online"></i> ${name}`;
            showToast("Conexión exitosa. El indexador funciona.");
        } else {
            iconTd.innerHTML = `<i class="fa-solid fa-earth-americas text-red-500 mr-3 text-lg" title="Estado: Error / No Conecta"></i> ${name}`;
            showToast("La conexión falló o la cookie ha expirado.", false);
        }
    } catch(e) { 
        iconTd.innerHTML = `<i class="fa-solid fa-earth-americas text-red-500 mr-3 text-lg" title="Estado: Error Local"></i> ${name}`;
        showToast("Error de red.", false); 
    }
}

/**
 * Pide a la API borrar la configuración de un indexador y recarga la página.
 */
async function deleteIndexer(identifier) {
    if(!confirm("¿Seguro que quieres eliminar este indexador de Kitsunarr?")) return;
    try {
        const res = await fetch(`/api/ui/indexer/${identifier}`, { method: 'DELETE' });
        const data = await res.json();
        if(data.success) {
            showToast("Indexador eliminado correctamente.");
            setTimeout(() => window.location.reload(), 1000);
        } else {
            showToast("Error al eliminar el indexador.", false);
        }
    } catch(e) { 
        showToast("Error de red.", false); 
    }
}

// ==========================================
// 10. GESTIÓN DE LOGS Y EVENTOS
// ==========================================

/**
 * Consulta el archivo físico de registro a través de la API y lo carga en la 
 * terminal simulada de la pestaña de estado, controlando el scroll automático.
 */
async function fetchLogs() {
    const consoleEl = document.getElementById('log-console');
    if (!consoleEl) return;

    try {
        const res = await fetch('/api/ui/logs');
        const data = await res.json();
        
        const isScrolledToBottom = consoleEl.scrollHeight - consoleEl.clientHeight <= consoleEl.scrollTop + 50;
        
        consoleEl.innerText = data.logs || "No hay eventos registrados.";
        
        if (isScrolledToBottom) {
            consoleEl.scrollTop = consoleEl.scrollHeight;
        }
    } catch (e) {
        consoleEl.innerText = "Error de red al conectar con el servidor interno de logs.";
    }
}

/**
 * Trunca el archivo físico de registro eliminando todo su contenido.
 */
async function clearLogs() {
    if(!confirm("¿Estás seguro de que quieres vaciar todo el registro de eventos?")) return;
    
    try {
        const res = await fetch('/api/ui/logs', { method: 'DELETE' });
        const data = await res.json();
        if(data.success) {
            showToast("Registro de eventos limpiado.");
            fetchLogs(); 
        } else {
            showToast("Error al limpiar los logs.", false);
        }
    } catch(e) { 
        showToast("Error de red.", false); 
    }
}

// ==========================================
// 11. MODALES DEL PROMPT PERSONALIZADO
// ==========================================

/**
 * Muestra el panel de alerta antes de permitir la edición del prompt crítico de la IA.
 */
function openPromptWarning() {
    document.getElementById('aiPromptWarningModal').classList.remove('hidden');
}

/**
 * Oculta el panel de alerta de modificación de prompt.
 */
function closePromptWarning() {
    document.getElementById('aiPromptWarningModal').classList.add('hidden');
}

/**
 * Abre el editor completo para modificar la plantilla del sistema enviada al LLM.
 */
function openPromptEditor() {
    closePromptWarning();
    document.getElementById('aiPromptEditorModal').classList.remove('hidden');
}

/**
 * Oculta el editor de la plantilla base del LLM.
 */
function closePromptEditor() {
    document.getElementById('aiPromptEditorModal').classList.add('hidden');
}

/**
 * Envía el texto modificado en el área de edición del prompt a la base de datos para su 
 * persistencia y uso como nueva guía de parseo para las operaciones de IA.
 */
async function saveCustomPrompt() {
    const promptText = document.getElementById('custom_prompt_textarea').value;
    
    try {
        const res = await fetch('/api/ui/ai/prompt', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ custom_prompt: promptText })
        });
        const data = await res.json();
        if(data.success) {
            showToast("Prompt personalizado guardado correctamente.");
            closePromptEditor();
        } else {
            showToast("Error al guardar el prompt.", false);
        }
    } catch(e) { showToast("Error de red.", false); }
}

/**
 * Elimina la personalización del prompt, volviendo a emplear las instrucciones maestras 
 * que vienen precompiladas por defecto en el núcleo de Kitsunarr.
 */
async function resetPrompt() {
    if(!confirm("¿Borrar tu prompt y volver a usar el de fábrica?")) return;
    document.getElementById('custom_prompt_textarea').value = "";
    await saveCustomPrompt();
}

// ==========================================
// 12. BIBLIOTECA TVDB (CACHÉ MAESTRA)
// ==========================================

window.currentTvdbData = [];

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
 * Pinta los pósters en el HTML.
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
 * Filtra la galería buscando en el título español, inglés y en la lista de alias.
 */
function filterTvdbGrid() {
    const q = document.getElementById('tvdbSearch').value.toLowerCase();
    const filtered = window.currentTvdbData.filter(show => {
        const matchEs = show.series_name_es && show.series_name_es.toLowerCase().includes(q);
        const matchEn = show.series_name_en && show.series_name_en.toLowerCase().includes(q);
        const matchAlias = show.aliases && show.aliases.toLowerCase().includes(q); // Como los alias son un string JSON, basta con buscar en el texto crudo
        return matchEs || matchEn || matchAlias;
    });
    renderTvdbGrid(filtered);
}

/**
 * Abre el modal con la información detallada, inyecta los datos 
 * y solicita los episodios a la API local.
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
    epContainer.innerHTML = '<div class="text-xs text-gray-500 italic"><i class="fa-solid fa-spinner fa-spin mr-1"></i> Cargando capítulos de la base de datos...</div>';
    
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
 * Renderiza el acordeón interactivo agrupando la lista plana de episodios por temporada.
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
        seasonDiv.className = "border border-gray-800 rounded bg-gray-900/30 overflow-hidden";
        
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
            epRow.className = "px-3 py-2 flex justify-between items-center hover:bg-gray-800/30 transition";
            epRow.innerHTML = `
                <div class="flex items-center space-x-3 overflow-hidden">
                    <span class="text-xs font-mono text-gray-600 bg-black px-1.5 py-0.5 rounded border border-gray-800 w-8 text-center">${ep.episode_number}</span>
                    <span class="text-xs text-gray-300 truncate" title="${ep.name_es}">${ep.name_es}</span>
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
 * Muestra u oculta la lista de episodios de una temporada específica y gira el icono.
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

function closeTvdbModal() {
    document.getElementById('tvdbInfoModal').classList.add('hidden');
}

/**
 * Elimina una ficha de la base de conocimientos.
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

// ==========================================
// 13. BÚSQUEDA INTERACTIVA THETVDB
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