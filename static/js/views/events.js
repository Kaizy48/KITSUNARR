/*
 * BLOQUE CONSOLA DE EVENTOS
 */

/*
 * Carga los eventos recientes del log de Kitsunarr y mantiene el scroll al final si el usuario ya estaba abajo.
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

/*
 * Vacía el log principal de Kitsunarr desde la consola de eventos.
 */
async function clearLogs() {
    const accepted = await appConfirm(
        '¿Estás seguro de que quieres vaciar todo el registro de eventos?',
        'Confirmar limpieza de logs'
    );
    if (!accepted) return;
    
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

/*
 * BLOQUE INICIALIZACION DE EVENTOS
 */

/*
 * Inicia la carga automática de eventos cuando la vista está presente.
 */
document.addEventListener("DOMContentLoaded", () => {
    const consoleEl = document.getElementById('log-console');
    if (consoleEl) {
        fetchLogs();
        setInterval(fetchLogs, 5000);
    }
});
