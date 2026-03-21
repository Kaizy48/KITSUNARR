// ==========================================
// LÓGICA DE LA VISTA: CONFIGURACIÓN
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
                if(typeof togglePingFields === 'function') togglePingFields();
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
 * Pide al backend limpiar las variables en memoria RAM del motor de IA.
 * Sirve para reiniciar el contador diario de cuota y despertar al worker.
 */
async function resetAIQuota() {
    if(!confirm("¿Seguro que quieres reiniciar a 0 el contador diario de la IA y despertar el worker automático?")) return;
    
    try {
        const res = await fetch('/api/ui/ai/reset_quota', { method: 'POST' });
        const data = await res.json();
        if(data.success) {
            showToast("Contador de IA reiniciado y worker despertado exitosamente.");
        } else {
            showToast("Error al reiniciar la cuota.", false);
        }
    } catch(e) {
        showToast("Error de red al conectar con el servidor interno.", false);
    }
}

// ==========================================
// MODALES DEL PROMPT PERSONALIZADO
// ==========================================

function openPromptWarning() {
    document.getElementById('aiPromptWarningModal').classList.remove('hidden');
}

function closePromptWarning() {
    document.getElementById('aiPromptWarningModal').classList.add('hidden');
}

function openPromptEditor() {
    closePromptWarning();
    document.getElementById('aiPromptEditorModal').classList.remove('hidden');
}

function closePromptEditor() {
    document.getElementById('aiPromptEditorModal').classList.add('hidden');
}

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

async function resetPrompt() {
    if(!confirm("¿Borrar tu prompt y volver a usar el de fábrica?")) return;
    document.getElementById('custom_prompt_textarea').value = "";
    await saveCustomPrompt();
}