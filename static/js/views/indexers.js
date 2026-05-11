/*
 * BLOQUE MODALES DE INDEXADORES
 */

/*
 * Abre el selector para añadir un nuevo indexador a Kitsunarr.
 */
function openSelectorModal() {
    const m = document.getElementById('selectorModal');
    if (m) m.classList.remove('hidden');
}

/*
 * Cierra los modales de selección y configuración de indexadores.
 */
function closeModals() {
    const selector = document.getElementById('selectorModal');
    const config = document.getElementById('configModal');
    if (selector) selector.classList.add('hidden');
    if (config) config.classList.add('hidden');
}

/*
 * Abre el formulario de configuración del indexador seleccionado o del indexador ya activo.
 */
function openConfigModal(identifier, name) {
    const selectorModal = document.getElementById('selectorModal');
    if (selectorModal) selectorModal.classList.add('hidden');

    if (identifier !== undefined && name !== undefined) {
        window.currentConfiguringIndexer = identifier;
        window.currentConfiguringIndexerName = name;
    }

    document.getElementById('configModal').classList.remove('hidden');
    document.getElementById('configModalTitle').innerText = `Configurar: ${window.currentConfiguringIndexerName || 'Indexador'}`;

    document.getElementById('cookie_val').value = '';
    document.getElementById('user_val').value = '';
    document.getElementById('pass_val').value = '';
}

/*
 * Cambia entre autenticación por cookie y autenticación por usuario y contraseña.
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

/*
 * Guarda las credenciales del indexador y valida la conexión con el tracker.
 */
async function saveIndexer() {
    if (!window.currentConfiguringIndexer) return;

    const authType = document.getElementById('auth_type_input').value;
    const cookieVal = document.getElementById('cookie_val').value.trim() || null;
    const userVal = document.getElementById('user_val').value.trim() || null;
    const passVal = document.getElementById('pass_val').value.trim() || null;

    const identifier = window.currentConfiguringIndexer;
    const name = window.currentConfiguringIndexerName;

    const btn = document.getElementById('saveBtn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Probando...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/ui/indexer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                identifier: identifier,
                name: name,
                auth_type: authType,
                cookie_string: cookieVal,
                username: userVal,
                password: passVal
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

/*
 * Ejecuta un test manual de conexión para el indexador elegido.
 */
async function testIndexer(identifier, name) {
    const iconTd = document.getElementById(`status-icon-${identifier}`);

    iconTd.innerHTML = `<i class="fa-solid fa-arrows-rotate fa-spin text-yellow-500 mr-3 text-lg"></i> ${name}`;
    showToast("Probando conexión con el tracker...");

    try {
        const res = await fetch(`/api/ui/indexer/test/${identifier}`, { method: 'POST' });
        const data = await res.json();

        if (data.success && data.status === 'ok') {
            iconTd.innerHTML = `<i class="fa-solid fa-earth-americas text-green-500 mr-3 text-lg" title="Estado: Online"></i> ${name}`;
            showToast("Conexión exitosa. El indexador funciona.");
        } else {
            iconTd.innerHTML = `<i class="fa-solid fa-earth-americas text-red-500 mr-3 text-lg" title="Estado: Error / No Conecta"></i> ${name}`;
            showToast("La conexión falló o la cookie ha expirado.", false);
        }
    } catch (e) {
        iconTd.innerHTML = `<i class="fa-solid fa-earth-americas text-red-500 mr-3 text-lg" title="Estado: Error Local"></i> ${name}`;
        showToast("Error de red.", false);
    }
}

/*
 * Elimina un indexador configurado en Kitsunarr tras confirmación del usuario.
 */
async function deleteIndexer(identifier) {
    const accepted = await appConfirm(
        '¿Seguro que quieres eliminar este indexador de Kitsunarr?',
        'Confirmar eliminación'
    );
    if (!accepted) return;
    try {
        const res = await fetch(`/api/ui/indexer/${identifier}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast("Indexador eliminado correctamente.");
            setTimeout(() => window.location.reload(), 1000);
        } else {
            showToast("Error al eliminar el indexador.", false);
        }
    } catch (e) {
        showToast("Error de red.", false);
    }
}

/*
 * Activa o desactiva un indexador y refresca su estado en la vista.
 */
async function toggleIndexer(identifier) {
    try {
        const res = await fetch(`/api/ui/indexer/${identifier}/toggle`, { method: 'PATCH' });
        const data = await res.json();

        if (data.success) {
            const estado = data.is_enabled ? 'activado' : 'desactivado';
            if (data.is_enabled && data.status === 'error') {
                showToast(`Indexador ${estado}, pero la prueba de conexión falló.`, false);
            } else {
                showToast(`Indexador ${estado} correctamente.`);
            }
            setTimeout(() => window.location.reload(), 1000);
        } else {
            showToast("No se pudo cambiar el estado del indexador.", false);
        }
    } catch (e) {
        showToast("Error de red al intentar actualizar el estado.", false);
    }
}
