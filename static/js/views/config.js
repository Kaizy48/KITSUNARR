const AI_PROFILE_PRESETS = {
    ollama: [
        { id: 'ollama-local', label: 'Ollama Local', provider: 'ollama', model: 'llama3.1:8b', rpm: 0, tpm: 0, rpd: 0 }
    ],
    openai: [
        { id: 'openai-free', label: 'OpenAI Gratuito', provider: 'openai', model: 'gpt-4.1-mini', rpm: 10, tpm: 40000, rpd: 250 },
        { id: 'openai-premium', label: 'OpenAI Premium', provider: 'openai', model: 'gpt-4.1', rpm: 60, tpm: 300000, rpd: 5000 }
    ],
    gemini: [
        { id: 'gemini-free', label: 'Gemini Gratuito', provider: 'gemini', model: 'gemini-2.5-flash', rpm: 15, tpm: 250000, rpd: 500 },
        { id: 'gemini-premium', label: 'Gemini Premium', provider: 'gemini', model: 'gemini-2.5-pro', rpm: 60, tpm: 1000000, rpd: 5000 }
    ]
};

/*
 * BLOQUE PERFILES DE IA
 */

/*
 * Funcion para detectar si el modelo configurado coincide con un perfil de IA conocido.
 */
function getCurrentAiProfilePreset() {
    const provider = document.getElementById('ai_provider')?.value || '';
    const model = (document.getElementById('ai_model')?.value || '').trim().toLowerCase();
    const list = AI_PROFILE_PRESETS[provider] || [];
    return list.find(p => p.model.toLowerCase() === model) || null;
}
/*
 * Funcion para aplicar limites y modelo recomendados desde el perfil de IA elegido.
 */
function applySelectedAiProfile(overrideModel = true) {
    const providerEl = document.getElementById('ai_provider');
    const profileEl = document.getElementById('ai_profile');
    const modelEl = document.getElementById('ai_model');
    const rpmEl = document.getElementById('ai_rpm');
    const tpmEl = document.getElementById('ai_tpm');
    const rpdEl = document.getElementById('ai_rpd');
    if (!providerEl || !profileEl || !modelEl || !rpmEl || !tpmEl || !rpdEl) return;

    const provider = providerEl.value;
    const selected = profileEl.value;
    const preset = (AI_PROFILE_PRESETS[provider] || []).find(p => p.id === selected);

    if (preset) {
        if (overrideModel) modelEl.value = preset.model;
        rpmEl.value = preset.rpm;
        tpmEl.value = preset.tpm;
        rpdEl.value = preset.rpd;
        rpmEl.readOnly = true;
        tpmEl.readOnly = true;
        rpdEl.readOnly = true;
    } else {
        rpmEl.readOnly = false;
        tpmEl.readOnly = false;
        rpdEl.readOnly = false;
    }
}
/*
 * Funcion para pintar los perfiles de IA disponibles segun el proveedor seleccionado.
 */
function renderAiProfiles() {
    const providerEl = document.getElementById('ai_provider');
    const profileEl = document.getElementById('ai_profile');
    if (!providerEl || !profileEl) return;

    const provider = providerEl.value;
    const options = AI_PROFILE_PRESETS[provider] || [];
    profileEl.innerHTML = '';

    options.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.innerText = p.label;
        profileEl.appendChild(opt);
    });

    const customOpt = document.createElement('option');
    customOpt.value = 'custom';
    customOpt.innerText = 'Custom (manual)';
    profileEl.appendChild(customOpt);

    const matched = getCurrentAiProfilePreset();
    profileEl.value = matched ? matched.id : 'custom';
    applySelectedAiProfile(false);
}

/*
 * BLOQUE CLAVE API Y REINICIO
 */

/*
 * Funcion para copiar la clave Torznab de KITSUNARR al portapapeles.
 */
function copyApiKey() {
    const input = document.getElementById('api_key_display');
    if (!input) return;
    input.select();
    document.execCommand('copy');
    showToast("Clave API copiada al portapapeles");
}
/*
 * Funcion para mostrar el aviso de reinicio del servidor.
 */
function openRestartModal() {
    const m = document.getElementById('restartModal');
    if (m) m.classList.remove('hidden');
}
/*
 * Funcion para regenerar la clave API de KITSUNARR y sincronizarla con aplicaciones externas.
 */
