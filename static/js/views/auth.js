// ==========================================
// LÓGICA DE VISTAS: LOGIN Y SETUP
// ==========================================

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
            body: JSON.stringify({username: user, password: pass})
        });
        const data = await res.json();

        if (data.success) {
            window.location.href = "/";
        } else {
            errBox.innerText = data.error;
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

async function handleLogin(e) {
    e.preventDefault();
    const user = document.getElementById('username').value.trim();
    const pass = document.getElementById('password').value;
    const errBox = document.getElementById('error-box');
    const btn = document.getElementById('submitBtn');

    errBox.classList.add('hidden');
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Verificando...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/ui/auth/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: user, password: pass})
        });
        const data = await res.json();

        if (data.success) {
            window.location.href = "/";
        } else {
            errBox.innerHTML = '<i class="fa-solid fa-circle-exclamation mr-1"></i> ' + data.error;
            errBox.classList.remove('hidden');
            btn.innerHTML = '<i class="fa-solid fa-right-to-bracket mr-2"></i> Acceder';
            btn.disabled = false;
        }
    } catch (err) {
        errBox.innerText = "Error de red al conectar con el servidor.";
        errBox.classList.remove('hidden');
        btn.innerHTML = '<i class="fa-solid fa-right-to-bracket mr-2"></i> Acceder';
        btn.disabled = false;
    }
}