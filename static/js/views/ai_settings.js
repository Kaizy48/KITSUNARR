// ==========================================
// LÓGICA DE LA VISTA: LABORATORIO DE IA
// ==========================================

let labCacheData = [];

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

/**
 * Consulta la API interna para obtener los torrents cacheados y los carga.
 * Muestra TODOS los torrents, pero ordena priorizando Pendientes y Errores.
 */
async function populateAITestDropdown() {
    if(!document.getElementById('test_ai_dropdown_list')) return;
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        labCacheData = data.torrents;
        
        labCacheData.sort((a, b) => {
            const weights = { 'Pendiente': 3, 'Error': 2, 'Manual': 1, 'Listo': 0 };
            const wA = weights[a.ai_status] || 0;
            const wB = weights[b.ai_status] || 0;
            
            if (wB !== wA) return wB - wA;
            return parseInt(b.guid) - parseInt(a.guid);
        });
        
        renderAITestDropdown(labCacheData);
    } catch (e) { console.error("Error cargando caché para IA:", e); }
}

/**
 * Renderiza el DOM del menú desplegable limitando visualmente a 50 resultados
 * para no saturar el DOM del navegador.
 */
function renderAITestDropdown(torrents) {
    const list = document.getElementById('test_ai_dropdown_list');
    if(!list) return;
    list.innerHTML = '';
    
    torrents.slice(0, 50).forEach(t => {
        const div = document.createElement('div');
        div.className = "p-3 border-b border-gray-800 hover:bg-gray-800 cursor-pointer text-sm text-gray-300 flex justify-between items-center transition w-full";
        
        let statusBadge = '';
        if(t.ai_status === 'Listo') {
            statusBadge = '<span class="text-xs bg-green-900/50 text-green-400 px-2 py-0.5 rounded border border-green-700">Listo</span>';
        } else if(t.ai_status === 'Pendiente') {
            statusBadge = '<span class="text-xs bg-yellow-900/50 text-yellow-400 px-2 py-0.5 rounded border border-yellow-700">Pendiente</span>';
        } else if(t.ai_status === 'Error') {
            statusBadge = '<span class="text-xs bg-red-900/50 text-red-400 px-2 py-0.5 rounded border border-red-700">Error</span>';
        } else if(t.ai_status === 'Manual') {
            statusBadge = '<span class="text-xs bg-blue-900/50 text-blue-400 px-2 py-0.5 rounded border border-blue-700">Manual</span>';
        }
        
        div.innerHTML = `<span class="truncate flex-1 min-w-0 pr-4">${t.enriched_title}</span> <div class="shrink-0">${statusBadge}</div>`;
        div.onclick = () => selectAITestTorrent(t);
        list.appendChild(div);
    });
}

/**
 * Filtra los elementos de TODA la base de datos (labCacheData) en tiempo real
 * según el texto introducido en el input, y pasa los resultados al renderizador.
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

function showAITestDropdown() { document.getElementById('test_ai_dropdown_list').classList.remove('hidden'); }
function hideAITestDropdownDelayed() { setTimeout(() => { const l = document.getElementById('test_ai_dropdown_list'); if(l) l.classList.add('hidden'); }, 200); }

/**
 * Selecciona un torrent específico de la lista y rellena el panel del Laboratorio.
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
 * Ejecuta una prueba de normalización completa usando la IA.
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

// ==========================================
// MODALES DEL PROMPT PERSONALIZADO
// ==========================================

function openPromptWarning() { document.getElementById('aiPromptWarningModal').classList.remove('hidden'); }
function closePromptWarning() { document.getElementById('aiPromptWarningModal').classList.add('hidden'); }
function openPromptEditor() { closePromptWarning(); document.getElementById('aiPromptEditorModal').classList.remove('hidden'); }
function closePromptEditor() { document.getElementById('aiPromptEditorModal').classList.add('hidden'); }

async function saveCustomPrompt() {
    const promptText = document.getElementById('custom_prompt_textarea').value;
    try {
        const res = await fetch('/api/ui/ai/prompt', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
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

async function resetPrompt() {
    if(!confirm("¿Borrar tu prompt y volver a usar el de fábrica?")) return;
    document.getElementById('custom_prompt_textarea').value = "";
    await saveCustomPrompt();
}

/**
 * ARRANQUE INICIAL
 */
document.addEventListener("DOMContentLoaded", () => {
    if(document.getElementById('test_ai_dropdown_list')) {
        populateAITestDropdown();
    }
});