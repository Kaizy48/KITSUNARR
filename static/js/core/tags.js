/*
 * BLOQUE CATALOGO CENTRAL DE TAGS
 */

const STANDARD_TAGS = [
    "8K", "4K", "2160p", "1440p", "1080p", "720p", "480p",
    "BDRemux", "Blu-ray", "BD", "BDRip", "WEB-DL", "WEBRip", "HDTV", "DVD", "TV",
    "10-bit", "8-bit", "HDR", "Dolby Vision",
    "AV1", "HEVC", "x265", "H.265", "AVC", "x264", "H.264",
    "Dual", "Multi-Audio", "FLAC", "ALAC", "TrueHD", "DTS-HD", "DTS", "AC3", "E-AC3", "AAC", "MP3", "Opus",
    "Multi-Sub", "Softsubs", "Hardsubs",
    "MKV", "MP4", "AVI",
    "Batch", "Remastered", "Uncensored"
];

/*
 * BLOQUE CLASIFICACION DE TAGS
 */

/*
 * Calcula el peso de orden de una etiqueta para que Kitsunarr muestre primero resolución, fuente, codec, audio y formato.
 */
function getTagWeight(tag) {
    const t = tag.toLowerCase();
    
    if (['8k', '4320p'].includes(t)) return 10;
    if (['4k', '2160p'].includes(t)) return 11;
    if (['1440p', '2k'].includes(t)) return 12;
    if (['1080p'].includes(t)) return 13;
    if (['720p'].includes(t)) return 14;
    if (['480p'].includes(t)) return 15;
    
    if (['bdremux', 'remux'].includes(t)) return 20;
    if (['blu-ray', 'bd', 'blu-ray4k', 'bd4k'].includes(t)) return 21;
    if (['bdrip', 'brrip'].includes(t)) return 22;
    if (['web-dl', 'web'].includes(t)) return 23;
    if (['webrip'].includes(t)) return 24;
    if (['hdtv'].includes(t)) return 25;
    if (['dvd'].includes(t)) return 26;
    if (['tv'].includes(t)) return 27;
    
    if (['10-bit', '10bit', 'hi10p'].includes(t)) return 30;
    if (['8-bit', '8bit'].includes(t)) return 31;
    if (['hdr', 'hdr10', 'dv', 'dolby vision'].includes(t)) return 32;

    if (['av1'].includes(t)) return 40;
    if (['hevc', 'x265', 'h.265'].includes(t)) return 41;
    if (['avc', 'x264', 'h.264'].includes(t)) return 42;
    
    if (['dual', 'multi-audio'].includes(t)) return 50;
    if (['flac', 'alac', 'lossless'].includes(t)) return 51;
    if (['truehd', 'dts-hd', 'dts-hd ma'].includes(t)) return 52;
    if (['dts', 'ac3', 'e-ac3', 'dd5.1'].includes(t)) return 53;
    if (['aac', 'mp3', 'opus', 'ogg'].includes(t)) return 54;
    
    if (['multi-sub', 'multi'].includes(t)) return 60;
    if (['softsubs', 'softsub'].includes(t)) return 61;
    if (['hardsubs', 'hardsub'].includes(t)) return 62;

    if (['mkv'].includes(t)) return 70;
    if (['mp4'].includes(t)) return 71;
    if (['avi'].includes(t)) return 72;
    
    if (['batch', 'pack'].includes(t)) return 80;
    if (['uncensored', 'sin censura'].includes(t)) return 81;
    if (['remastered'].includes(t)) return 82;

    return 99;
}

/*
 * Ordena dos etiquetas usando su peso de categoría y, si empatan, el nombre visible.
 */
function sortTags(a, b) {
    const weightA = getTagWeight(a);
    const weightB = getTagWeight(b);
    
    if (weightA !== weightB) {
        return weightA - weightB;
    }
    return a.localeCompare(b);
}

/*
 * Devuelve el estilo visual y el icono que Kitsunarr usa para representar una etiqueta en tarjetas y filtros.
 */
function getTagData(tag) {
    const w = getTagWeight(tag);
    
    if (w >= 10 && w < 20) return { style: "bg-blue-900/40 text-blue-300 border-blue-700/50", icon: "📺" };
    if (w >= 20 && w < 30) return { style: "bg-slate-800/80 text-slate-300 border-slate-600/50", icon: "💿" };
    if (w >= 30 && w < 40) return { style: "bg-indigo-900/40 text-indigo-300 border-indigo-700/50", icon: "🎨" };
    if (w >= 40 && w < 50) return { style: "bg-purple-900/40 text-purple-300 border-purple-700/50", icon: "🎞️" };
    if (w >= 50 && w < 60) return { style: "bg-orange-900/40 text-orange-300 border-orange-700/50", icon: "🔊" };
    if (w >= 60 && w < 70) return { style: "bg-yellow-900/40 text-yellow-300 border-yellow-700/50", icon: "💬" };
    if (w >= 70 && w < 80) return { style: "bg-teal-900/40 text-teal-300 border-teal-700/50", icon: "📦" };
    if (w >= 80 && w < 90) return { style: "bg-red-900/40 text-red-300 border-red-700/50", icon: "✨" };
    
    return { style: "bg-gray-800/80 text-gray-300 border-gray-600/50", icon: "🏷️" };
}
