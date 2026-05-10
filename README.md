<p align="center">
  <img src="static/img/Kitsunarr-logo-512x512.png" alt="Kitsunarr Logo" width="200"/>
</p>

<h1 align="center">Kitsunarr</h1>

<p align="center">
  <strong>Proxy Inteligente & Indexador Torznab</strong><br>
  El puente entre trackers de anime y el ecosistema *arr
</p>

---

### 🦊 ¿Qué es Kitsunarr?

**Kitsunarr** es una aplicación self-hosted que actúa como **proxy, indexador y normalizador de metadatos** para trackers que no encajan bien con el ecosistema *arr.

Su objetivo principal es recibir peticiones de **Sonarr** mediante **Torznab**, raspar trackers compatibles, enriquecer los resultados con **TheTVDB** e **IA**, guardar una caché local y devolver títulos limpios que Sonarr pueda entender mejor.

---

######### 🧪 ESTADO DEL PROYECTO #########

* **Beta activa**: Kitsunarr ya es usable, pero todavía está en fase de validación con feedback real.
* **Uso recomendado**: habilita solo la opción de busqueda interactiva y contrasta los resultados, en caso de error reporta una issue en github.
* **Enfoque actual**: proporcionar compatibilidad con trackers de anime sin soporte al ecosistema *arr

---

######### 🔐 SEGURIDAD Y CRIPTOGRAFÍA 🛡️ #########

* **Base de datos local**: SQLite para configuración, caché, fichas TVDB, candidatos, episodios y relaciones.
* **Cifrado de secretos**: Fernet/AES para cookies, API keys, credenciales Arr, credenciales qBittorrent y claves sensibles.
* **Descifrado en memoria**: las credenciales se descifran solo durante la ejecución de scrapers, IA, TVDB, Arr o qBittorrent.
* **UI protegida**: login, sesión JWT y campos sensibles enmascarados.
* **Torznab protegido**: Sonarr accede mediante la API key generada por Kitsunarr.

---

######### 🔄 ECOSISTEMA ARR Y TORZNAB 🌐 #########

* **Endpoint Torznab compatible**: `/api?t=...` con soporte para `caps`, `search` y `tvsearch`.
* **Categoría Anime**: respuesta Torznab orientada a `5000/5070`.
* **Autoagregado en Sonarr/Radarr**: Kitsunarr puede crear o actualizar su indexador Torznab en aplicaciones arr.
* **URL interna**: campo específico para instalaciones Docker, proxies inversos o redes internas.
* **Regeneración de API key**: al regenerar la clave, Kitsunarr puede sincronizarla con Arr.

---

######### 📡 INDEXADORES Y SCRAPING 🔍 #########

* **Autenticación flexible**: soporte para cookie o usuario/contraseña según configuración del indexador.
* **Scraping paginado**: recorre páginas sucesivas cuando la búsqueda contiene texto.
* **Scraping profundo**: si una ficha no existe en caché, Kitsunarr abre la ficha del tracker y extrae más información.
* **Metadatos técnicos**: resolución, fuente, codec, audio, subtítulos, contenedor, softsubs, freeleech, tamaño y archivos.
* **Portadas**: obtiene imágenes desde el hilo/ficha asociada cuando el tracker lo permite.
* **Protección de caché**: evita repetir trabajo cuando el GUID ya existe en la base local.

---

######### 🧠 MOTOR DE INTELIGENCIA ARTIFICIAL 🤖 #########