async function executeRegenerate(restart) {
    try {
        const res = await fetch('/api/ui/system/apikey/regenerate', { method: 'POST' });
        const data = await res.json();

        if (data.success) {
            let msg = "Clave generada correctamente.";
            if (data.sonarr_synced) msg += " Sonarr auto-actualizado.";
            if (data.radarr_synced) msg += " Radarr auto-actualizado.";

            showToast(msg);

            if (restart) {
                setTimeout(() => showToast("Reiniciando el servidor..."), 1000);
                await fetch('/api/ui/system/restart', { method: 'POST' });
                setTimeout(() => window.location.reload(), 3000);
            } else {
                setTimeout(() => window.location.reload(), 2000);
            }
        }
    } catch (e) {
        showToast("Error de red al intentar regenerar la clave.", false);
    }
}

/*
 * BLOQUE CONFIGURACION TVDB
 */

/*
 * Funcion para guardar credenciales y activacion de TheTVDB.
 */
async function saveTVDB() {
    const apiKey = document.getElementById('tvdb_api_key_input').value.trim();
    const toggle = document.getElementById('tvdb_is_enabled');
    const isEnabled = toggle ? toggle.checked : false;
    const btn = document.getElementById('btn_save_tvdb');
    const originalText = btn.innerHTML;

    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i>...';
    btn.disabled = true;
    try {
        const res = await fetch('/api/ui/system/tvdb', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tvdb_api_key: apiKey, tvdb_is_enabled: isEnabled })
        });
        const data = await res.json();
        if (data.success) {
            btn.innerHTML = '<i class="fa-solid fa-check text-green-200 mr-2"></i> Guardado';
            setTimeout(() => { btn.innerHTML = originalText; btn.disabled = false; window.location.reload(); }, 1500);
            showToast("Configuración TVDB guardada.");
        } else {
            showToast("Error: " + data.error, false);
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    } catch (e) {
        showToast("Error de red", false);
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}
/*
 * Funcion para probar la conexion con TheTVDB antes de guardar la configuracion.
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
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tvdb_api_key: apiKey, tvdb_is_enabled: false })
        });
        const data = await res.json();

        msgBox.classList.remove('hidden');
        if (data.success) {
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

/*
 * BLOQUE SWITCHES DE IA
 */

/*
 * Funcion para mostrar advertencias antes de activar el procesamiento con IA.
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
            } catch (e) {
                warningBox.classList.add('hidden');
            }
        }

        if (m) m.classList.remove('hidden');
    } else {
        saveAdvancedSettings();
    }
}
/*
 * Funcion para confirmar la activacion de IA desde el aviso de configuracion.
 */
function confirmAIEnable() {
    const m = document.getElementById('aiWarningModal');
    if (m) m.classList.add('hidden');
    document.getElementById('ai_is_enabled').checked = true;
    saveAdvancedSettings();
}
/*
 * Funcion para cancelar la activacion de IA desde el aviso de configuracion.
 */
function cancelAIEnable() {
    const m = document.getElementById('aiWarningModal');
    if (m) m.classList.add('hidden');
    document.getElementById('ai_is_enabled').checked = false;
}
/*
 * Funcion para guardar switches de IA y modo automatico.
 */
async function saveAdvancedSettings() {
    const isEnabled = document.getElementById('ai_is_enabled').checked;
    const isAutomated = document.getElementById('ai_is_automated').checked;

    try {
        const res = await fetch('/api/ui/system/advanced', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_enabled: isEnabled, is_automated: isAutomated })
        });
        const data = await res.json();
        if (data.success) {
            showToast("Ajustes avanzados de procesamiento guardados.");
        } else {
            showToast(data.error, false);
        }
    } catch (e) {
        showToast("Error de red guardando ajustes.", false);
    }
}

/*
 * BLOQUE CONFIGURACION DEL PROVEEDOR IA
 */

/*
 * Funcion para adaptar los campos visibles al proveedor de IA seleccionado.
 */
function toggleAIFields() {
    const provider = document.getElementById('ai_provider');
    if (!provider) return;

    const keyContainer = document.getElementById('ai_key_container');
    const urlContainer = document.getElementById('ai_url_container');
    const limitsContainer = document.getElementById('ai_limits_container');

    if (provider.value === 'ollama') {
        keyContainer.classList.add('hidden');
        urlContainer.classList.remove('hidden');
        if (limitsContainer) limitsContainer.classList.add('hidden');
    } else {
        keyContainer.classList.remove('hidden');
        urlContainer.classList.add('hidden');
        if (limitsContainer) limitsContainer.classList.remove('hidden');
    }
    renderAiProfiles();
}
/*
 * Funcion para cargar limites, consumo y estado runtime del modelo de IA seleccionado.
 */
