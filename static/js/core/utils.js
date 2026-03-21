// ==========================================
// CONSTANTES Y CONFIGURACIÓN
// ==========================================
const APP_VERSION = "0.3.1"; 
const GITHUB_REPO = "Kaizy48/KITSUNARR"; 


// ==========================================
// UTILIDADES Y HELPERS DE INTERFAZ
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
 * Muestra una notificación emergente (Toast) temporal en la pantalla.
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
// CONTROL DE VERSIÓN Y ACTUALIZACIONES
// ==========================================

document.addEventListener("DOMContentLoaded", () => {
    checkAppVersion();
});

/**
 * Comprueba si existe una nueva versión de la aplicación en GitHub 
 * comparando el tag del último release con la versión actual local.
 */
async function checkAppVersion() {
    const storedVersion = localStorage.getItem('kitsunarr_version');

    if (storedVersion !== APP_VERSION) {
        const modal = document.getElementById('versionModal');
        const content = document.getElementById('versionModalContent');
        const versionText = document.getElementById('modal_version_text');
        
        if(modal && content && versionText) {
            versionText.innerText = `Beta ${APP_VERSION}`; 
            
            modal.classList.remove('hidden');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                content.classList.remove('scale-95');
            }, 10);
        }
        return;
    }

    try {
        const res = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases/latest`);
        if (res.ok) {
            const data = await res.json();
            const latestTag = data.tag_name;
            const cleanLatest = latestTag.replace('v', '').replace('V', '');
            
            if (cleanLatest !== APP_VERSION && isNewerVersion(cleanLatest, APP_VERSION)) {
                showToast(`¡Nueva versión ${latestTag} disponible en GitHub!`, true);
            }
        }
    } catch (e) {
        console.log("Comprobación de actualizaciones en GitHub omitida (Sin conexión).");
    }
}

/**
 * Evalúa numéricamente si la versión obtenida del repositorio remoto 
 * es estrictamente mayor que la versión en ejecución local.
 */
function isNewerVersion(latest, current) {
    return latest.localeCompare(current, undefined, { numeric: true, sensitivity: 'base' }) > 0;
}

/**
 * Oculta el modal de aviso de nueva versión y guarda el estado en el almacenamiento 
 * local para no volver a mostrar el mensaje en la misma versión.
 */
function closeVersionModal() {
    const modal = document.getElementById('versionModal');
    const content = document.getElementById('versionModalContent');
    if(modal && content) {
        modal.classList.add('opacity-0');
        content.classList.add('scale-95');
        setTimeout(() => {
            modal.classList.add('hidden');
            localStorage.setItem('kitsunarr_version', APP_VERSION);
        }, 300);
    }
}