/*
 * BLOQUE CONFIGURACION INICIAL
 */

/*
 * Crea el primer administrador de Kitsunarr desde el asistente de instalación.
 */
async function handleSetup(e) {
    e.preventDefault();
    const user = document.getElementById('username').value.trim();
    const pass = document.getElementById('password').value;
    const passConf = document.getElementById('password_confirm').value;
    const errBox = document.getElementById('error-box');
    const btn = document.getElementById('submitBtn');

    if (pass !== passConf) {
        errBox.innerText = "Las contraseñas no coinciden.";
        errBox.classList.remove('hidden');
        return;
    }

    if (pass.length < 6) {
        errBox.innerText = "La contraseña debe tener al menos 6 caracteres.";
        errBox.classList.remove('hidden');
        return;
    }

    errBox.classList.add('hidden');
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Configurando...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/ui/auth/setup', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({admin_user: user, admin_password: pass})
        });
        const data = await res.json();

        if (data.success) {
            window.location.href = "/login";
        } else {
            errBox.innerText = data.error || (data.detail ? data.detail[0].msg : "Error desconocido");
            errBox.classList.remove('hidden');
            btn.innerHTML = '<i class="fa-solid fa-rocket mr-2"></i> Iniciar Kitsunarr';
            btn.disabled = false;
        }
    } catch (err) {
        errBox.innerText = "Error de red al conectar con el servidor.";
        errBox.classList.remove('hidden');
        btn.innerHTML = '<i class="fa-solid fa-rocket mr-2"></i> Iniciar Kitsunarr';
        btn.disabled = false;
    }
}