* **Multi-proveedor**: OpenAI, Gemini y Ollama.
* **Modelos locales**: preparado para modelos tipo `qwen2.5-coder:7b`, `llama3.1:8b` o equivalentes vía Ollama.
* **Prompt maestro**: normaliza títulos para Sonarr usando título, sinopsis, candidatos TVDB y reglas de temporada.
* **Prompt personalizado**: la UI permite crear un prompt propio con guía y cargar el prompt maestro como ejemplo.
* **Detección de temporadas**: por título, sinopsis del tracker, menciones tipo “tercera temporada”, packs y rangos.
* **Respeto de TVDB**: evita inventar nombres y prioriza el nombre oficial/candidato vinculado.
* **Procesamiento automático**: worker de fondo para fichas pendientes si la IA y el modo automático están activados.
* **Procesamiento manual**: botón para reenviar fichas concretas o lotes a la IA.
* **Cuotas por modelo**: control de RPM, TPM, RPD, backoff y estado runtime.
* **Laboratorio de IA**: ping de proveedor, selección de ficha y prueba de normalización sin guardar cambios.

---

######### 📺 INTEGRACIÓN CON THETVDB (v4) 🇯🇵 #########

* **Configuración opcional**: TheTVDB puede estar activado o desactivado desde la UI.
* **Búsqueda automática**: limpia títulos de torrents y busca candidatos TVDB.
* **Búsqueda manual**: vista dedicada para consultar TheTVDB y descargar fichas maestras.
* **Biblioteca local TVDB**: guarda series, alias, nombres, sinopsis, pósters, banners, estado y temporadas.
* **Episodios locales**: descarga episodios y los agrupa por temporada para consulta desde la UI.
* **Candidatos por ficha**: guarda relaciones entre torrents y posibles series TVDB.
* **Vinculación manual**: omnibox con biblioteca local, sugerencias IA, alias y opción de forzar ID.

---

######### 🗄️ CACHÉ LOCAL Y FICHAS TÉCNICAS 🔗 #########

* **Caché de torrents**: almacena resultados del tracker con GUID, título original, título IA, TVDB, tags, archivos y telemetría.
* **Ficha técnica del torrent**: muestra póster, título normalizado, nombre original, tags, sinopsis, tamaño, temporada, TVDB y archivos.
* **Editor manual**: permite modificar título final, temporada, descripción, TVDB, tags y renombrado de archivos.
* **Renombrado por archivo**: guarda nombres preparados para Sonarr cuando hay múltiples archivos de vídeo.
* **Tags inteligentes**: resolución, fuente, codec, audio, subtítulos, contenedor y etiquetas personalizadas.
* **Descarga manual**: permite descargar el `.torrent` original desde la ficha.

---

######### 📚 ESTANTERÍA DE SERIES #########

* **Vista por serie**: al vincular torrents a un TVDB, Kitsunarr crea una estantería de la serie.
* **Hero TVDB**: banner, póster, año, estado, sinopsis y alias.
* **Agrupación por temporada**: temporadas regulares, especiales/OVAs/películas y fichas sin temporada identificada.
* **Filtros por tags**: filtra releases dentro de una serie por calidad, audio, subtítulos, freeleech o tags personalizados.

---

######### 🧲 QBITTORRENT Y TELEMETRÍA #########

* **Conexión qBittorrent**: configuración de URL, usuario y contraseña desde la UI.
* **Laboratorio de torrents**: lista fichas locales sin hash y torrents visibles en qBittorrent.
* **Vinculación manual**: empareja una ficha Kitsunarr con un torrent del cliente usando info hash.
* **Telemetría en ficha**: estado, progreso, descarga, subida, ratio, ETA/hash y presencia en cliente.
* **Refresco periódico**: la ficha actualiza la telemetría mientras está abierta.
* **Cálculo de hash**: puede calcular el info hash desde el `.torrent` origen cuando falta vinculación.

---

######### 🖼️ VISTA EN PÓSTER Y GALERÍA 🎭 #########

* **Cuadrículas responsive**: tarjetas de póster ajustadas al tamaño de pantalla.
* **Escala visual configurable**: modos compacto, normal y grande.
* **Fansub visible**: overlay con el fansub/origen del release.
* **Badges de estado**: IA, TVDB, freeleech, lote, torrents vinculados y estados de emisión.
* **Búsqueda instantánea**: por título, GUID, TVDB y alias.
* **Portadas proxificadas**: Kitsunarr sirve imágenes remotas a través de su backend para evitar problemas de carga.

