(() => {
    const app = document.getElementById("reception-app");
    const form = document.getElementById("chat-form");
    const messageInput = document.getElementById("chat-message");
    const sendButton = document.getElementById("chat-send");
    const resetButton = document.getElementById("chat-reset");
    const retryButton = document.getElementById("chat-retry");
    const confirmation = document.getElementById("chat-confirmation");
    const answerButtons = confirmation.querySelectorAll("button");
    const log = document.getElementById("chat-log");
    const messages = document.getElementById("chat-messages");
    const status = document.getElementById("chat-status");
    const language = app.dataset.language;
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
            languageChanged: "The conversation language changed. Start over to continue in English.",
            reference: "Booking reference",
        },
        pl: {
            guest: "Ty",
            reception: "Recepcja",
            connecting: "Łączenie z recepcją…",
            waiting: "Oczekiwanie na odpowiedź recepcji…",
            connectionError: "Połączenie zostało przerwane. Połącz się ponownie, aby kontynuować.",
            reload: "Odśwież stronę, aby połączyć się ponownie z recepcją.",
            updated: "Rozmowa została zaktualizowana. Postępuj zgodnie z najnowszą odpowiedzią.",
            languageChanged: "Język rozmowy został zmieniony. Wybierz „Zacznij od nowa”, aby kontynuować po polsku.",
            reference: "Numer rezerwacji",
        },
    }[language];

    let revision = null;
    let stage = null;
    let pending = false;
    let connected = false;

    function updateControls() {
        const ready = connected && !pending;
        const canSend = ready && !terminalStages.has(stage);
        resetButton.disabled = pending;
        retryButton.disabled = pending;
        messageInput.disabled = !canSend;
        sendButton.disabled = !canSend;
        confirmation.hidden = stage !== "confirm";
        answerButtons.forEach((button) => {
            button.disabled = !canSend;
        });
    }

    function appendMessage(text, role, reference) {
        const item = document.createElement("li");
        item.className = `message message--${role}`;
        const speaker = document.createElement("span");
        speaker.className = "sr-only";
        speaker.textContent = `${labels[role]}: `;
        const body = document.createElement("p");
        body.textContent = text;
        item.append(speaker, body);

        if (reference && !text.includes(reference)) {
            const referenceLine = document.createElement("p");
            referenceLine.className = "booking-reference";
            referenceLine.textContent = `${labels.reference}: ${reference}`;
            item.append(referenceLine);
        }

        messages.append(item);
        log.scrollTop = log.scrollHeight;
    }

    async function requestChat(payload) {
        if (pending) return;
        const previousFocus = document.activeElement;
        const focusComposer = form.contains(previousFocus) || confirmation.contains(previousFocus);
        let reply = null;
        let failure = null;

        pending = true;
        retryButton.hidden = true;
        status.textContent = payload.message ? labels.waiting : labels.connecting;
        updateControls();
        form.dispatchEvent(new CustomEvent("reception:request", { detail: { reset: payload.reset === true } }));

        try {
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

            if (response.status === 403) {
                failure = labels.reload;
                return;
            }

            const result = await response.json();
            const stale = response.status === 409 && result.stale === true;
            if ((!response.ok && !stale) || typeof result.reply !== "string" || !result.revision || !result.stage) {
                throw new Error("Invalid reception response");
            }
            if (stale && result.language !== language) {
                failure = labels.languageChanged;
                return;
            }

            if (payload.start || payload.reset) messages.replaceChildren();
            if (payload.message && !stale) appendMessage(payload.message, "guest");
            appendMessage(result.reply, "reception", result.reference);
            messageInput.value = "";
            stage = result.stage;
            revision = result.revision;
            connected = true;
            status.textContent = stale ? labels.updated : "";
            reply = result.reply;
        } catch {
            failure = labels.connectionError;
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
        }
    }

    function sendMessage(message) {
        if (!connected || pending || terminalStages.has(stage) || !message) return;
        if (language === "pl" && (stage === "welcome" || stage === "check_in")) {
            const selection = message.toLowerCase().replace(/[.!?]+$/, "").trim();
            if (["english", "2", "dwa", "angielski", "po angielsku"].includes(selection)) {
                window.location.assign(app.dataset.englishUrl);
                return;
            }
        }
        return requestChat({ message, revision });
    }

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
