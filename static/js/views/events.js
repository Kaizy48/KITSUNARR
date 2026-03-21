// ==========================================
// LÓGICA DE LA VISTA: EVENTOS Y LOGS
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
// INICIALIZACIÓN AUTOMÁTICA
// ==========================================

/**
 * Al cargar la página de eventos, hace una primera llamada y configura el bucle de 5s
 */
document.addEventListener("DOMContentLoaded", () => {
    const consoleEl = document.getElementById('log-console');
    if (consoleEl) {
        fetchLogs();
        setInterval(fetchLogs, 5000);
    }
});