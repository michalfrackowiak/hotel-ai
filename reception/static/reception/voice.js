(() => {
    const button = document.getElementById("start-voice");
    const status = document.getElementById("voice-status");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-message");
    const recognitionType = window.SpeechRecognition || window.webkitSpeechRecognition;
    const synthesis = window.speechSynthesis;
    const canSpeak = Boolean(synthesis && window.SpeechSynthesisUtterance);
    const audioUrl = button.dataset.audioUrl;
    const audioContextType = window.AudioContext || window.webkitAudioContext;
    const canPlayPiper = Boolean(audioUrl && audioContextType);
    const polish = document.documentElement.lang === "pl";
    const locale = polish ? "pl-PL" : "en-US";
    const copy = polish ? {
        start: "Włącz rozmowę głosową",
        stop: "Zakończ rozmowę głosową",
        unavailable: "Ta przeglądarka nie obsługuje rozmów głosowych. Skorzystaj z czatu.",
        listening: "Słucham…",
        waiting: "Czekam na odpowiedź…",
        preparing: "Recepcja przygotowuje odpowiedź głosową…",
        speaking: "Recepcja odpowiada…",
        fallback: "Piper jest niedostępny. Używam głosu przeglądarki…",
        stale: "Rozmowa zmieniła się w innej karcie. Odśwież stronę, aby wznowić rozmowę głosową.",
        permission: "Zezwól na dostęp do mikrofonu, aby rozmawiać głosowo.",
        silence: "Nie usłyszano odpowiedzi. Spróbuj ponownie lub skorzystaj z czatu.",
        error: "Rozmowa głosowa została przerwana. Spróbuj ponownie lub skorzystaj z czatu.",
        finished: "Rozmowa głosowa zakończona.",
    } : {
        start: "Start voice booking",
        stop: "End voice booking",
        unavailable: "Voice booking is unavailable in this browser. You can still use chat.",
        listening: "Listening…",
        waiting: "Waiting for a reply…",
        preparing: "Preparing the spoken reply…",
        speaking: "Reception is speaking…",
        fallback: "Piper is unavailable. Using the browser voice…",
        stale: "The conversation changed in another tab. Refresh to resume voice booking.",
        permission: "Allow microphone access to use voice booking.",
        silence: "No response was heard. Try again or use chat.",
        error: "Voice booking was interrupted. Try again or use chat.",
        finished: "Voice booking ended.",
    };

    if (!window.isSecureContext || !recognitionType || (!canPlayPiper && !canSpeak)) {
        status.textContent = copy.unavailable;
        return;
    }

    let active = false;
    let recognition = null;
    let speech = null;
    let playback = null;
    let audioContext = null;
    let stage = null;
    let lastReply = null;

    function clearPlayback() {
        if (!playback) return;
        const current = playback;
        playback = null;
        current.controller.abort();
        if (current.source) {
            current.source.onended = null;
            current.source.stop();
            current.source.disconnect();
        }
    }

    function pauseAudio() {
        const current = recognition;
        recognition = null;
        if (current) current.abort();
        clearPlayback();
        if (speech) {
            speech = null;
            synthesis.cancel();
        }
    }

    function stop(message) {
        active = false;
        pauseAudio();
        button.disabled = input.disabled || stage === "completed";
        button.textContent = copy.start;
        button.setAttribute("aria-pressed", "false");
        status.textContent = message;
    }

    function transcribedMessage(text) {
        if (stage !== "email") return text;
        const normalized = text
            .replace(/\s+(?:at|małpa)\s+/giu, "@")
            .replace(/\s+(?:dot|kropka)\s+/giu, ".");
        return normalized.includes("@") ? normalized.replace(/\s/g, "") : text;
    }

    function listen() {
        if (!active) return;
        const current = new recognitionType();
        recognition = current;
        current.lang = locale;
        current.continuous = false;
        current.interimResults = false;
        let transcript = "";

        current.onresult = (event) => {
            if (recognition !== current) return;
            transcript = event.results[event.resultIndex][0].transcript.trim();
        };
        current.onerror = (event) => {
            if (recognition !== current) return;
            const permissionDenied = ["not-allowed", "service-not-allowed"].includes(event.error);
            if (permissionDenied) stop(copy.permission);
            else stop(event.error === "no-speech" ? copy.silence : copy.error);
        };
        current.onend = () => {
            if (recognition !== current) return;
            recognition = null;
            if (!transcript) {
                stop(copy.silence);
                return;
            }
            if (input.disabled || transcript.length > input.maxLength) {
                stop(copy.error);
                return;
            }
            input.value = transcribedMessage(transcript);
            status.textContent = copy.waiting;
            form.requestSubmit();
        };
        try {
            current.start();
            status.textContent = copy.listening;
        } catch {
            recognition = null;
            stop(copy.error);
        }
    }

    function finishReply() {
        if (stage === "completed") stop(copy.finished);
        else listen();
    }

    function speakWithBrowser(reply, message) {
        if (!canSpeak) {
            stop(copy.error);
            return;
        }
        const current = new SpeechSynthesisUtterance(reply);
        current.lang = locale;
        const voice = synthesis.getVoices().find((entry) =>
            entry.lang.toLowerCase().split("-")[0] === locale.toLowerCase().split("-")[0]);
        if (voice) current.voice = voice;
        speech = current;
        current.onend = () => {
            if (speech !== current) return;
            speech = null;
            finishReply();
        };
        current.onerror = () => {
            if (speech === current) stop(copy.error);
        };
        status.textContent = message;
        synthesis.speak(current);
    }

    async function speak(turn) {
        const context = audioContext;
        if (!context) {
            speakWithBrowser(turn.reply, audioUrl ? copy.fallback : copy.speaking);
            return;
        }

        const current = { controller: new AbortController() };
        playback = current;
        status.textContent = copy.preparing;

        function fallback() {
            if (playback !== current) return;
            clearPlayback();
            speakWithBrowser(turn.reply, copy.fallback);
        }

        try {
            const response = await fetch(audioUrl, {
                method: "POST",
                credentials: "same-origin",
                cache: "no-store",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": form.elements.csrfmiddlewaretoken.value,
                },
                body: JSON.stringify({ revision: turn.revision }),
                signal: current.controller.signal,
            });
            if (playback !== current) return;
            if ([404, 409].includes(response.status)) {
                stop(copy.stale);
                button.disabled = true;
                return;
            }
            const contentType = response.headers.get("Content-Type") || "";
            if (!response.ok || !/^audio\/(x-)?wav(?:;|$)/i.test(contentType)) {
                fallback();
                return;
            }

            const bytes = await response.arrayBuffer();
            if (playback !== current) return;
            const buffer = await context.decodeAudioData(bytes);
            if (playback !== current) return;
            if (context.state !== "running") {
                fallback();
                return;
            }
            const source = context.createBufferSource();
            source.buffer = buffer;
            source.connect(context.destination);
            source.onended = () => {
                if (playback !== current) return;
                clearPlayback();
                finishReply();
            };
            source.start();
            current.source = source;
            status.textContent = copy.speaking;
        } catch {
            fallback();
        }
    }

    button.addEventListener("click", () => {
        if (active) {
            stop(copy.finished);
        } else if (!input.disabled && lastReply && stage !== "completed") {
            active = true;
            button.textContent = copy.stop;
            button.setAttribute("aria-pressed", "true");
            if (canPlayPiper) {
                try {
                    // Resume playback during the click so later replies can play automatically.
                    audioContext ??= new audioContextType();
                    audioContext.resume().catch(() => {});
                } catch {
                    audioContext = null;
                }
            }
            speak(lastReply);
        }
    });
    form.addEventListener("reception:request", (event) => {
        if (!active || event.detail.reset) {
            if (active) stop(copy.finished);
            button.disabled = true;
            return;
        }
        pauseAudio();
        status.textContent = copy.waiting;
    });
    form.addEventListener("reception:reply", (event) => {
        stage = event.detail.stage;
        lastReply = event.detail;
        button.disabled = !active && stage === "completed";
        if (active) {
            pauseAudio();
            speak(lastReply);
        }
    });
    form.addEventListener("reception:error", () => {
        if (active) stop(copy.error);
        button.disabled = true;
    });
    document.addEventListener("visibilitychange", () => {
        if (document.hidden && active) stop(copy.finished);
    });
    window.addEventListener("pagehide", () => {
        if (active) stop(copy.finished);
    });
})();