---

######### 📦 EXPORTACIÓN E IMPORTACIÓN MODULAR 💾 #########

* **Exportación de torrents**: comparte fichas crudas cacheadas.
* **Exportación TVDB**: comparte fichas maestras, metadatos y episodios.
* **Bundle verificado**: backup limpio de torrents con IA Lista y TVDB Listo, junto a fichas TVDB y relaciones/candidatos.
* **Importación inteligente**: añade registros sin pisar existentes.

---

### 🐳 Instalación con Docker

La forma recomendada de ejecutar Kitsunarr es mediante Docker.

#### 1. Preparar el archivo `.env`
Copia el contenido de `.env.example` a un nuevo archivo llamado `.env` y ajusta tus valores:

```env
# Puerto en el que accederás desde tu navegador
KITSUNARR_PORT=4080

# Ruta donde se guardará la base de datos y la llave maestra de cifrado
KITSUNARR_DATA=/mnt/...
KITSUNARR_SECRETS=/mnt/...

# Zona horaria
TZ=Europe/Madrid
```

#### 2. Preparar el archivo `docker-compose.yml`
```yaml
services:
  kitsunarr:
    image: ghcr.io/kaizy48/kitsunarr:latest
    container_name: kitsunarr
    restart: unless-stopped
    env_file:
      - .env
    ports:
      - "${KITSUNARR_PORT}:4080"
    environment:
      - TZ=${TZ}
    volumes:
      - ${KITSUNARR_DATA}:/app/data
      - ${KITSUNARR_SECRETS}:/app/secrets
```

#### 3. Primer arranque

1. Abre `http://IP_DEL_SERVIDOR:4080`.
2. Crea el usuario administrador en el asistente inicial.
3. Añade el indexador.
4. Configura TheTVDB si quieres identificación automática robusta.
5. Configura IA si quieres normalización avanzada.
6. Configura Sonarr desde Kitsunarr o añade manualmente el indexador Torznab.
7. Opcionalmente configura qBittorrent para telemetría y emparejamiento.

---

### ⚖️ Licencia y Transparencia

Este proyecto es de **Código Abierto** bajo la licencia **GNU GPL v3**. Creemos en la transparencia total: el código es auditable para que cualquier usuario sepa exactamente cómo se manejan sus sesiones y datos.

**Desarrollo Asistido por IA**: Este software utiliza herramientas de Inteligencia Artificial en su proceso de creación para optimizar la lógica y el flujo de trabajo. No obstante, **todo el código es revisado, editado y validado manualmente por programadores con conocimientos técnicos** para garantizar que el sistema sea seguro, eficiente y cumpla con los estándares de calidad necesarios.

**Nota sobre Atribución**: Tienes derecho a ver, modificar y usar este código. Sin embargo, para cualquier clon o proyecto derivado, **se exige la mención expresa de Kitsunarr como el proyecto original**, manteniendo los créditos del autor de forma visible y clara en todo momento.

#####################

⚠️ Disclaimer (Aviso Legal)

Kitsunarr es una herramienta de software diseñada exclusivamente como un proxy de metadatos, organizador de información e integrador local para uso personal.

* **No aloja contenido**: Kitsunarr no almacena archivos multimedia protegidos por derechos de autor.
* **No comparte archivos**: Kitsunarr no distribuye contenido ni opera como tracker.
* **No es un cliente de descarga**: la aplicación no descarga contenido multimedia; solo comunica metadatos y enlaces gestionados por servicios externos configurados por el usuario.
* **Responsabilidad**: el usuario es el único responsable del uso que haga de esta herramienta y de asegurar que su actividad cumple con las leyes locales.

#####################

**Desarrollado con ❤️ para hacer el self-hosting de anime más inteligente y sencillo. Firmado Kaizy_48** 🦊✨
