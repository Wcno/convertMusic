// convertMusic front end: preview, MP3/MP4 choice, streamed progress, download.
//
// POST /api/convert answers with newline-delimited JSON:
//   {"type":"progress","stage":"download"|"convert","progress":0..0.8}
//   {"type":"file","filename","title","size","mimetype"}
//   {"type":"chunk","data":"<base64>"}  (repeated)
//   {"type":"done"}  or  {"type":"error","message"}
// The file is rebuilt in the browser, so the server keeps nothing between requests.
(() => {
    const SEGMENTS = 24;
    const AMBER_FROM = 19;   // segments 20-24 = convert + transfer (last ~20%)
    const SERVER_SHARE = 0.8;

    const $ = (id) => document.getElementById(id);
    const form = $("unit");
    const urlInput = $("url");
    const loadBtn = $("load");
    const bounceBtn = $("bounce");
    const display = $("display");
    const thumb = $("thumb");
    const ladder = $("ladder");
    const lamps = { link: $("lamp-link"), audio: $("lamp-audio"), mp3: $("lamp-mp3") };

    for (let i = 0; i < SEGMENTS; i++) {
        const seg = document.createElement("span");
        seg.className = "seg" + (i >= AMBER_FROM ? " is-amber" : "");
        ladder.appendChild(seg);
    }
    const segs = [...ladder.children];

    let info = null;        // metadata of the loaded video
    let infoFor = "";       // URL that `info` belongs to
    let infoRequest = 0;    // guards against out-of-order responses
    let busy = false;
    let fileUrl = null;     // object URL of the last file

    // Rough bitrates (bits/s) for the size estimate; MP4 adds ~128 kbps of audio.
    const VIDEO_BITRATE = { 360: 500e3, 720: 1.5e6, 1080: 3e6 };

    // ------------------------------------------------------------ helpers

    const format = () => form.querySelector('input[name="format"]:checked').value;
    const tier = () => form.querySelector('input[name="quality"]:checked').value;
    // The value shown on the checked quality key, e.g. "320" (kbps) or "720" (p).
    const qualityValue = () =>
        form.querySelector('input[name="quality"]:checked').parentElement.querySelector(".sel__num").textContent;
    const qualityLabel = () => (format() === "mp3" ? `${qualityValue()} kbps` : `${qualityValue()}p`);

    function estimateBytes(seconds) {
        const q = Number(qualityValue());
        const bitsPerSecond = format() === "mp3" ? q * 1000 : VIDEO_BITRATE[q] + 128e3;
        return (seconds * bitsPerSecond) / 8;
    }

    // Relabel the quality keys, readouts and main key for MP3 or MP4.
    function applyFormat() {
        const fmt = format();
        form.querySelectorAll("[data-mp3]").forEach((el) => { el.textContent = el.dataset[fmt]; });
        $("q-unit").textContent = fmt === "mp3" ? "kbps" : "resolución";
        $("bounce-sub").textContent = fmt === "mp3" ? "a MP3" : "a MP4";
        updateReadouts();
    }

    function formatTime(seconds) {
        if (!Number.isFinite(seconds)) return "–:––";
        const s = Math.round(seconds);
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const ss = String(s % 60).padStart(2, "0");
        return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
    }

    function formatSize(bytes, estimate) {
        if (!Number.isFinite(bytes) || bytes <= 0) return "–";
        const mb = bytes / 1048576;
        return (estimate ? "≈ " : "") + (mb < 10 ? mb.toFixed(1) : Math.round(mb)) + " MB";
    }

    function updateReadouts(actualSize) {
        $("d-quality").textContent = format() === "mp3" ? qualityValue() : qualityValue() + "p";
        $("d-unit").textContent = format() === "mp3" ? "kbps" : format().toUpperCase();
        $("d-time").textContent = formatTime(info?.duration);
        $("d-size").textContent = actualSize
            ? formatSize(actualSize, false)
            : formatSize(estimateBytes(info?.duration), true);
    }

    function setDisplay(state, label, title, channel = "", thumbUrl = null) {
        display.dataset.state = state;
        $("d-label").textContent = label;
        $("d-title").textContent = title;
        $("d-channel").textContent = channel;
        if (thumbUrl) {
            thumb.src = thumbUrl;
            thumb.hidden = false;
        } else {
            thumb.hidden = true;
            thumb.removeAttribute("src");
        }
    }

    function showInfo(label = "Señal", state = "ready") {
        setDisplay(state, label, info.title, info.channel, info.thumbnail);
        updateReadouts();
    }

    function setLamp(name, state) {
        const lamp = lamps[name];
        lamp.classList.remove("is-on", "is-busy", "is-fault");
        if (state) lamp.classList.add(state);
    }

    function setMeter(fraction, { converting = false, fault = false } = {}) {
        const pct = Math.round(Math.max(0, Math.min(1, fraction)) * 100);
        const lit = Math.round((pct / 100) * SEGMENTS);
        segs.forEach((seg, i) => seg.classList.toggle("is-lit", i < lit));
        ladder.toggleAttribute("data-converting", converting);
        ladder.toggleAttribute("data-fault", fault);
        ladder.setAttribute("aria-valuenow", String(pct));
        $("pct").textContent = String(pct);
    }

    const status = (text) => { $("status").textContent = text; };

    function fault(message) {
        $("fault-msg").textContent = message;
        $("fault").hidden = !message;
    }

    function setBusy(value) {
        busy = value;
        bounceBtn.disabled = value;
        bounceBtn.setAttribute("aria-busy", String(value));
        bounceBtn.querySelector(".key__main").textContent = value ? "Grabando" : "Convertir";
        loadBtn.disabled = value;
        urlInput.readOnly = value;
        form.querySelectorAll('input[name="quality"], input[name="format"]').forEach((r) => (r.disabled = value));
    }

    function resetPanel() {
        $("done").hidden = true;
        fault("");
        setMeter(0);
        setLamp("audio", null);
        setLamp("mp3", null);
    }

    function base64ToBytes(b64) {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return bytes;
    }

    function saveFile(blob, filename) {
        if (fileUrl) URL.revokeObjectURL(fileUrl);
        fileUrl = URL.createObjectURL(blob);
        const again = $("again");
        again.href = fileUrl;
        again.download = filename;
        again.click();
    }

    // ------------------------------------------------------------- network

    async function getJSON(url, options) {
        let res;
        try {
            res = await fetch(url, options);
        } catch {
            throw new Error("No hay conexión con el servidor. Revisa tu internet e inténtalo de nuevo.");
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "Algo salió mal. Inténtalo de nuevo.");
        return data;
    }

    // Calls onMessage for every JSON line of a streamed response.
    async function readLines(res, onMessage) {
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
            const { value, done } = await reader.read();
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
            let nl;
            while ((nl = buffer.indexOf("\n")) >= 0) {
                const line = buffer.slice(0, nl).trim();
                buffer = buffer.slice(nl + 1);
                if (line) onMessage(JSON.parse(line));
            }
            if (done) return;
        }
    }

    async function loadInfo() {
        const url = urlInput.value.trim();
        if (!url || busy) return null;
        if (info && infoFor === url) return info;

        const ticket = ++infoRequest;
        resetPanel();
        info = null;
        setLamp("link", "is-busy");
        setDisplay("busy", "Buscando", "Leyendo el video…", "Un momento");
        status("Consultando YouTube…");
        try {
            const data = await getJSON("/api/info?url=" + encodeURIComponent(url));
            if (ticket !== infoRequest) return null;
            info = data;
            infoFor = url;
            setLamp("link", "is-on");
            showInfo();
            status("Video encontrado. Elige la calidad y pulsa Convertir.");
            return info;
        } catch (err) {
            if (ticket !== infoRequest) return null;
            setLamp("link", "is-fault");
            setDisplay("error", "Sin señal", "No se pudo leer el enlace");
            status("");
            fault(err.message);
            return null;
        }
    }

    async function convert() {
        if (busy) return;
        if (!urlInput.value.trim()) {
            fault("Pega un enlace de YouTube para empezar.");
            urlInput.focus();
            return;
        }
        if (!(await loadInfo())) return;

        const fmt = format();
        const label = qualityLabel();
        resetPanel();
        setBusy(true);
        setLamp("audio", "is-busy");
        showInfo("Grabando", "busy");
        status(fmt === "mp3" ? "Descargando audio…" : "Descargando video…");

        const parts = [];
        let meta = null;
        let received = 0;
        let finished = false;

        function onMessage(msg) {
            if (msg.type === "progress") {
                if (msg.stage === "convert") {
                    setLamp("audio", "is-on");
                    setLamp("mp3", "is-busy");
                    status(fmt === "mp3" ? `Convirtiendo a MP3 · ${label}…` : `Uniendo video y audio en MP4 · ${label}…`);
                    setMeter(msg.progress, { converting: true });
                } else {
                    setMeter(msg.progress);
                }
            } else if (msg.type === "file") {
                meta = msg;
                setLamp("audio", "is-on");
                setLamp("mp3", "is-busy");
                status(`Enviando el ${fmt.toUpperCase()}…`);
                setMeter(SERVER_SHARE);
            } else if (msg.type === "chunk") {
                const bytes = base64ToBytes(msg.data);
                parts.push(bytes);
                received += bytes.length;
                setMeter(SERVER_SHARE + (1 - SERVER_SHARE) * (received / (meta?.size || received)));
            } else if (msg.type === "error") {
                throw new Error(msg.message);
            } else if (msg.type === "done") {
                finished = true;
            }
        }

        try {
            let res;
            try {
                res = await fetch("/api/convert", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url: infoFor, format: fmt, quality: tier() }),
                });
            } catch {
                throw new Error("No hay conexión con el servidor. Revisa tu internet e inténtalo de nuevo.");
            }
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.error || "El servidor no pudo convertir el video. Inténtalo de nuevo.");
            }
            await readLines(res, onMessage);
            if (!finished || !meta) {
                throw new Error("La conexión se cortó antes de terminar. Prueba con un video más corto o inténtalo de nuevo.");
            }

            setMeter(1);
            setLamp("mp3", "is-on");
            showInfo("Listo", "ready");
            updateReadouts(received);
            status(`Listo: ${meta.filename}`);
            $("done").hidden = false;
            saveFile(new Blob(parts, { type: meta.mimetype || "application/octet-stream" }), meta.filename);
        } catch (err) {
            setLamp("audio", lamps.audio.classList.contains("is-on") ? "is-on" : "is-fault");
            setLamp("mp3", "is-fault");
            setMeter(Number(ladder.getAttribute("aria-valuenow")) / 100, { fault: true });
            showInfo("Fallo", "error");
            status("");
            fault(err.message);
        } finally {
            setBusy(false);
        }
    }

    // -------------------------------------------------------------- events

    form.addEventListener("submit", (e) => {
        e.preventDefault();
        convert();
    });

    loadBtn.addEventListener("click", loadInfo);

    form.querySelectorAll('input[name="quality"]').forEach((radio) =>
        radio.addEventListener("change", () => updateReadouts()));
    form.querySelectorAll('input[name="format"]').forEach((radio) =>
        radio.addEventListener("change", applyFormat));

    let debounce;
    urlInput.addEventListener("input", () => {
        clearTimeout(debounce);
        if (urlInput.value.trim() !== infoFor) {
            info = null;
            setLamp("link", null);
        }
        debounce = setTimeout(() => {
            if (/youtu\.?be/i.test(urlInput.value)) loadInfo();
        }, 450);
    });

    $("reset").addEventListener("click", () => {
        info = null;
        infoFor = "";
        urlInput.value = "";
        resetPanel();
        setLamp("link", null);
        setDisplay("idle", "Sin señal", "Pega un enlace para empezar", "El video aparecerá aquí");
        updateReadouts();
        status("Listo para recibir un enlace.");
        urlInput.focus();
    });

    thumb.addEventListener("error", () => { thumb.hidden = true; });

    // Initial state (a server-rendered error from the no-JS fallback keeps its message).
    if (!$("fault").hidden) display.dataset.state = "error";
    applyFormat(); // the browser may restore MP4 after a reload
    setMeter(0);
})();
