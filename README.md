<p align="center">
  <img src="static/img/Kitsunarr-logo-512x512.png" alt="Kitsunarr Logo" width="200"/>
</p>

<h1 align="center">Kitsunarr</h1>

<p align="center">
  <strong>Proxy Inteligente & Indexador</strong><br>
  El puente definitivo entre trackers de nicho y la automatización avanzada.
</p>

---

### 🦊 ¿Qué es Kitsunarr?

**Kitsunarr** es un proxy e indexador especializado para escenarios donde herramientas generalistas no logran una integración perfecta con trackers de anime complejos. Actúa como un **reformateador activo** que traduce el contenido de trackers de nicho al estándar **Torznab** que Sonarr entiende a la perfección.

---

######### 🔐 SEGURIDAD Y CRIPTOGRAFÍA 🛡️ #########

* **Base de Datos Blindada**: Cifrado simétrico AES-256 (Fernet) nativo para proteger contraseñas, cookies de sesión y API Keys.
* **Hashing Avanzado**: Contraseñas de administrador protegidas mediante el algoritmo irreversible Argon2.
* **Descifrado en Memoria RAM**: Los motores de IA, Scrapers y TVDB descifran las credenciales al vuelo únicamente en la memoria RAM durante la ejecución.
* **Enmascaramiento UI**: La interfaz gráfica oculta automáticamente los campos sensibles (`********`) para evitar exposiciones accidentales.

######### 🔄 ECOSISTEMA Y SINCRONIZACIÓN ARR 🌐 #########

* **Auto-Sincronización**: Al generar tu Clave API maestra, Kitsunarr auto-configura o actualiza su indexador Torznab en tus instancias de Sonarr y Radarr de forma automática.
* **Soporte para Redes Docker**: Nuevo parámetro de "URL Interna" para permitir la comunicación directa en configuraciones con Proxies Inversos o redes cerradas.

######### 📡 INDEXADORES Y SCRAPING 🔍 #########

* **Compatibilidad Nativa**: Scraper optimizado con soporte para navegación interactiva y RSS.
* **Gestión de Sesión**: Sistema de Auto-Login y renovación automática de cookies para evitar interrupciones en las descargas.
* **Extracción Profunda**: Captura metadatos técnicos avanzados (códecs de video, múltiples pistas de audio, idiomas de subtítulos).
* **Búsqueda Interactiva**: Realiza búsquedas manuales desde la UI que disparan un raspado en tiempo real de los indexadores configurados.

######### 🧠 MOTOR DE INTELIGENCIA ARTIFICIAL 🤖 #########

* **Multi-Proveedor**: Soporte integrado para **Gemini**, **OpenAI** y **Ollama** (LLMs locales).
* **Procesamiento Automatizado**: Trabajadores de fondo que limpian títulos, identifican temporadas y resuelven conflictos de nombres sin intervención humana.
* **Laboratorio de Pruebas**: Interfaz dedicada para probar prompts, con ajustes en config para límites de tokens (RPM/TPM) y visualizar la respuesta cruda de la IA.
* **Batch Processing**: Capacidad de enviar lotes de torrents pendientes para una normalización inmediata.

######### 📺 INTEGRACIÓN CON THETVDB (v4) 🇯🇵 #########

* **Jerarquía Romaji/Latino**: Sistema de nombres inteligente que prioriza la pronunciación japonesa en letras latinas para una identificación rápida.
* **Enciclopedia Local**: Descarga y almacena sinopsis traducidas, pósters de alta calidad y estados de emisión.
* **Sincronización de Episodios**: Obtiene listas completas de capítulos con títulos traducidos y formateados bajo el estándar `SxxExx - Nombre`.
* **Omnibox con Alias**: Buscador inteligente que detecta coincidencias incluso por nombres alternativos de las series.

######### 🖼️ VISTA EN PÓSTER Y GALERÍA 🎭 #########

