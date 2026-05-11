/*
 * BLOQUE CONFIGURACION GLOBAL DE FRONTEND
 */

const APP_VERSION = "0.5.0"; 
const GITHUB_REPO = "Kaizy48/KITSUNARR"; 

const VERSION_SEEN_KEY = 'kitsunarr_seen_version';
const UPDATE_CHECK_DATE_KEY = 'kitsunarr_last_update_check_date';
const UPDATE_NOTIFIED_TAG_KEY = 'kitsunarr_last_notified_update_tag';

/*
 * BLOQUE UTILIDADES GENERICAS
 */

/*
 * Convierte bytes a un texto legible para tamaños de torrents y archivos.
 */
function formatBytes(bytes, decimals = 2) {
    if (!+bytes) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

/*
 * Convierte segundos a un texto corto de tiempo para estados de descarga y espera.
 */
function formatTime(seconds) {
    if (!seconds || seconds <= 0) return '0s';
    const d = Math.floor(seconds / (3600*24));
    const h = Math.floor(seconds % (3600*24) / 3600);
    const m = Math.floor(seconds % 3600 / 60);
    const s = Math.floor(seconds % 60);
    
    const dDisplay = d > 0 ? d + "d " : "";
    const hDisplay = h > 0 ? h + "h " : "";
    const mDisplay = m > 0 ? m + "m " : "";
    const sDisplay = s > 0 ? s + "s" : "";
    return dDisplay + hDisplay + mDisplay + sDisplay;
}

/*
 * Muestra una notificación visual reutilizable en la interfaz de Kitsunarr.
 */
function showToast(message, isSuccess = true) {
    const container = document.getElementById('toast-container');
    if (!container) return alert(message);

    const toast = document.createElement('div');
    const colorClass = isSuccess ? 'bg-green-600' : 'bg-red-600';
    const iconClass = isSuccess ? 'fa-check-circle' : 'fa-triangle-exclamation';

    toast.className = `${colorClass} text-white px-4 py-3 rounded shadow-lg flex items-center transform transition-all duration-300 translate-y-full opacity-0 z-[100] mt-2 border border-white/20 font-bold text-sm`;
    toast.innerHTML = `<i class="fa-solid ${iconClass} mr-2"></i> ${message}`;
    
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.remove('translate-y-full', 'opacity-0');
    }, 10);
    
    setTimeout(() => {
        toast.classList.add('opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/*
 * BLOQUE LAYOUT Y ACCESIBILIDAD DE POSTERS
 */

const POSTER_LAYOUT_CONFIG = {
    compact: { columns: 7, modeScale: 0.98 },
    normal: { columns: 5, modeScale: 1.06 },
    large: { columns: 3, modeScale: 1.18 }
};

let posterLayoutRaf = null;

/*
 * Devuelve el modo actual de tamaño de pósters guardado por el usuario.
 */
function getCurrentPosterSizeMode() {
    const savedSize = localStorage.getItem('kitsunarr_text_size') || 'normal';
    return POSTER_LAYOUT_CONFIG[savedSize] ? savedSize : 'normal';
}

/*
 * Calcula el ancho útil de una cuadrícula descontando su padding horizontal.
 */
function calculateGridInnerWidth(grid) {
    const rect = grid.getBoundingClientRect();
    const styles = window.getComputedStyle(grid);
    const paddingX = parseFloat(styles.paddingLeft || '0') + parseFloat(styles.paddingRight || '0');
    return Math.max(0, rect.width - paddingX);
}

/*
 * Ajusta columnas, separación y escala tipográfica de las cuadrículas de pósters.
 */
function applyDynamicPosterLayout() {
    const mode = getCurrentPosterSizeMode();
    const cfg = POSTER_LAYOUT_CONFIG[mode];
    const htmlEl = document.documentElement;
    const grids = document.querySelectorAll('.responsive-poster-grid');

    htmlEl.setAttribute('data-text-size', mode);
    if (!grids.length) return;

    grids.forEach(grid => {
        const containerWidth = calculateGridInnerWidth(grid);
        if (containerWidth <= 0) return;

        const gap = Math.max(10, Math.min(26, Math.round(containerWidth * 0.012)));
        const totalGap = gap * (cfg.columns - 1);
        const posterWidth = (containerWidth - totalGap) / cfg.columns;
        const widthScale = Math.max(0.95, Math.min(1.55, posterWidth / 180));
        const fontScale = Number((cfg.modeScale * widthScale).toFixed(3));

        grid.style.setProperty('--poster-columns', String(cfg.columns));
        grid.style.setProperty('--poster-grid-gap', `${gap}px`);
        grid.style.setProperty('--poster-font-scale', `${fontScale}`);
    });

}

/*
 * Programa el recalculo de layout de pósters en el siguiente frame del navegador.
 */
function schedulePosterLayoutRefresh() {
    if (posterLayoutRaf !== null) {
        window.cancelAnimationFrame(posterLayoutRaf);
    }

    posterLayoutRaf = window.requestAnimationFrame(() => {
        posterLayoutRaf = null;
        applyDynamicPosterLayout();
    });
}

/*
 * Fuerza una actualización del layout responsive de pósters.
 */
function refreshPosterLayouts() {
    schedulePosterLayoutRefresh();
}

/*
 * Cambia el modo de visualización de pósters y guarda la preferencia en el navegador.
 */
function changeTextSize(size, showFeedback = true) {
    const normalizedSize = POSTER_LAYOUT_CONFIG[size] ? size : 'normal';

    localStorage.setItem('kitsunarr_text_size', normalizedSize);
    document.documentElement.setAttribute('data-text-size', normalizedSize);
    schedulePosterLayoutRefresh();

    if (showFeedback) {
        showToast("Tamaño de interfaz actualizado");
    }
}

/*
 * Inicializa el modo visual de pósters al cargar la aplicación.
 */
function initTextSize() {
    const savedSize = getCurrentPosterSizeMode();
    changeTextSize(savedSize, false);
}

/*
 * Normaliza una versión semántica para comparar releases de Kitsunarr.
 */
function normalizeSemver(version) {
    if (!version) return [0, 0, 0];
    const clean = String(version).trim().replace(/^v/i, '');
    const parts = clean.split('.').map(p => parseInt(p, 10));
    return [parts[0] || 0, parts[1] || 0, parts[2] || 0];
}

/*
 * Comprueba si una versión candidata es superior a la versión actual.
 */
function isVersionGreater(candidate, current) {
    const a = normalizeSemver(candidate);
    const b = normalizeSemver(current);
    for (let i = 0; i < 3; i += 1) {
        if (a[i] > b[i]) return true;
        if (a[i] < b[i]) return false;
    }
    return false;
}

/*
 * Cierra el modal de bienvenida de versión y marca la versión como vista.
 */
function closeVersionModal() {
    const modal = document.getElementById('updateVersionModal');
    if (modal) modal.classList.add('hidden');
    localStorage.setItem(VERSION_SEEN_KEY, APP_VERSION);
}

/*
 * Muestra el modal de versión cuando el usuario aún no ha visto esta release.
 */
function maybeShowVersionWelcomeModal() {
    const seenVersion = localStorage.getItem(VERSION_SEEN_KEY);
    if (seenVersion === APP_VERSION) return;

    const modal = document.getElementById('updateVersionModal');
    const modalVersionText = document.getElementById('modal_version_text');
    if (!modal || !modalVersionText) return;

    modalVersionText.innerText = APP_VERSION;
    modal.classList.remove('hidden');
}

/*
 * Consulta GitHub una vez al día para avisar al usuario si existe una release nueva.
 */
async function checkGithubUpdateNotice() {
    const today = new Date().toISOString().slice(0, 10);
    const lastCheckDate = localStorage.getItem(UPDATE_CHECK_DATE_KEY);
    if (lastCheckDate === today) return;

    localStorage.setItem(UPDATE_CHECK_DATE_KEY, today);

    try {
        const res = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases/latest`, {
            headers: { 'Accept': 'application/vnd.github+json' }
        });
        if (!res.ok) return;

        const release = await res.json();
        const latestTag = (release.tag_name || '').trim();
        const latestUrl = (release.html_url || '').trim();
        if (!latestTag || !latestUrl) return;

        if (!isVersionGreater(latestTag, APP_VERSION)) return;

        const notifiedTag = localStorage.getItem(UPDATE_NOTIFIED_TAG_KEY);
        if (notifiedTag === latestTag) return;

        localStorage.setItem(UPDATE_NOTIFIED_TAG_KEY, latestTag);

        const accepted = await appConfirm(
            `Hay una nueva versión disponible: ${latestTag} (actual: ${APP_VERSION}).\n\n¿Quieres abrir la página de la release en GitHub?`,
            'Actualización disponible'
        );
        if (accepted) {
            window.open(latestUrl, '_blank', 'noopener,noreferrer');
        }
    } catch (e) {
    }
}

/*
 * Inicializa utilidades globales de interfaz cuando el navegador carga Kitsunarr.
 */
document.addEventListener("DOMContentLoaded", () => {
    initTextSize();
    schedulePosterLayoutRefresh();
    window.addEventListener('resize', schedulePosterLayoutRefresh);

    maybeShowVersionWelcomeModal();
    checkGithubUpdateNotice();
});

/*
 * BLOQUE RENDERIZADO COMPARTIDO DE TORRENTS
 */

/*
 * Obtiene el título base del torrent sin prefijo de fansub ni bloques técnicos finales.
 */
function getCleanTorrentTitle(t) {
    if (!t) return "Sin título";

    const sourceTitle = t.original_title || t.enriched_title || t.ai_translated_title;
    if (!sourceTitle) return "Sin título";

    let cleaned = sourceTitle.replace(/^\s*(\[[^\]]+\]\s*)+/, '');

    cleaned = cleaned.replace(/\s*(\[[^\]]+\]\s*)+$/, '');

    cleaned = cleaned.replace(/\s{2,}/g, ' ').trim();
    return cleaned || sourceTitle;
}

/*
 * Genera los iconos de estado de IA y TVDB que acompañan a una ficha torrent.
 */
function generateWorkerIconsHtml(t) {
    let aiIcon = '';
    if (t.ai_status === 'Listo') aiIcon = '<i class="fa-solid fa-circle-check text-green-500" title="IA Lista"></i>';
    else if (t.ai_status === 'Manual') aiIcon = '<i class="fa-solid fa-user-gear text-blue-400" title="IA Editada Manualmente"></i>';
    else if (t.ai_status === 'Error') aiIcon = '<i class="fa-solid fa-circle-xmark text-red-500" title="Error IA"></i>';
    else aiIcon = '<i class="fa-solid fa-clock text-gray-500" title="IA Pendiente"></i>';

    let tvdbIcon = '';
    if (t.tvdb_status === 'Listo') tvdbIcon = '<i class="fa-solid fa-circle-check text-green-500" title="TVDB Validado"></i>';
    else if (t.tvdb_status === 'Manual' || t.tvdb_status === 'Revisión Manual') tvdbIcon = '<i class="fa-solid fa-user-gear text-blue-400" title="TVDB Editado Manualmente / Requiere Revisión"></i>';
    else if (t.tvdb_status === 'Error' || t.tvdb_status === 'No Encontrado') tvdbIcon = '<i class="fa-solid fa-circle-xmark text-red-500" title="Error TVDB / No Encontrado"></i>';
    else tvdbIcon = '<i class="fa-solid fa-clock text-gray-500" title="TVDB Pendiente"></i>';
    
    return `<div class="flex space-x-1.5 text-base z-20">${aiIcon} ${tvdbIcon}</div>`;
}

/*
 * Genera la marca visual del fansub o tracker sobre el póster del torrent.
 */
function generateFansubWatermarkHtml(t) {
    const fansub = t.fansub_name || 'Tracker Desconocido';

    return `
        <div class="absolute bottom-0 left-0 w-full p-2 pt-8 z-20 pointer-events-none">
            <div class="text-[10px] font-bold uppercase tracking-widest drop-shadow-[0_2px_4px_rgba(0,0,0,0.6)]">
                <div class="mb-1">
                    <span class="fansub-badge inline-flex items-center rounded-sm bg-yellow-500/15 border border-yellow-500/50 text-yellow-300 font-black tracking-widest">FANSUB</span>
                </div>
                <div class="fansub-shell inline-block rounded">
                    <span class="fansub-label block max-w-full text-yellow-300 tracking-widest">${fansub}</span>
                </div>
            </div>
        </div>
    `;
}

/*
 * Cierra la sesión del usuario y redirige a la pantalla de login.
 */
async function handleLogout() {
    try {
        const res = await fetch('/api/ui/auth/logout', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            window.location.href = "/login";
        }
    } catch (e) {
        showToast("Error al cerrar la sesión.", false);
    }
}

/*
 * Muestra un modal de confirmación reutilizable y devuelve la decisión del usuario.
 */
function appConfirm(message, title = 'Confirmar accion') {
    return new Promise((resolve) => {
        const existing = document.getElementById('app_confirm_modal_overlay');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'app_confirm_modal_overlay';
        overlay.className = 'fixed inset-0 bg-black/80 flex items-center justify-center z-[120] backdrop-blur-sm p-4';

        overlay.innerHTML = `
            <div class="k-panel w-full max-w-lg overflow-hidden shadow-2xl border border-gray-700">
                <div class="px-6 py-4 border-b border-gray-800 bg-[#120a1b]">
                    <h2 class="text-white font-bold text-lg">${title}</h2>
                </div>
                <div class="px-6 py-5">
                    <p class="text-sm text-gray-300 whitespace-pre-line leading-relaxed">${message}</p>
                </div>
                <div class="px-6 py-4 border-t border-gray-800 bg-black flex justify-end gap-3">
                    <button id="app_confirm_cancel" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded border border-gray-700 font-bold text-sm transition">Cancelar</button>
                    <button id="app_confirm_accept" class="px-4 py-2 bg-yellow-500 hover:bg-yellow-400 text-black rounded border border-yellow-600 font-black text-sm transition">Aceptar</button>
                </div>
            </div>
        `;

        /*
         * Funcion interna para cerrar el modal de confirmacion y devolver la decision del usuario.
         */
        const cleanup = (result) => {
            document.removeEventListener('keydown', onKeydown);
            overlay.remove();
            resolve(result);
        };

        /*
         * Funcion interna para cancelar la confirmacion cuando el usuario pulsa Escape.
         */
        const onKeydown = (e) => {
            if (e.key === 'Escape') cleanup(false);
        };

        overlay.querySelector('#app_confirm_cancel').onclick = () => cleanup(false);
        overlay.querySelector('#app_confirm_accept').onclick = () => cleanup(true);
        overlay.onclick = (e) => {
            if (e.target === overlay) cleanup(false);
        };

        document.addEventListener('keydown', onKeydown);
        document.body.appendChild(overlay);
    });
}
