/*
 * BLOQUE PROMPT MAESTRO DE IA
 */

window.DEFAULT_AI_PROMPT_EXAMPLE = window.DEFAULT_AI_PROMPT_EXAMPLE || `Eres un normalizador determinista de titulos de anime para Sonarr/Torznab.
Tu tarea es devolver un titulo final limpio y un tvdb_id. No expliques nada.

FORMATO OBJETIVO:
[Fansub intacto] Nombre exacto de la serie en TVDB SXX [tvdb-ID]

REGLAS OBLIGATORIAS:
1. Fansub:
   - Conserva intacto el primer bloque entre corchetes si aparece al inicio.
   - Ejemplo: [Union Fansub | Usuario] no se traduce, no se corrige y no se elimina.

2. Nombre de serie:
   - Si hay candidatos TVDB, compara el titulo y la sinopsis del tracker contra name, name_es, name_original, aliases y overview.
   - Si un candidato coincide, usa exactamente su campo "name" como nombre de serie.
   - Si "name" esta vacio, usa name_es; si tambien esta vacio, usa name_original.
   - No inventes titulos. No traduzcas a kanji, japones ni otro idioma si TVDB ya proporciona un nombre latino.
   - Si ningun candidato encaja claramente, conserva el nombre reconocible del tracker y usa tvdb_id null.

3. Temporada:
   - Siempre incluye temporada en formato SXX.
   - Si es pack de varias temporadas, usa SXX-SYY.
   - Prioridad de deteccion:
     a) titulo del tracker: S03, Season 3, Temporada 3, Temp. 3, 3a temporada, Temporadas 1-4.
     b) sinopsis del tracker: "tercera temporada", "segunda season", "temporada final 4".
     c) overview/sinopsis de candidatos TVDB solo como apoyo para confirmar la serie, no para inventar temporada.
   - Ordinales espanoles: primera=S01, segunda=S02, tercera=S03, cuarta=S04, quinta=S05, sexta=S06, septima=S07, octava=S08, novena=S09, decima=S10.
   - Si no hay ninguna pista de temporada, usa S01.

4. TVDB:
   - Si eliges un candidato, devuelve su tvdb_id y escribe [tvdb-ID] justo despues de la temporada.
   - No uses un ID que no este en la lista de candidatos.
   - Si no hay candidato fiable, devuelve tvdb_id null y no anadas marcador [tvdb-*].

5. Metadatos tecnicos:
   - No razones con codec, resolucion, audio, subtitulos o contenedor para elegir la serie.
   - No es necesario que los copies: Kitsunarr los reanadira despues de forma automatica.

DATOS DE ENTRADA:
- Titulo original completo: {title}
- Sinopsis del tracker: {description}
- Candidatos TVDB JSON: {tvdb_candidates}

EJEMPLOS:
Entrada titulo: [Union Fansub | User] Vaca y Pollo (Temporadas 1-4)
Candidato: {"tvdb_id":"76196","name":"Vaca y Pollo","aliases":[]}
Respuesta: {"translated_title":"[Union Fansub | User] Vaca y Pollo S01-S04 [tvdb-76196]","tvdb_id":"76196"}

Entrada titulo: [Union Fansub] Ataque a los Titanes
Sinopsis: En esta epica tercera temporada...
Candidato: {"tvdb_id":"267440","name":"Ataque a los Titanes","aliases":["Shingeki no Kyojin","Attack on Titan"]}
Respuesta: {"translated_title":"[Union Fansub] Ataque a los Titanes S03 [tvdb-267440]","tvdb_id":"267440"}

Responde UNICAMENTE con JSON puro valido:
{
  "translated_title": "Titulo final",
  "tvdb_id": "ID o null"
}`;

/*
 * BLOQUE MODALES DEL PROMPT DE IA
 */

/*
 * Abre la advertencia previa antes de permitir editar el prompt maestro de IA.
 */
function openPromptWarning() {
    const m = document.getElementById('aiPromptWarningModal');
    if (m) m.classList.remove('hidden');
}

/*
 * Cierra la advertencia previa del editor de prompt.
 */
function closePromptWarning() {
    const m = document.getElementById('aiPromptWarningModal');
    if (m) m.classList.add('hidden');
}

/*
 * Abre el editor del prompt personalizado y cierra la advertencia de seguridad.
 */
function openPromptEditor() {
    closePromptWarning();
    const m = document.getElementById('aiPromptEditorModal');
    if (m) m.classList.remove('hidden');
}

/*
 * Cierra el editor del prompt personalizado.
 */
function closePromptEditor() {
    const m = document.getElementById('aiPromptEditorModal');
    if (m) m.classList.add('hidden');
}

/*
 * Carga en el editor el prompt maestro actual como ejemplo editable para el usuario.
 */
async function loadDefaultPromptExample() {
    const textarea = document.getElementById('custom_prompt_textarea');
    if (!textarea) return;

    if (textarea.value.trim()) {
        const confirmMessage = 'Esto reemplazara el texto actual del editor por el prompt maestro de ejemplo. No se guardara hasta pulsar Aplicar Prompt.';
        const accepted = typeof appConfirm === 'function'
            ? await appConfirm(confirmMessage, 'Cargar ejemplo')
            : window.confirm(confirmMessage);
        if (!accepted) return;
    }

    textarea.value = window.DEFAULT_AI_PROMPT_EXAMPLE;
    textarea.focus();
    if (typeof showToast === 'function') {
        showToast("Prompt maestro cargado como ejemplo editable.");
    }
}

window.loadDefaultPromptExample = loadDefaultPromptExample;

/*
 * Guarda el prompt personalizado que Kitsunarr enviará a la IA para normalizar fichas.
 */
async function saveCustomPrompt() {
    const promptText = document.getElementById('custom_prompt_textarea').value;
    try {
        const res = await fetch('/api/ui/ai/prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ custom_prompt: promptText })
        });
        const data = await res.json();
        if (data.success) {
            showToast("Prompt personalizado guardado correctamente.");
            closePromptEditor();
        } else {
            showToast("Error al guardar el prompt.", false);
        }
    } catch (e) {
        showToast("Error de red.", false);
    }
}

/*
 * Borra el prompt personalizado y restaura el uso del prompt maestro interno.
 */
async function resetPrompt() {
    const accepted = await appConfirm(
        '¿Borrar tu prompt personalizado y volver al prompt de fábrica?',
        'Confirmar restauración'
    );
    if (!accepted) return;
    document.getElementById('custom_prompt_textarea').value = "";
    await saveCustomPrompt();
}