async function loadLimitsForSelectedModel(forceRefresh = false) {
    const providerEl = document.getElementById('ai_provider');
    const modelEl = document.getElementById('ai_model');
    if (!providerEl || !modelEl) return;

    const provider = providerEl.value.trim();
    const model = modelEl.value.trim();
    const profile = document.getElementById('ai_profile')?.value || 'custom';
    if (!provider || !model) return;

    try {
        const res = await fetch(`/api/ui/ai/model_limits?provider=${encodeURIComponent(provider)}&model_name=${encodeURIComponent(model)}`);
        const data = await res.json();
        if (!data.success || !data.limits) return;

        const rpmEl = document.getElementById('ai_rpm');
        const tpmEl = document.getElementById('ai_tpm');
        const rpdEl = document.getElementById('ai_rpd');
        if (rpmEl && (profile === 'custom' || forceRefresh)) rpmEl.value = data.limits.rpm_limit;
        if (tpmEl && (profile === 'custom' || forceRefresh)) tpmEl.value = data.limits.tpm_limit;
        if (rpdEl && (profile === 'custom' || forceRefresh)) rpdEl.value = data.limits.rpd_limit;

        const s = data.stats || {};
        const rpmUsed = document.getElementById('ai_stats_rpm_used');
        const tpmUsed = document.getElementById('ai_stats_tpm_used');
        const rpdUsed = document.getElementById('ai_stats_rpd_used');
        const windowEl = document.getElementById('ai_stats_window');
        const dayEl = document.getElementById('ai_stats_day');
        const runtimeStateEl = document.getElementById('ai_model_runtime_state');
        const runtimeUntilEl = document.getElementById('ai_model_runtime_until');
        if (rpmUsed) rpmUsed.innerText = `${s.minute_requests ?? 0} / ${data.limits.rpm_limit}`;
        if (tpmUsed) tpmUsed.innerText = `${s.minute_tokens ?? 0} / ${data.limits.tpm_limit}`;
        if (rpdUsed) rpdUsed.innerText = `${s.daily_count ?? 0} / ${data.limits.rpd_limit}`;
        if (windowEl) windowEl.innerText = s.minute_window_start || 'Sin actividad reciente';
        if (dayEl) dayEl.innerText = s.daily_date || 'Sin actividad';

        const b = data.backoff || { status: 'active' };
        if (runtimeStateEl) {
            if (b.status === 'paused') {
                runtimeStateEl.innerText = 'En pausa';
                runtimeStateEl.className = 'text-yellow-400 font-bold';
            } else {
                runtimeStateEl.innerText = 'Activo';
                runtimeStateEl.className = 'text-green-400 font-bold';
            }
        }
        if (runtimeUntilEl) {
            if (b.status === 'paused' && b.until_iso) {
                const dt = new Date(b.until_iso);
                const hhmm = dt.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
                runtimeUntilEl.innerText = `hasta ${hhmm}`;
            } else {
                runtimeUntilEl.innerText = '';
            }
        }
    } catch (e) {
    }
}
/*
 * Funcion para guardar proveedor, modelo, credenciales y limites de IA.
 */
