(function () {
    'use strict';
    const root = document.querySelector('[data-ops-page]');
    if (!root) return;
    const find = (selector) => root.querySelector(selector);
    const set = (selector, text) => { const node = find(selector); if (node) node.textContent = text; };
    const notice = find('[data-ops-update]');
    const initial = new URL(window.location.href);
    let selectedID = initial.searchParams.get('run_id') || '';
    let selected = null;
    let paused = false;
    let busy = false;
    let historyKey = '';
    function formatDate(value) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? 'Sin fecha' : date.toLocaleString('es-MX', {dateStyle:'short', timeStyle:'medium'});
    }
    function runState(run) {
        if (run.status === 'running' && run.stale) return 'Sin actualizaciones';
        return {running:'En ejecución', succeeded:'Completada', failed:'Fallida', unreachable:'Host inaccesible'}[run.status] || 'Sin datos';
    }
    function renderVault(data) {
        const v = data.snapshot;
        let state = 'Sin datos de Vault';
        let description = 'Todavía no se ha recibido una lectura del servidor.';
        if (v) {
            state = v.stale ? 'Lectura antigua' : !v.reachable ? 'Sin conexión' : !v.initialized ? 'Sin inicializar' : v.sealed ? 'Sellado' : v.standby ? 'En espera' : 'Operativo';
            description = v.stale ? 'El colector lleva más de un minuto sin enviar una lectura. El estado actual no está confirmado.' : !v.reachable ? 'El colector no pudo verificar la conexión HTTPS con el servidor.' : v.sealed ? 'El servidor responde. Se requiere la ceremonia de unseal para habilitar la bóveda.' : !v.initialized ? 'El servidor responde y está pendiente de inicialización.' : 'El servidor responde con TLS verificado y la bóveda está disponible.';
        }
        set('[data-vault-state]', state);
        set('[data-vault-description]', description);
        set('[data-vault-initialized]', !v || v.initialized === null ? 'Sin lectura' : v.initialized ? 'Inicializada' : 'Pendiente');
        set('[data-vault-sealed]', !v || v.sealed === null ? 'Sin lectura' : v.sealed ? 'Sellada' : 'Desprecintada');
        set('[data-vault-tls]', !v ? 'Sin lectura' : v.reachable ? 'Verificado' : 'Sin verificar');
        set('[data-vault-version]', v && v.version ? v.version : '—');
        set('[data-vault-received]', v ? formatDate(v.received_at) + (v.stale ? ' · lectura antigua' : '') : 'No recibida');
        const dot = find('[data-vault-dot]');
        dot.className = 'ops-dot' + (v && !v.stale && v.reachable && v.initialized && !v.sealed ? ' is-ok' : v ? ' is-warning' : '');
    }
    function renderHistory(runs) {
        const key = JSON.stringify(runs) + selectedID;
        if (key === historyKey) return;
        historyKey = key;
        const list = find('[data-run-list]');
        const focusID = document.activeElement && document.activeElement.dataset.runId;
        set('[data-run-count]', String(runs.length));
        if (!runs.length) return;
        const rows = runs.map(function (run) {
            const link = document.createElement('a');
            const href = new URL(window.location.href);
            href.searchParams.set('run_id', run.id);
            link.href = href.href;
            link.className = 'execution-item' + (selected && selected.id === run.id ? ' is-selected' : '');
            link.dataset.runId = run.id;
            const title = document.createElement('strong'); title.textContent = run.playbook;
            const status = document.createElement('span'); status.textContent = runState(run);
            const meta = document.createElement('small'); meta.textContent = run.elapsed + ' s · ' + formatDate(run.started_at);
            link.append(title, status, meta);
            return link;
        });
        list.replaceChildren(...rows);
        if (focusID) Array.from(list.querySelectorAll('[data-run-id]')).find((link) => link.dataset.runId === focusID)?.focus({preventScroll:true});
    }
    function renderLog() {
        if (paused || !selected) return;
        const term = find('[data-log-search]').value.toLowerCase();
        const errors = find('[data-errors-only]').checked;
        const events = selected.events || [];
        const filtered = events.filter((event) => (!errors || ['failed','unreachable'].includes(event.type)) && [event.label,event.host,event.type].join(' ').toLowerCase().includes(term));
        const output = find('[data-terminal-output]');
        const oldScroll = output.scrollTop;
        const fragment = document.createDocumentFragment();
        filtered.forEach(function (event) {
            const line = document.createElement('div'); line.className = 'terminal-line'; line.dataset.eventType = event.type;
            const time = document.createElement('span'); time.className = 'terminal-time'; time.textContent = '+' + event.elapsed + 's';
            const type = document.createElement('span'); type.className = 'terminal-kind'; type.textContent = event.type;
            const message = document.createElement('span'); message.textContent = event.label + (event.host ? ' · ' + event.host : '');
            line.append(time,type,message); fragment.append(line);
        });
        if (!filtered.length) {
            const empty = document.createElement('p'); empty.textContent = events.length ? 'No hay eventos que coincidan con el filtro.' : 'Esperando los primeros eventos de la ejecución.'; fragment.append(empty);
        }
        output.replaceChildren(fragment);
        set('[data-log-count]', filtered.length + ' de ' + events.length + ' eventos · últimas 400 líneas');
        output.scrollTop = find('[data-log-follow]').checked ? output.scrollHeight : oldScroll;
    }
    function renderAnsible(data) {
        selected = data.selected;
        renderHistory(data.runs || []);
        find('[data-log-download]').disabled = !selected;
        if (!selected) return;
        set('[data-run-title]', selected.playbook);
        set('[data-run-meta]', selected.id + ' · ' + formatDate(selected.started_at) + ' · ' + selected.elapsed + ' s');
        set('[data-run-status]', runState(selected));
        set('[data-terminal-playbook]', selected.playbook);
        set('[data-terminal-live]', selected.status === 'running' && !selected.stale ? 'EN DIRECTO' : selected.stale && selected.status === 'running' ? 'SIN SEÑAL' : 'FINALIZADA');
        Object.entries(selected.counters || {}).forEach(([key,value]) => set('[data-counter="' + key + '"]', String(value)));
        renderLog();
    }
    async function refresh() {
        if (busy || document.hidden) return;
        busy = true;
        const abort = new AbortController();
        const deadline = window.setTimeout(() => abort.abort(), 7000);
        try {
            const url = new URL(root.dataset.endpoint, window.location.origin);
            if (selectedID) url.searchParams.set('run_id', selectedID);
            const response = await fetch(url, {credentials:'same-origin', cache:'no-store', signal:abort.signal, headers:{Accept:'application/json'}});
            if (response.redirected || response.status === 401) throw new Error('La sesión ha caducado. Vuelve a iniciar sesión.');
            if (!response.ok) throw new Error(response.status === 403 ? 'Esta vista requiere una cuenta con rol administrador.' : 'No se pudo actualizar. Se conserva la última lectura.');
            const data = await response.json();
            if (root.dataset.opsPage === 'vault') renderVault(data); else renderAnsible(data);
            notice.classList.remove('is-error');
            notice.textContent = 'Actualizado a las ' + new Date().toLocaleTimeString('es-MX') + ' · cada 5 segundos' + (paused ? ' · consola en pausa' : '');
        } catch (error) {
            notice.classList.add('is-error');
            notice.textContent = error.name === 'AbortError' ? 'La actualización tardó demasiado. Se conserva la última lectura.' : error.message;
        } finally { window.clearTimeout(deadline); busy = false; }
    }
    root.querySelectorAll('[data-ops-refresh]').forEach((button) => button.addEventListener('click', refresh));
    if (root.dataset.opsPage === 'ansible') {
        find('[data-run-list]').addEventListener('click', function (event) {
            const link = event.target.closest('[data-run-id]');
            if (!link || event.ctrlKey || event.metaKey || event.shiftKey) return;
            event.preventDefault(); selectedID = link.dataset.runId;
            window.history.pushState({}, '', link.href); refresh();
        });
        window.addEventListener('popstate', () => { selectedID = new URL(window.location.href).searchParams.get('run_id') || ''; refresh(); });
        find('[data-log-search]').addEventListener('input', renderLog);
        find('[data-errors-only]').addEventListener('change', renderLog);
        find('[data-log-pause]').addEventListener('click', function () { paused = !paused; this.textContent = paused ? 'Reanudar' : 'Pausar'; this.setAttribute('aria-pressed', String(paused)); if (!paused) renderLog(); });
        find('[data-log-download]').addEventListener('click', function () {
            if (!selected) return;
            const lines = ['ANSIBLE · ' + selected.playbook, selected.started_at + ' · ' + runState(selected), 'Registro estructurado sin argumentos ni salida de comandos.', ''];
            (selected.events || []).forEach((event) => lines.push('+' + event.elapsed + 's [' + event.type.toUpperCase() + '] ' + event.label + (event.host ? ' · ' + event.host : '')));
            lines.push('', 'PLAY RECAP ' + Object.entries(selected.counters).map(([key,value]) => key + '=' + value).join(' '));
            const url = URL.createObjectURL(new Blob([lines.join('\n')], {type:'text/plain;charset=utf-8'}));
            const link = document.createElement('a'); link.href = url; link.download = 'ansible-' + selected.id + '.log'; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        });
    }
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    refresh();
    window.setInterval(refresh, 5000);
})();
