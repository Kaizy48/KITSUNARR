// ==========================================
// LÓGICA DE LA VISTA: INDEXADORES (TRACKERS)
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