async function saveAIConfig() {
    const provider = document.getElementById('ai_provider').value;
    const model = document.getElementById('ai_model').value;
    const key = document.getElementById('ai_key').value;
    const url = document.getElementById('ai_url').value;

    const rpm = parseInt(document.getElementById('ai_rpm').value) || 5;
    const tpm = parseInt(document.getElementById('ai_tpm').value) || 250000;
    const rpd = parseInt(document.getElementById('ai_rpd').value) || 20;

    const btn = document.getElementById('btn_save_ai_config');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Guardando...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/ui/ai/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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

        if (data.success) {
            if (document.getElementById('ping_provider')) {
                document.getElementById('ping_provider').value = provider;
                document.getElementById('ping_model').value = model;
                document.getElementById('ping_key').value = key;
                document.getElementById('ping_url').value = url;
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
/*
 * Funcion para reiniciar contadores de cuota del motor de IA.
 */
async function resetAIQuota() {
    const accepted = await appConfirm(
        '¿Seguro que quieres reiniciar a 0 los contadores diarios y ventanas por modelo de IA?',
        'Confirmar reinicio de cuota IA'
    );
    if (!accepted) return;

    try {
        const res = await fetch('/api/ui/ai/reset_quota', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast("Contadores de IA reiniciados correctamente.");
        } else {
            showToast("Error al reiniciar la cuota.", false);
        }
    } catch (e) {
        showToast("Error de red al conectar con el servidor interno.", false);
    }
}

/*
 * BLOQUE INICIALIZACION
 */

/*
 * Funcion de inicializacion de perfiles y limites de IA en la pantalla de configuracion.
 */
document.addEventListener('DOMContentLoaded', () => {
    const providerEl = document.getElementById('ai_provider');
    const modelEl = document.getElementById('ai_model');
    const profileEl = document.getElementById('ai_profile');
    if (providerEl) {
        providerEl.addEventListener('change', async () => {
            toggleAIFields();
            await loadLimitsForSelectedModel();
        });
    }
    if (profileEl) {
        profileEl.addEventListener('change', () => {
            applySelectedAiProfile(true);
            loadLimitsForSelectedModel(true);
        });
    }
    if (modelEl) {
        modelEl.addEventListener('change', loadLimitsForSelectedModel);
        modelEl.addEventListener('blur', loadLimitsForSelectedModel);
    }
    renderAiProfiles();
    loadLimitsForSelectedModel();
});

/*
 * BLOQUE INTEGRACIONES ARR
 */

/*
 * Funcion para autoagregar o sincronizar el indexador KITSUNARR con aplicaciones Arr.
 */
async function syncApp(type) {
    const url = document.getElementById(`${type}_url`).value.trim();
    const key = document.getElementById(`${type}_key`).value.trim();
    const internalUrl = document.getElementById('kitsunarr_internal_url').value.trim();

    if (!url || !key) {
        showToast(`Introduce la URL y API Key de ${type}`, false);
        return;
    }

    if (type === 'sonarr') {
        const accepted = await appConfirm(
            'Kitsunarr esta en fase beta.\n\nAl autoagregar el indexador a Sonarr, revisa con cuidado los campos RSS y Busqueda automatica antes de dejarlos activos. Si una ficha se identifica mal, Sonarr podria descargar algo incorrecto.\n\nLa busqueda interactiva es la opcion mas segura mientras validas tu configuracion.',
            'Aviso beta para Sonarr'
        );
        if (!accepted) return;
    }

    showToast(`Sincronizando con ${type}...`);
    try {
        const res = await fetch(`/api/ui/system/sync/${type}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url, api_key: key, internal_url: internalUrl })
        });
        const data = await res.json();
        if (data.success) {
            const appName = type.charAt(0).toUpperCase() + type.slice(1);
            showToast(data.warning ? `${appName} sincronizado con advertencia: ${data.warning}` : `${appName} sincronizado correctamente.`);
        } else {
            showToast(`Error: ${data.error}`, false);
        }
    } catch (e) {
        showToast("Error de red", false);
    }
}

/*
 * BLOQUE CONFIGURACION QBITTORRENT
 */

/*
 * Funcion para guardar la configuracion de conexion con qBittorrent.
 */
async function saveQbittorrent() {
    const url = document.getElementById('qbittorrent_url').value.trim();
    const user = document.getElementById('qbittorrent_user').value.trim();
    const pass = document.getElementById('qbittorrent_password').value.trim();

    const btn = document.getElementById('btn_save_qb');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Guardando...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/ui/system/qbittorrent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ qbittorrent_url: url, qbittorrent_user: user, qbittorrent_password: pass })
        });
        const data = await res.json();

        if (data.success) {
            showToast("Configuración de qBittorrent guardada.");
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
/*
 * Funcion para probar la conexion con qBittorrent desde configuracion.
 */
async function testQbittorrent() {
    const url = document.getElementById('qbittorrent_url').value.trim();
    const user = document.getElementById('qbittorrent_user').value.trim();
    const pass = document.getElementById('qbittorrent_password').value.trim();

    if (!url || !user || !pass) {
        showToast("Rellena todos los campos para probar la conexión.", false);
        return;
    }

    const btn = document.getElementById('btn_test_qb');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin text-blue-400"></i>';
    btn.disabled = true;

    try {
        const res = await fetch('/api/ui/system/qbittorrent/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ qbittorrent_url: url, qbittorrent_user: user, qbittorrent_password: pass })
        });
        const data = await res.json();

        if (data.success) {
            showToast("¡Conexión exitosa con qBittorrent!");
        } else {
            showToast(`Error: ${data.error}`, false);
        }
    } catch (e) {
        showToast("Error interno de red", false);
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

