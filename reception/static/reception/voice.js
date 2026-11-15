(() => {
    const button = document.getElementById("start-voice");
    const status = document.getElementById("voice-status");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-message");
    const recognitionType = window.SpeechRecognition || window.webkitSpeechRecognition;
    const synthesis = window.speechSynthesis;
    const canSpeak = Boolean(synthesis && window.SpeechSynthesisUtterance);
    const audioUrl = button.dataset.audioUrl;
    const transcribeUrl = button.dataset.transcribeUrl;
    const audioContextType = window.AudioContext || window.webkitAudioContext;
    const offlineContextType = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const canPlayPiper = Boolean(audioUrl && audioContextType);
    const canRecord = Boolean(transcribeUrl && audioContextType && offlineContextType &&
        window.AudioWorkletNode && navigator.mediaDevices?.getUserMedia);
    const terminalStages = new Set(["completed", "cancelled", "handover"]);
    const silenceLimits = { welcome: 2000, check_in: 10000 };
    const polishCopy = {
        start: "Włącz rozmowę głosową",
        stop: "Zakończ rozmowę głosową",
        unavailable: "Ta przeglądarka nie obsługuje rozmów głosowych. Skorzystaj z czatu.",
        listening: "Słucham…",
        waiting: "Czekam na odpowiedź…",
        preparing: "Recepcja przygotowuje odpowiedź głosową…",
        speaking: "Recepcja odpowiada…",
        fallback: "Piper jest niedostępny. Używam głosu przeglądarki…",
        stale: "Rozmowa zmieniła się w innej karcie. Odśwież stronę, aby wznowić rozmowę głosową.",
        connecting: "Włączam mikrofon…",
        permission: "Zezwól na dostęp do mikrofonu, aby rozmawiać głosowo.",
        microphone: "Nie można użyć mikrofonu. Sprawdź, czy jest podłączony, lub skorzystaj z czatu.",
        transcribing: "Rozpoznaję odpowiedź…",
        recognition: "Rozpoznawanie mowy jest niedostępne. Spróbuj ponownie lub skorzystaj z czatu.",
        silence: "Nie usłyszano odpowiedzi. Spróbuj ponownie lub skorzystaj z czatu.",
        error: "Rozmowa głosowa została przerwana. Spróbuj ponownie lub skorzystaj z czatu.",
        finished: "Rozmowa głosowa zakończona.",
    };
    const englishCopy = {
        start: "Start voice booking",
        stop: "End voice booking",
        unavailable: "Voice booking is unavailable in this browser. You can still use chat.",
        listening: "Listening…",
        waiting: "Waiting for a reply…",
        preparing: "Preparing the spoken reply…",
        speaking: "Reception is speaking…",
        fallback: "Piper is unavailable. Using the browser voice…",
        stale: "The conversation changed in another tab. Refresh to resume voice booking.",
        connecting: "Opening the microphone…",
        permission: "Allow microphone access to use voice booking.",
        microphone: "The microphone is unavailable. Check that it is connected or use chat.",
        transcribing: "Transcribing your answer…",
        recognition: "Speech recognition is unavailable. Try again or use chat.",
        silence: "No response was heard. Try again or use chat.",
        error: "Voice booking was interrupted. Try again or use chat.",
        finished: "Voice booking ended.",
    };
    let locale = document.documentElement.lang === "pl" ? "pl-PL" : "en-US";
    let copy = locale === "pl-PL" ? polishCopy : englishCopy;

    if (!window.isSecureContext || (!canRecord && !recognitionType) || (!canPlayPiper && !canSpeak)) {
        status.textContent = copy.unavailable;
        return;
    }
    const privacy = document.getElementById("voice-privacy");
    if (privacy) privacy.hidden = !canRecord;

    let active = false;
    let recognition = null;
    let speech = null;
    let playback = null;
    let nextAudio = null;
    let audioContext = null;
    let microphone = null;
    let microphoneModule = null;
    let recording = null;
    let silenceTimer = null;
    let stage = null;
    let lastReply = null;
    let voiceWelcome = false;
    let voicePart = 0;

    function currentSegment() {
        return lastReply?.voiceParts?.[voicePart];
    }

    function canStart() {
        return !input.disabled && Boolean(lastReply) && stage !== "delivery_pending" &&
            (!terminalStages.has(stage) || (stage === "handover" && voiceWelcome));
    }

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
        clearTimeout(silenceTimer);
        silenceTimer = null;
        const current = recognition;
        recognition = null;
        if (current) current.abort();
        clearRecording();
        clearPlayback();
        nextAudio?.controller.abort();
        nextAudio = null;
        if (speech) {
            speech = null;
            synthesis.cancel();
        }
    }

    function stop(message) {
        active = false;
        form.dispatchEvent(new CustomEvent("reception:voice-session", { detail: { active } }));
        pauseAudio();
        const stream = microphone?.stream;
        microphone = null;
        if (stream) {
            for (const track of stream.getTracks()) track.stop();
        }
        button.disabled = !canStart();
        button.textContent = copy.start;
        button.setAttribute("aria-pressed", "false");
        status.textContent = message;
    }

    function playNextSegment() {
        if (!lastReply?.voiceParts?.[voicePart + 1]) return false;
        const prepared = nextAudio;
        nextAudio = null;
        pauseAudio();
        voicePart += 1;
        speak(lastReply, prepared);
        return true;
    }

    function handleSilence() {
        if (!active || playNextSegment()) return;
        if (stage in silenceLimits && !input.disabled) {
            form.dispatchEvent(new CustomEvent("reception:voice-silence"));
        } else {
            stop(copy.silence);
        }
    }

    function submitTranscript(text) {
        const transcript = text.trim();
        if (!transcript) {
            handleSilence();
            return;
        }
        if (input.disabled || transcript.length > input.maxLength) {
            stop(copy.error);
            return;
        }
        input.value = transcript;
        status.textContent = copy.waiting;
        form.dispatchEvent(new CustomEvent("reception:transcript", { detail: transcript }));
    }

    function listenWithBrowser(silenceLimit) {
        if (!active) return;
        const current = new recognitionType();
        recognition = current;
        current.lang = stage === "welcome" ? "en-US" : locale;
        current.continuous = false;
        current.interimResults = false;
        let transcript = "";

        current.onresult = (event) => {
            if (recognition !== current) return;
            transcript = event.results[event.resultIndex][0].transcript.trim();
            if (transcript) {
                clearTimeout(silenceTimer);
                silenceTimer = null;
            }
        };
        current.onspeechstart = () => {
            if (recognition === current) {
                clearTimeout(silenceTimer);
                silenceTimer = null;
            }
        };
        current.onerror = (event) => {
            if (recognition !== current) return;
            if (event.error === "not-allowed") stop(copy.permission);
            else if (event.error === "audio-capture") stop(copy.microphone);
            else if (event.error === "no-speech") {
                if (!silenceTimer) handleSilence();
            } else stop(copy.recognition);
        };
        current.onend = () => {
            if (recognition !== current) return;
            recognition = null;
            if (transcript || !silenceTimer) submitTranscript(transcript);
        };
        try {
            current.start();
            if (silenceLimit) silenceTimer = setTimeout(handleSilence, silenceLimit);
            status.textContent = copy.listening;
        } catch {
            recognition = null;
            stop(copy.recognition);
        }
    }

    function stopCapture(current) {
        if (!current.source) return;
        clearTimeout(current.timer);
        current.track.enabled = false;
        current.node.port.onmessage = null;
        current.node.onprocessorerror = null;
        current.source.disconnect();
        current.node.disconnect();
        current.source = null;
    }

    function clearRecording() {
        if (!recording) return;
        const current = recording;
        recording = null;
        current.controller.abort();
        stopCapture(current);
    }

    async function encodePcm(samples, inputRate) {
        const rate = 16000;
        if (inputRate !== rate) {
            const offline = new offlineContextType(1, Math.ceil(samples.length * rate / inputRate), rate);
            const source = offline.createBufferSource();
            source.buffer = offline.createBuffer(1, samples.length, inputRate);
            source.buffer.copyToChannel(samples, 0);
            source.connect(offline.destination);
            source.start();
            samples = (await offline.startRendering()).getChannelData(0);
        }
        const output = new DataView(new ArrayBuffer(samples.length * 2));
        for (let i = 0; i < samples.length; i++) {
            const sample = Math.max(-1, Math.min(1, samples[i]));
            output.setInt16(i * 2, Math.round(sample < 0 ? sample * 32768 : sample * 32767), true);
        }
        return output.buffer;
    }

    async function transcribe(current, samples) {
        if (recording !== current) return;
        stopCapture(current);
        if (!samples?.length) {
            handleSilence();
            return;
        }
        status.textContent = copy.transcribing;
        try {
            const bytes = await encodePcm(samples, audioContext.sampleRate);
            if (recording !== current) return;
            const response = await fetch(transcribeUrl, {
                method: "POST",
                credentials: "same-origin",
                cache: "no-store",
                headers: {
                    "Content-Type": "application/octet-stream",
                    "X-CSRFToken": form.elements.csrfmiddlewaretoken.value,
                    "X-Reception-Revision": current.revision,
                },
                body: bytes,
                signal: current.controller.signal,
            });
            if (recording !== current) return;
            if (response.status === 404 || response.status === 409) {
                stop(copy.stale);
                button.disabled = true;
                return;
            }
            if (!response.ok) throw new Error("Transcription request failed");
            const result = await response.json();
            if (recording === current) submitTranscript(result.text);
        } catch {
            if (recording === current) stop(copy.recognition);
        }
    }

    function listenWithMicrophone(silenceLimit) {
        const stream = microphone?.stream;
        const track = stream?.getAudioTracks()[0];
        if (!track || track.readyState !== "live") {
            stop(copy.microphone);
            return;
        }
        try {
            const node = new AudioWorkletNode(audioContext, "reception-microphone", {
                channelCount: 1,
                processorOptions: { silenceSeconds: (silenceLimit || 10000) / 1000 },
            });
            const source = audioContext.createMediaStreamSource(stream);
            const current = { source, node, track, revision: lastReply.revision, controller: new AbortController() };
            recording = current;
            current.timer = setTimeout(() => stop(copy.silence), 25000);
            node.port.onmessage = (event) => transcribe(current, event.data);
            node.onprocessorerror = () => stop(copy.microphone);
            source.connect(node).connect(audioContext.destination);
            track.enabled = true;
            status.textContent = copy.listening;
        } catch {
            stop(copy.microphone);
        }
    }

    async function openMicrophone() {
        const current = {};
        microphone = current;
        status.textContent = copy.connecting;
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            });
            if (microphone !== current) {
                for (const track of stream.getTracks()) track.stop();
                return;
            }
            current.stream = stream;
            for (const track of stream.getAudioTracks()) {
                track.enabled = false;
                track.addEventListener("ended", () => {
                    if (microphone === current) stop(copy.microphone);
                });
            }
            microphoneModule ??= audioContext.audioWorklet.addModule(button.dataset.microphoneModule);
            await microphoneModule;
            if (microphone === current) {
                current.ready = true;
                if (!input.disabled || terminalStages.has(stage) || stage === "delivery_pending") speak(lastReply);
            }
        } catch (error) {
            microphoneModule = null;
            if (microphone === current) {
                stop(["NotAllowedError", "SecurityError"].includes(error.name) ? copy.permission : copy.microphone);
            }
        }
    }

    function awaitGuest(silenceLimit = silenceLimits[stage]) {
        if (!active) return;
        if (terminalStages.has(stage)) stop(copy.finished);
        else if (stage === "delivery_pending") status.textContent = copy.waiting;
        else if (canRecord) listenWithMicrophone(silenceLimit);
        else listenWithBrowser(silenceLimit);
    }

    function finishReply() {
        const segment = currentSegment();
        if (segment?.listen) {
            awaitGuest(segment.seconds * 1000);
        } else if (segment?.seconds) {
            status.textContent = copy.waiting;
            silenceTimer = setTimeout(() => {
                if (active && !playNextSegment()) awaitGuest();
            }, segment.seconds * 1000);
        } else if (!playNextSegment()) {
            awaitGuest();
        }
    }

    function speakWithBrowser(turn, message) {
        if (!canSpeak) {
            stop(copy.error);
            return;
        }
        const parts = turn.welcomeParts || [[turn.language, turn.reply]];
        function speakPart(index) {
            const [language, reply] = parts[index];
            const current = new SpeechSynthesisUtterance(reply);
            current.lang = language === "pl" ? "pl-PL" : "en-US";
            const voice = synthesis.getVoices().find((entry) => entry.lang.toLowerCase().split("-")[0] === language);
            if (voice) current.voice = voice;
            speech = current;
            current.onstart = () => {
                if (speech === current) announceSpoken(turn, reply, voicePart + index);
            };
            current.onend = () => {
                if (speech !== current) return;
                speech = null;
                if (index + 1 < parts.length) speakPart(index + 1);
                else finishReply();
            };
            current.onerror = () => {
                if (speech === current) stop(copy.error);
            };
            synthesis.speak(current);
        }
        status.textContent = message;
        speakPart(0);
    }

    function announceSpoken(turn, text, index = voicePart) {
        form.dispatchEvent(new CustomEvent("reception:spoken", {
            detail: { revision: turn.revision, text, index },
        }));
    }

    function requestAudio(turn, part) {
        const controller = new AbortController();
        const result = fetch(audioUrl, {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": form.elements.csrfmiddlewaretoken.value,
            },
            body: JSON.stringify({ revision: turn.revision, part, voice_key: part === undefined ? undefined : turn.voiceKey }),
            signal: controller.signal,
        }).then(async (response) => {
            const contentType = response.headers.get("Content-Type") || "";
            const bytes = response.ok && /^audio\/(x-)?wav(?:;|$)/i.test(contentType) ? await response.arrayBuffer() : null;
            return { status: response.status, bytes };
        }).catch(() => ({}));
        return { controller, result };
    }

    async function speak(turn, prepared = null) {
        const segment = currentSegment();
        const spokenTurn = segment
            ? { ...turn, reply: segment.text, language: segment.language || turn.language, welcomeParts: undefined }
            : { ...turn, reply: turn.voiceReply || turn.reply };
        const context = audioContext;
        if (!canPlayPiper || !context) {
            speakWithBrowser(spokenTurn, audioUrl ? copy.fallback : copy.speaking);
            return;
        }

        const current = prepared ?? requestAudio(turn, segment ? voicePart : undefined);
        playback = current;
        status.textContent = copy.preparing;

        function fallback() {
            if (playback !== current) return;
            clearPlayback();
            speakWithBrowser(spokenTurn, copy.fallback);
        }

        try {
            const { status: responseStatus, bytes } = await current.result;
            if (playback !== current) return;
            if ([404, 409].includes(responseStatus)) {
                if (responseStatus === 409 && turn.stage === "delivery_pending") {
                    clearPlayback();
                    status.textContent = copy.waiting;
                    return;
                }
                stop(copy.stale);
                button.disabled = true;
                return;
            }
            if (!bytes) {
                fallback();
                return;
            }

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
            announceSpoken(spokenTurn, spokenTurn.reply);
            current.source = source;
            status.textContent = copy.speaking;
            if (segment && turn.voiceParts[voicePart + 1]) {
                nextAudio = requestAudio(turn, voicePart + 1);
            }
        } catch {
            fallback();
        }
    }

    button.addEventListener("click", () => {
        if (active) {
            stop(copy.finished);
        } else if (canStart()) {
            active = true;
            voicePart = 0;
            form.dispatchEvent(new CustomEvent("reception:voice-session", { detail: { active } }));
            button.textContent = copy.stop;
            button.setAttribute("aria-pressed", "true");
            if (canPlayPiper || canRecord) {
                try {
                    // Resume playback during the click so later replies can play automatically.
                    audioContext ??= new audioContextType();
                    audioContext.resume().catch(() => {});
                } catch {
                    audioContext = null;
                }
            }
            if (canRecord && audioContext) openMicrophone();
            else if (canRecord) stop(copy.microphone);
            else if (!input.disabled) speak(lastReply);
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
        voicePart = 0;
        voiceWelcome = event.detail.voiceWelcome;
        locale = event.detail.language === "pl" ? "pl-PL" : "en-US";
        copy = locale === "pl-PL" ? polishCopy : englishCopy;
        button.textContent = active ? copy.stop : copy.start;
        button.lang = event.detail.language;
        status.lang = event.detail.language;
        button.disabled = !active && !canStart();
        if (active && (!canRecord || microphone?.ready)) {
            pauseAudio();
            speak(lastReply);
        }
    });
    form.addEventListener("reception:voice-welcome", (event) => {
        voiceWelcome = event.detail.allowed;
        if (!active) button.disabled = !canStart();
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