* **Identificación Visual**: Interfaz basada en cuadrículas de pósters para una navegación intuitiva por tu biblioteca local y los resultados de búsqueda.
* **Tarjetas Informativas**: Cada título muestra de un vistazo su estado de procesamiento, fansub de origen y año de estreno.
* **Efectos de Interacción**: Animaciones de zoom y overlays de información detallada al pasar el cursor sobre las carátulas.
* **Filtros en Tiempo Real**: Buscador instantáneo que filtra la galería de pósters por nombre, ID o alias mientras escribes.

######### 🗄️ CACHÉ Y BASE DE DATOS RELACIONAL 🔗 #########

* **Ficha Técnica Dual**: Modal de visualización que permite comparar los datos originales del tracker frente a los datos enriquecidos de la biblioteca TVDB local.
* **Gestión de Estados**: Árbol de iconos semánticos para identificar el progreso de cada ficha (Pendiente ⏳, Candidatos 📋, Validado ✅, Revisión Manual 👁️).

######### 📦 EXPORTACIÓN E IMPORTACIÓN MODULAR 💾 #########

* **Módulo de Torrents**: Exporta fichas crudas para pedir ayuda a otros usuarios en el procesamiento de nombres.
* **Módulo TVDB**: Comparte tu base de conocimientos (metadatos y episodios) con la comunidad.
* **Bundle de Backup**: Copia de seguridad total que empaqueta Torrents, Fichas TVDB y sus Relaciones (candidatos de la ficha).
* **Rehidratación Inteligente**: Al importar, Kitsunarr reconstruye automáticamente las URLs de descarga locales y descarga las fichas maestras huérfanas en segundo plano.

######### 🎨 INTERFAZ Y EXPERIENCIA (UX) 🖥️ #########

* **Diseño Panorámico**: Nueva Ficha Maestra en 3 columnas optimizada para monitores modernos y lectura cómoda de sinopsis.
* **Aviso de Actualizaciones**: Sistema conectado a la API de GitHub que notifica automáticamente cuando hay una nueva versión disponible en el repositorio.
* **Consola de Eventos**: Registro detallado de actividad en tiempo real para monitorizar el comportamiento de los workers de fondo.

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

---

### ⚖️ Licencia y Transparencia

Este proyecto es de **Código Abierto** bajo la licencia **GNU GPL v3**. Creemos en la transparencia total: el código es auditable para que cualquier usuario sepa exactamente cómo se manejan sus sesiones y datos.

**Desarrollo Asistido por IA**: Este software utiliza herramientas de Inteligencia Artificial en su proceso de creación para optimizar la lógica y el flujo de trabajo. No obstante, **todo el código es revisado, editado y validado manualmente por programadores con conocimientos técnicos** para garantizar que el sistema sea seguro, eficiente y cumpla con los estándares de calidad necesarios para su uso en producción.

**Nota sobre Atribución**: Tienes derecho a ver, modificar y usar este código. Sin embargo, para cualquier clon o proyecto derivado, **se exige la mención expresa de Kitsunarr como el proyecto original**, manteniendo los créditos del autor de forma visible y clara en todo momento.

#####################

⚠️ Disclaimer (Aviso Legal)
Kitsunarr es una herramienta de software diseñada exclusivamente como un proxy de metadatos y organizador de información para uso personal.

* **No comparte archivos**: Kitsunarr no aloja, distribuye ni facilita la descarga de archivos protegidos por derechos de autor.
* **No es un cliente de descarga**: La aplicación no descarga contenido; su única función es facilitar la comunicación de datos entre servicios de terceros.
* **Responsabilidad**: El usuario es el único responsable del uso que haga de esta herramienta y de asegurar que su actividad cumple con las leyes locales.

#####################

**Desarrollado con ❤️ para hacer el self-hosting de anime más inteligente y sencillo. Firmado Kaizy_48** 🦊✨