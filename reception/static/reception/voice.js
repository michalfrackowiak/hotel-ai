(() => {
    const button = document.getElementById("start-voice");
    const status = document.getElementById("voice-status");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-message");
    const recognitionType = window.SpeechRecognition || window.webkitSpeechRecognition;
    const synthesis = window.speechSynthesis;
    const polish = document.documentElement.lang === "pl";
    const locale = polish ? "pl-PL" : "en-US";
    const copy = polish ? {
        start: "Włącz rozmowę głosową",
        stop: "Zakończ rozmowę głosową",
        unavailable: "Ta przeglądarka nie obsługuje rozmów głosowych. Skorzystaj z czatu.",
        listening: "Słucham…",
        waiting: "Czekam na odpowiedź…",
        speaking: "Recepcja odpowiada…",
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
        speaking: "Reception is speaking…",
        permission: "Allow microphone access to use voice booking.",
        silence: "No response was heard. Try again or use chat.",
        error: "Voice booking was interrupted. Try again or use chat.",
        finished: "Voice booking ended.",
    };

    if (!window.isSecureContext || !recognitionType || !synthesis || !window.SpeechSynthesisUtterance) {
        status.textContent = copy.unavailable;
        return;
    }

    let active = false;
    let recognition = null;
    let speech = null;
    let stage = null;

    function pauseAudio() {
        const current = recognition;
        recognition = null;
        if (current) current.abort();
        if (speech) {
            speech = null;
            synthesis.cancel();
        }
    }

    function stop(message) {
        active = false;
        pauseAudio();
        button.disabled = stage === "completed";
        button.textContent = copy.start;
        button.setAttribute("aria-pressed", "false");
        status.textContent = message;
    }

    function transcribedMessage(text) {
        if (stage !== "email") return text;
        const normalized = text.toLowerCase()
            .replace(/\s+(?:at|małpa)\s+/gu, "@")
            .replace(/\s+(?:dot|kropka)\s+/gu, ".");
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

    function speak(reply) {
        const current = new SpeechSynthesisUtterance(reply);
        current.lang = locale;
        const voice = synthesis.getVoices().find((entry) =>
            entry.lang.toLowerCase().split("-")[0] === locale.toLowerCase().split("-")[0]);
        if (voice) current.voice = voice;
        speech = current;
        current.onend = () => {
            if (speech !== current) return;
            speech = null;
            if (stage === "completed") stop(copy.finished);
            else listen();
        };
        current.onerror = () => {
            if (speech === current) stop(copy.error);
        };
        status.textContent = copy.speaking;
        synthesis.speak(current);
    }

    button.addEventListener("click", () => {
        if (active) {
            stop(copy.finished);
        } else if (!input.disabled && stage !== "completed") {
            active = true;
            button.textContent = copy.stop;
            button.setAttribute("aria-pressed", "true");
            listen();
        }
    });
    function waitForReply() {
        if (!active) return;
        pauseAudio();
        status.textContent = copy.waiting;
    }
    form.addEventListener("submit", waitForReply, { capture: true });
    document.getElementById("chat-confirmation").addEventListener("click", waitForReply, { capture: true });
    document.getElementById("chat-reset").addEventListener("click", () => {
        if (active) stop(copy.finished);
    });
    form.addEventListener("reception:reply", (event) => {
        stage = event.detail.stage;
        button.disabled = !active && stage === "completed";
        if (active) {
            pauseAudio();
            speak(event.detail.reply);
        }
    });
    form.addEventListener("reception:error", () => {
        if (active) stop(copy.error);
    });
    document.addEventListener("visibilitychange", () => {
        if (document.hidden && active) stop(copy.finished);
    });
    window.addEventListener("pagehide", () => {
        if (active) stop(copy.finished);
    });
})();
