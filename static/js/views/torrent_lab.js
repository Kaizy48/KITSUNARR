/*
 * BLOQUE LABORATORIO DE TORRENTS
 */

let kitsunarrTorrents = [];
let qbittorrentTorrents = [];

/*
 * Carga en paralelo las fichas locales de Kitsunarr y los torrents visibles en qBittorrent.
 */
async function loadData() {
    await Promise.all([fetchKitsunarrOrphans(), fetchQbittorrentList()]);
}

/*
 * Obtiene fichas de Kitsunarr sin info hash para poder vincularlas manualmente.
 */
async function fetchKitsunarrOrphans() {
    try {
        const res = await fetch('/api/ui/cache');
        const data = await res.json();
        kitsunarrTorrents = data.torrents.filter(t => !t.info_hash);
        renderKitDropdown(kitsunarrTorrents);
    } catch (e) {
        showToast("Error cargando fichas de Kitsunarr", false);
    }
}

/*
 * Obtiene la lista de torrents de qBittorrent para emparejamiento manual.
 */
async function fetchQbittorrentList() {
    try {
        const res = await fetch('/api/ui/qbittorrent/list');
        const data = await res.json();
        if (data.success) {
            qbittorrentTorrents = data.torrents;
            renderQbDropdown(qbittorrentTorrents);
        } else {
            showToast(data.error, false);
        }
    } catch (e) {
        showToast("Error conectando con qBittorrent", false);
    }
}

/*
 * BLOQUE DESPLEGABLES DE EMPAREJAMIENTO
 */

/*
 * Renderiza el desplegable de fichas locales pendientes de vincular.
 */
function renderKitDropdown(items) {
    const list = document.getElementById('kit_dropdown_list');
    if (!list) return;
    list.innerHTML = '';
    items.slice(0, 50).forEach(t => {
        const div = document.createElement('div');
        div.className = "p-3 border-b border-gray-800 hover:bg-gray-800 cursor-pointer text-sm text-gray-300 flex justify-between items-center transition w-full";
        div.innerHTML = `<span class="truncate flex-1 min-w-0 pr-4">${t.enriched_title}</span> <span class="text-[10px] text-gray-500 font-mono">${t.guid}</span>`;
        div.onclick = () => selectKitTorrent(t);
        list.appendChild(div);
    });
}

/*
 * Renderiza el desplegable de torrents encontrados en qBittorrent.
 */
function renderQbDropdown(items) {
    const list = document.getElementById('qb_dropdown_list');
    if (!list) return;
    list.innerHTML = '';
    items.slice(0, 50).forEach(t => {
        const div = document.createElement('div');
        div.className = "p-3 border-b border-gray-800 hover:bg-gray-800 cursor-pointer text-sm text-gray-300 flex flex-col transition w-full";
        div.innerHTML = `<span class="truncate font-bold">${t.name}</span><span class="text-[10px] text-blue-500 font-mono truncate">${t.info_hash}</span>`;
        div.onclick = () => selectQbTorrent(t);
        list.appendChild(div);
    });
}

/*
 * Filtra el desplegable de fichas de Kitsunarr por título o GUID.
 */
function filterKitDropdown() {
    const q = document.getElementById('kit_search_input').value.toLowerCase();
    const filtered = kitsunarrTorrents.filter(t => 
        (t.enriched_title && t.enriched_title.toLowerCase().includes(q)) || 
        (t.guid && t.guid.includes(q))
    );
    renderKitDropdown(filtered);
}

/*
 * Filtra el desplegable de qBittorrent por nombre o info hash.
 */
function filterQbDropdown() {
    const q = document.getElementById('qb_search_input').value.toLowerCase();
    const filtered = qbittorrentTorrents.filter(t => 
        (t.name && t.name.toLowerCase().includes(q)) || 
        (t.info_hash && t.info_hash.toLowerCase().includes(q))
    );
    renderQbDropdown(filtered);
}

/*
 * Selecciona una ficha local de Kitsunarr para el emparejamiento.
 */
function selectKitTorrent(t) {
    document.getElementById('kit_search_input').value = t.enriched_title;
    document.getElementById('kit_selected_guid').value = t.guid;
    document.getElementById('kit_guid_display').innerText = t.guid;
    document.getElementById('kit_title_display').innerText = t.enriched_title;
    document.getElementById('kit_fansub_display').innerText = t.fansub_name || 'Desconocido';
    const poster = document.getElementById('kit_poster');
    if (t.poster_url) {
        poster.style.backgroundImage = `url('/api/ui/poster?url=${encodeURIComponent(t.poster_url)}')`;
        poster.innerHTML = '';
    } else {
        poster.style.backgroundImage = "url('/static/img/Kitsunarr-logo-512x512.png')";
        poster.innerHTML = '';
    }
}

/*
 * Selecciona un torrent de qBittorrent para el emparejamiento.
 */
function selectQbTorrent(t) {
    document.getElementById('qb_search_input').value = t.name;
    document.getElementById('qb_selected_hash').value = t.info_hash;
    document.getElementById('qb_hash_display').innerText = t.info_hash;
    document.getElementById('qb_name_display').innerText = t.name;
    document.getElementById('qb_size_display').innerText = formatBytes(t.size);
    document.getElementById('qb_progress_display').innerText = (t.progress * 100).toFixed(1) + '%';
}

/*
 * BLOQUE EMPAREJAMIENTO MANUAL
 */

/*
 * Envía el vínculo entre ficha Kitsunarr e info hash de qBittorrent.
 */
async function pairTorrents() {
    const guid = document.getElementById('kit_selected_guid').value;
    const hash = document.getElementById('qb_selected_hash').value;
    if (!guid || !hash) return showToast("Selecciona una ficha y un torrent", false);
    const btn = document.getElementById('btn_pair_torrents');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Emparejando...';
    try {
        const res = await fetch('/api/ui/torrent/pair', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ guid: guid, info_hash: hash })
        });
        const data = await res.json();
        if (data.success) {
            showToast("¡Emparejamiento exitoso!");
            setTimeout(() => location.reload(), 1500);
        } else {
            showToast(data.error, false);
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    } catch (e) {
        showToast("Error de red", false);
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

/*
 * Convierte bytes a texto legible dentro del laboratorio de torrents.
 */
function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/*
 * Muestra el desplegable de fichas Kitsunarr.
 */
function showKitDropdown() { document.getElementById('kit_dropdown_list').classList.remove('hidden'); }

/*
 * Oculta el desplegable de fichas Kitsunarr tras permitir selección.
 */
function hideKitDropdownDelayed() { setTimeout(() => { const l = document.getElementById('kit_dropdown_list'); if(l) l.classList.add('hidden'); }, 200); }

/*
 * Muestra el desplegable de torrents qBittorrent.
 */
function showQbDropdown() { document.getElementById('qb_dropdown_list').classList.remove('hidden'); }

/*
 * Oculta el desplegable de torrents qBittorrent tras permitir selección.
 */
function hideQbDropdownDelayed() { setTimeout(() => { const l = document.getElementById('qb_dropdown_list'); if(l) l.classList.add('hidden'); }, 200); }

/*
 * Inicializa el laboratorio de torrents cuando la vista está cargada.
 */
document.addEventListener("DOMContentLoaded", () => {
    loadData();
});
