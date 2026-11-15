(() => {
    const app = document.getElementById("reception-app");
    const form = document.getElementById("chat-form");
    const messageInput = document.getElementById("chat-message");
    const sendButton = document.getElementById("chat-send");
    const resetButton = document.getElementById("chat-reset");
    const retryButton = document.getElementById("chat-retry");
    const confirmation = document.getElementById("chat-confirmation");
    const languageChoice = document.getElementById("chat-language");
    const delivery = document.getElementById("chat-delivery");
    const quickReplies = [confirmation, languageChoice, delivery];
    const answerButtons = quickReplies.flatMap((group) => Array.from(group.querySelectorAll("button")));
    const log = document.getElementById("chat-log");
    const messages = document.getElementById("chat-messages");
    const status = document.getElementById("chat-status");
    const footer = form.closest(".chat-footer");
    let language = app.dataset.language;
    const terminalStages = new Set(["completed", "cancelled"]);
    const labels = {
        en: {
            guest: "You",
            reception: "Reception",
            connecting: "Connecting to reception…",
            waiting: "Waiting for reception…",
            connectionError: "The connection was interrupted. Reconnect to continue.",
            reload: "Refresh this page to reconnect to reception.",
            updated: "The conversation was updated. Follow the latest reply.",
            reference: "Booking reference",
            staff: "Reception staff",
            handoverWaiting: "Waiting for reception staff…",
            handoverConnected: "You’re chatting with reception staff.",
            handoverClosed: "Reception staff closed this chat.",
            handoverOffline: "Staff chat is disconnected. Trying again…",
            placeholder: "Type a message…",
            input: "Your message",
            send: "Send",
            reset: "Start over",
            retry: "Reconnect",
            yes: "Yes, confirm",
            no: "No",
        },
        pl: {
            guest: "Ty",
            reception: "Recepcja",
            connecting: "Łączenie z recepcją…",
            waiting: "Oczekiwanie na odpowiedź recepcji…",
            connectionError: "Połączenie zostało przerwane. Połącz się ponownie, aby kontynuować.",
            reload: "Odśwież stronę, aby połączyć się ponownie z recepcją.",
            updated: "Rozmowa została zaktualizowana. Postępuj zgodnie z najnowszą odpowiedzią.",
            reference: "Numer rezerwacji",
            staff: "Pracownik recepcji",
            handoverWaiting: "Oczekiwanie na pracownika recepcji…",
            handoverConnected: "Rozmawiasz z pracownikiem recepcji.",
            handoverClosed: "Recepcja zakończyła tę rozmowę.",
            handoverOffline: "Połączenie z recepcją zostało przerwane. Ponawiam próbę…",
            placeholder: "Napisz wiadomość…",
            input: "Twoja wiadomość",
            send: "Wyślij",
            reset: "Od nowa",
            retry: "Połącz ponownie",
            yes: "Tak, potwierdzam",
            no: "Nie",
        },
    };

    let revision = null;
    let stage = null;
    let pending = false;
    let connected = false;
    let lastReply = null;
    let idleTimer = null;
    let voiceActive = false;
    let pollTimer = null;
    let handoverClosed = false;
    let staffReplied = false;
    let cursor = 0;

    function updateControls() {
        const copy = labels[language];
        const ready = connected && !pending;
        const canSend = ready && !terminalStages.has(stage) && !handoverClosed;
        footer.lang = language;
        resetButton.lang = language;
        resetButton.disabled = pending;
        retryButton.disabled = pending;
        messageInput.disabled = !canSend;
        messageInput.placeholder = copy.placeholder;
        messageInput.labels[0].textContent = copy.input;
        sendButton.disabled = !canSend;
        sendButton.firstElementChild.textContent = copy.send;
        resetButton.textContent = copy.reset;
        retryButton.textContent = copy.retry;
        languageChoice.hidden = language !== "pl" || stage !== "check_in";
        confirmation.hidden = stage !== "confirm";
        const [yes, no] = confirmation.querySelectorAll("button");
        yes.textContent = copy.yes;
        yes.dataset.answer = language === "pl" ? "Tak" : "Yes";
        no.textContent = copy.no;
        no.dataset.answer = copy.no;
        delivery.hidden = stage !== "delivery";
        answerButtons.forEach((button) => {
            button.disabled = !canSend;
        });
    }

    function appendMessage(text, role, reference) {
        const copy = labels[language];
        const item = document.createElement("li");
        item.className = `message message--${role === "staff" ? "reception" : role}`;
        item.lang = language;
        const speaker = document.createElement("span");
        speaker.className = "sr-only";
        speaker.textContent = `${copy[role]}: `;
        const body = document.createElement("p");
        body.textContent = text;
        item.append(speaker, body);

        if (reference && !text.includes(reference)) {
            const referenceLine = document.createElement("p");
            referenceLine.className = "booking-reference";
            referenceLine.textContent = `${copy.reference}: ${reference}`;
            item.append(referenceLine);
        }

        messages.append(item);
        log.scrollTop = log.scrollHeight;
    }

    async function fetchChat(payload) {
        const response = await fetch(form.action, {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": form.elements.csrfmiddlewaretoken.value,
            },
            body: JSON.stringify({ language, ...payload }),
        });
        if (response.status === 403) return null;
        const result = await response.json();
        if ((!response.ok && !result.stale) || typeof result.reply !== "string" || !result.revision || !result.stage) {
            throw new Error("Invalid reception response");
        }
        return result;
    }

    function appendHandover(result) {
        for (const entry of result.handover_messages) {
            if (entry.id <= cursor) continue;
            appendMessage(entry.text, entry.staff ? "staff" : "guest");
            staffReplied ||= entry.staff;
            cursor = entry.id;
        }
        handoverClosed = result.handover_closed;
        const copy = labels[language];
        status.textContent = handoverClosed ? copy.handoverClosed : (staffReplied ? copy.handoverConnected : copy.handoverWaiting);
    }

    function schedulePoll() {
        clearTimeout(pollTimer);
        if (connected && stage === "handover") pollTimer = setTimeout(pollHandover, 4000);
    }

    async function pollHandover() {
        if (stage !== "handover") return;
        if (pending || document.hidden) {
            schedulePoll();
            return;
        }
        const previous = revision;
        try {
            const result = await fetchChat({ poll: true, revision, cursor });
            if (pending || revision !== previous) return;
            if (!result || result.stage !== "handover" || result.language !== language) {
                await requestChat({ start: true, language: result?.language || language });
                return;
            }
            revision = result.revision;
            appendHandover(result);
            updateControls();
        } catch {
            if (stage === "handover") status.textContent = labels[language].handoverOffline;
        } finally {
            schedulePoll();
        }
    }

    async function requestChat(payload) {
        if (pending) return;
        clearTimeout(idleTimer);
        const previousFocus = document.activeElement;
        const focusComposer = form.contains(previousFocus) || quickReplies.some((group) => group.contains(previousFocus));
        let reply = null;
        let failure = null;

        pending = true;
        retryButton.hidden = true;
        status.textContent = payload.message ? labels[language].waiting : labels[language].connecting;
        updateControls();
        form.dispatchEvent(new CustomEvent("reception:request", { detail: { reset: payload.reset === true } }));

        try {
            const result = await fetchChat(payload);
            if (!result) {
                failure = labels[language].reload;
                return;
            }
            if (payload.start || payload.reset) {
                messages.replaceChildren();
                cursor = 0;
                lastReply = null;
                staffReplied = false;
            }
            const wasHandover = stage === "handover";
            if (payload.message && !result.stale && !wasHandover) {
                appendMessage(payload.message, "guest");
                if (result.stage === "handover" && result.handover_messages.length) {
                    cursor = result.handover_messages[0].id;
                }
            }
            if (result.language !== language) {
                language = result.language;
                const address = new URL(window.location.href);
                address.searchParams.set("lang", language);
                history.replaceState(null, "", address);
            }
            if (!wasHandover || result.stage !== "handover" || lastReply !== result.reply) {
                appendMessage(result.reply, "reception", result.reference);
                reply = result.reply;
            }
            lastReply = result.reply;
            if (!result.stale) messageInput.value = "";
            stage = result.stage;
            revision = result.revision;
            connected = true;
            status.textContent = result.stale ? labels[language].updated : "";
            if (stage === "handover") appendHandover(result);
            else handoverClosed = false;
        } catch {
            failure = labels[language].connectionError;
            retryButton.hidden = false;
        } finally {
            if (failure !== null) {
                connected = false;
                status.textContent = failure;
            }
            pending = false;
            updateControls();
            if (focusComposer && document.activeElement === document.body) {
                (messageInput.disabled ? resetButton : messageInput).focus();
            }
            if (reply !== null) {
                form.dispatchEvent(new CustomEvent("reception:reply", {
                    detail: { reply, stage, language, revision },
                }));
            }
            if (failure !== null) {
                form.dispatchEvent(new CustomEvent("reception:error", {
                    detail: { message: failure, language },
                }));
            }
            schedulePoll();
        }
    }

    function sendMessage(message) {
        if (!connected || pending || terminalStages.has(stage) || !message) return;
        if (handoverClosed) return;
        return requestChat({ message, revision, ...(voiceActive ? { voice: true } : {}) });
    }

    function checkInTimeout() {
        if (connected && !pending && stage === "check_in") {
            requestChat({ timeout: true, revision, ...(voiceActive ? { voice: true } : {}) });
        }
    }

    function trackComposerSilence() {
        clearTimeout(idleTimer);
        if (!voiceActive && stage === "check_in" && !messageInput.value.trim()) {
            idleTimer = setTimeout(checkInTimeout, 10000);
        }
    }

    messageInput.addEventListener("focus", trackComposerSilence);
    messageInput.addEventListener("input", trackComposerSilence);
    messageInput.addEventListener("blur", () => clearTimeout(idleTimer));
    form.addEventListener("reception:voice-session", (event) => {
        voiceActive = event.detail.active;
        clearTimeout(idleTimer);
    });
    form.addEventListener("reception:voice-silence", checkInTimeout);

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        sendMessage(messageInput.value.trim());
    });

    answerButtons.forEach((button) => {
        button.addEventListener("click", () => sendMessage(button.dataset.answer));
    });
    resetButton.addEventListener("click", () => requestChat({ reset: true }));
    retryButton.addEventListener("click", () => requestChat({ start: true }));

    requestChat({ start: true });
})();
