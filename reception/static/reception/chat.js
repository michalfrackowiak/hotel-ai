(() => {
    const app = document.getElementById("reception-app");
    const form = document.getElementById("chat-form");
    const messageInput = document.getElementById("chat-message");
    const sendButton = document.getElementById("chat-send");
    const resetButton = document.getElementById("chat-reset");
    const retryButton = document.getElementById("chat-retry");
    const confirmation = document.getElementById("chat-confirmation");
    const contactValue = document.getElementById("chat-contact-value");
    const changeDelivery = document.getElementById("chat-change-delivery");
    const typeInstead = document.getElementById("chat-type-instead");
    const voiceReview = document.getElementById("voice-answer-review");
    const voiceReviewHelp = document.getElementById("voice-answer-help");
    const voiceReviewRetry = document.getElementById("voice-answer-retry");
    const languageChoice = document.getElementById("chat-language");
    const delivery = document.getElementById("chat-delivery");
    const suggestions = document.getElementById("chat-suggestions");
    const quickReplies = [confirmation, languageChoice, delivery, suggestions];
    const answerButtons = quickReplies.flatMap((group) => Array.from(group.querySelectorAll("button")));
    const log = document.getElementById("chat-log");
    const messages = document.getElementById("chat-messages");
    const status = document.getElementById("chat-status");
    const footer = form.closest(".chat-footer");
    const staySummary = document.getElementById("booking-summary");
    const editButtons = Array.from(staySummary?.querySelectorAll("[data-edit-booking]") || []);
    const staySummaryValues = document.getElementById("booking-summary-values");
    const staySummaryTitle = document.getElementById("booking-summary-title");
    const calendar = document.getElementById("booking-date-picker");
    const dateForm = document.getElementById("booking-date-form");
    const arrival = document.getElementById("booking-check-in");
    const departure = document.getElementById("booking-check-out");
    const arrivalLabel = document.getElementById("booking-check-in-label");
    let language = app.dataset.language;
    const dedicatedVoice = app.dataset.mode === "voice";
    const legacyVoice = app.dataset.mode === "combined";
    const terminalStages = new Set(["completed", "cancelled", "delivery_pending"]);
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
            correct: "Yes, this is correct",
            change: "Change contact details",
            changeDelivery: "Change delivery method",
            typeInstead: "Type instead",
            reviewAnswer: "Review the captured answer. You can edit it before sending.",
            retryAnswer: "Say it again",
            summary: "Your stay",
            fields: { check_in: "Check-in", check_out: "Check-out", guests: "Guests", room: "Room", total_price: "Total" },
            edits: { dates: "Change dates", guests: "Change guests", room: "Change room" },
            calendar: "Choose dates",
            optionalDeparture: "Check-out (optional)",
            sendDates: "Send dates",
            invalidDates: "Choose a future or current check-in and a later check-out where required.",
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
            correct: "Tak, dane są poprawne",
            change: "Popraw dane kontaktowe",
            changeDelivery: "Zmień sposób dostawy",
            typeInstead: "Wpisz zamiast dyktować",
            reviewAnswer: "Sprawdź rozpoznaną odpowiedź. Możesz ją poprawić przed wysłaniem.",
            retryAnswer: "Powiedz ponownie",
            summary: "Twój pobyt",
            fields: { check_in: "Przyjazd", check_out: "Wyjazd", guests: "Goście", room: "Pokój", total_price: "Łącznie" },
            edits: { dates: "Zmień daty", guests: "Zmień liczbę gości", room: "Zmień pokój" },
            calendar: "Wybierz daty",
            optionalDeparture: "Wyjazd (opcjonalnie)",
            sendDates: "Wyślij daty",
            invalidDates: "Wybierz datę przyjazdu od dzisiaj oraz późniejszą datę wyjazdu, jeśli jest wymagana.",
        },
    };

    let revision = null;
    let voiceTimeoutChatRevision = null;
    let stage = null;
    let pending = false;
    let connected = false;
    let lastReply = null;
    let idleTimer = null;
    let voiceActive = false;
    let voiceWelcome = false;
    let pollTimer = null;
    let handoverClosed = false;
    let staffReplied = false;
    let cursor = 0;
    let suggestedAnswers = [];
    let quickReplyLabels = {};
    let displayedTurn = null;
    let transcriptShown = false;
    let voiceDraft = null;
    let canChangeDelivery = false;
    let stay = null;
    let picker = null;

    function displayDate(value) {
        return new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", {
            dateStyle: "medium", timeZone: "UTC",
        }).format(new Date(value + "T00:00:00Z"));
    }

    function displayStayValue(field, value) {
        if (field === "check_in" || field === "check_out") return displayDate(value);
        if (field === "total_price") {
            return new Intl.NumberFormat(language, { style: "currency", currency: "PLN" }).format(value);
        }
        return value;
    }

    function updateDepartureMinimum() {
        departure.min = picker?.departure_min || "";
        if (!picker?.arrival_min || !arrival.value || !arrival.validity.valid) return;
        const next = new Date(arrival.value + "T00:00:00Z");
        next.setUTCDate(next.getUTCDate() + 1);
        departure.min = next.toISOString().slice(0, 10);
    }

    function updateBookingControls(copy, canSend) {
        if (!staySummary) return;
        staySummary.hidden = !stay;
        staySummaryTitle.textContent = copy.summary;
        staySummaryValues.replaceChildren();
        for (const [field, title] of Object.entries(copy.fields)) {
            if (stay?.[field] === undefined) continue;
            const term = document.createElement("dt");
            term.textContent = title;
            const value = document.createElement("dd");
            value.textContent = displayStayValue(field, stay[field]);
            const item = document.createElement("div");
            item.append(term, value);
            staySummaryValues.append(item);
        }
        editButtons.forEach((button) => {
            const action = button.dataset.editBooking;
            button.hidden = !stay?.edits.includes(action);
            button.textContent = copy.edits[action];
            button.disabled = !canSend;
        });

        calendar.hidden = !picker;
        calendar.inert = !canSend;
        if (!picker) calendar.open = false;
        document.getElementById("booking-date-title").textContent = copy.calendar;
        arrivalLabel.hidden = !picker?.arrival_min;
        arrivalLabel.firstElementChild.textContent = copy.fields.check_in;
        arrival.disabled = !picker?.arrival_min;
        arrival.min = picker?.arrival_min || "";
        departure.required = picker?.departure_required === true;
        document.getElementById("booking-check-out-label").textContent =
            departure.required ? copy.fields.check_out : copy.optionalDeparture;
        document.getElementById("booking-date-send").textContent = copy.sendDates;
        dateForm.querySelector("fieldset").disabled = !canSend;
        updateDepartureMinimum();
    }

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
        languageChoice.hidden = language !== "pl" || !["welcome", "check_in"].includes(stage);
        const english = languageChoice.querySelector("button");
        english.textContent = quickReplyLabels.answer_label_choose_english || "English";
        english.lang = quickReplyLabels.answer_label_choose_english ? language : "en";
        const contactConfirmation = stage === "contact_confirm";
        confirmation.hidden = stage !== "confirm" && !contactConfirmation;
        contactValue.hidden = !contactConfirmation;
        changeDelivery.hidden = !contactConfirmation || !canChangeDelivery;
        changeDelivery.textContent = quickReplyLabels.answer_label_change_delivery || copy.changeDelivery;
        changeDelivery.disabled = !canSend;
        if (typeInstead) {
            typeInstead.hidden = !voiceActive || !["email", "phone", "contact_confirm"].includes(stage);
            typeInstead.disabled = !canSend;
            typeInstead.textContent = copy.typeInstead;
        }
        const [yes, no] = confirmation.querySelectorAll("button");
        yes.textContent = contactConfirmation
            ? quickReplyLabels.answer_label_approve_contact || copy.correct
            : quickReplyLabels.answer_label_approve_quote || copy.yes;
        yes.dataset.answer = language === "pl" ? "Tak" : "Yes";
        no.textContent = contactConfirmation
            ? quickReplyLabels.answer_label_edit_contact || copy.change
            : quickReplyLabels.answer_label_decline_quote || copy.no;
        no.dataset.answer = copy.no;
        delivery.hidden = stage !== "delivery";
        const [email, sms] = delivery.querySelectorAll("button");
        email.textContent = quickReplyLabels.answer_label_email || "E-mail";
        sms.textContent = quickReplyLabels.answer_label_sms || "SMS";
        answerButtons.forEach((button) => {
            button.disabled = !canSend;
        });
        suggestions.hidden = suggestedAnswers.length === 0 || !canSend;
        if (voiceReview) {
            const reviewing = voiceDraft !== null && canSend;
            voiceReview.hidden = !reviewing;
            voiceReviewHelp.textContent = copy.reviewAnswer;
            voiceReviewRetry.textContent = copy.retryAnswer;
            voiceReviewRetry.hidden = !voiceActive;
            if (reviewing) messageInput.setAttribute("aria-describedby", voiceReviewHelp.id);
            else messageInput.removeAttribute("aria-describedby");
        }
        updateBookingControls(copy, canSend);
        suggestions.replaceChildren(...suggestedAnswers.map((answer) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "quiet-button";
            button.textContent = answer.label;
            button.addEventListener("click", () => sendMessage(answer.text));
            return button;
        }));
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
        return item;
    }

    function displayReply(result) {
        contactValue.textContent = result.contact_candidate || "";
        canChangeDelivery = result.can_change_delivery === true;
        const item = appendMessage(result.reply, "reception", result.reference);
        displayedTurn = { revision: result.revision, result, item, spoken: new Map() };
        if (voiceActive) item.querySelector("p").textContent = "";
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
        if (legacyVoice && voiceWelcome !== Boolean(result.voice_welcome)) {
            voiceWelcome = Boolean(result.voice_welcome);
            form.dispatchEvent(new CustomEvent("reception:voice-welcome", { detail: { allowed: voiceWelcome } }));
        }
        const copy = labels[language];
        status.textContent = handoverClosed ? copy.handoverClosed : (staffReplied ? copy.handoverConnected : copy.handoverWaiting);
    }

    function schedulePoll() {
        clearTimeout(pollTimer);
        if (!connected) return;
        if (stage === "handover") pollTimer = setTimeout(pollConversation, 4000);
        if (stage === "delivery_pending") pollTimer = setTimeout(pollConversation, 2000);
    }

    async function pollConversation() {
        const polledStage = stage;
        if (!["handover", "delivery_pending"].includes(polledStage)) return;
        if (pending || document.hidden) {
            schedulePoll();
            return;
        }
        const previous = revision;
        try {
            const result = await fetchChat({ poll: true, revision, cursor });
            if (pending || revision !== previous) return;
            if (polledStage === "delivery_pending" && result?.stage === "completed" && result.language === language) {
                revision = result.revision;
                stage = result.stage;
                lastReply = result.reply;
                displayReply(result);
                suggestedAnswers = result.suggestions || [];
                quickReplyLabels = result.quick_reply_labels || {};
                status.textContent = "";
                updateControls();
                announceReply(result);
                return;
            }
            if (!result || result.stage !== polledStage || result.language !== language) {
                await requestChat(openConversation({ resume: true }));
                return;
            }
            revision = result.revision;
            if (stage === "handover") appendHandover(result);
            updateControls();
        } catch {
            if (stage === "handover") status.textContent = labels[language].handoverOffline;
        } finally {
            schedulePoll();
        }
    }

    function announceReply(result) {
        form.dispatchEvent(new CustomEvent("reception:reply", { detail: {
            reply: result.reply, stage, language, revision,
            voiceWelcome, welcomeParts: result.welcome_parts,
            voiceParts: result.voice_parts, voiceKey: result.voice_key, voiceReply: result.voice_reply,
            voiceSilenceSeconds: result.voice_silence_seconds,
            voiceCanSkipPrompt: result.voice_can_skip_prompt === true,
            voiceTimeoutContinueInChat: result.voice_timeout_continue_in_chat === true,
        } }));
    }

    function openConversation({ resume = false } = {}) {
        return { start: true, ...(resume ? { resume: true } : {}), ...(dedicatedVoice ? { voice: true } : {}) };
    }

    function discardVoiceDraft() {
        if (!voiceDraft) return;
        voiceDraft = null;
        messageInput.value = "";
    }

    async function requestChat(payload) {
        if (pending) return;
        clearTimeout(idleTimer);
        const previousFocus = document.activeElement;
        const focusBooking = calendar?.contains(previousFocus) || staySummary?.contains(previousFocus);
        const focusComposer = focusBooking || form.contains(previousFocus) ||
            quickReplies.some((group) => group.contains(previousFocus));
        let reply = null;
        let failure = null;
        let focusDates = false;

        if (payload.reset) discardVoiceDraft();
        pending = true;
        retryButton.hidden = true;
        status.textContent = payload.message || payload.dates || payload.edit ? labels[language].waiting : labels[language].connecting;
        updateControls();
        form.dispatchEvent(new CustomEvent("reception:request", { detail: { reset: payload.reset === true } }));

        try {
            const result = await fetchChat(payload);
            if (!result) {
                failure = labels[language].reload;
                return;
            }
            if (result.date_error) {
                picker = result.date_picker || null;
                status.textContent = labels[language].invalidDates;
                focusDates = Boolean(picker);
                return;
            }
            if (voiceDraft && (payload.message || payload.dates || payload.edit || result.stale ||
                result.revision !== voiceDraft.revision)) {
                discardVoiceDraft();
            }
            const preserveLog = payload.start && payload.resume && connected && result.revision === revision;
            if ((payload.start && !preserveLog) || payload.reset) {
                messages.replaceChildren();
                cursor = 0;
                lastReply = null;
                staffReplied = false;
            }
            const wasHandover = stage === "handover";
            if (payload.message && !result.stale && !wasHandover) {
                if (!payload.transcript || !transcriptShown) appendMessage(payload.message, "guest");
                if (result.stage === "handover" && result.handover_messages.length) {
                    cursor = result.handover_messages[0].id;
                }
            }
            if (payload.dates && !result.stale) {
                appendMessage(Object.values(payload.dates).map(displayDate).join(" – "), "guest");
            }
            if (result.language !== language) {
                language = result.language;
                const address = new URL(window.location.href);
                address.searchParams.set("lang", language);
                history.replaceState(null, "", address);
                document.querySelectorAll(".reception-modes a").forEach((link) => {
                    const destination = new URL(link.href);
                    destination.searchParams.set("lang", language);
                    link.href = destination.href;
                });
            }
            if (preserveLog) {
                reply = result;
            } else if (!wasHandover || result.stage !== "handover" || lastReply !== result.reply) {
                displayReply(result);
                reply = result;
            } else if (payload.voice_start) {
                reply = result;
            }
            lastReply = result.reply;
            if (!voiceDraft && !preserveLog && !result.stale && !payload.timeout && !payload.voice_start) messageInput.value = "";
            stage = result.stage;
            if (calendar && result.revision !== revision) dateForm.reset();
            revision = result.revision;
            stay = result.booking_summary || null;
            picker = result.date_picker || null;
            focusDates = !result.stale && Boolean(picker && (payload.edit === "dates" || (payload.dates && calendar?.open)));
            voiceWelcome = legacyVoice && Boolean(result.voice_welcome);
            suggestedAnswers = result.suggestions || [];
            quickReplyLabels = result.quick_reply_labels || {};
            transcriptShown = false;
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
            if (focusDates && calendar) {
                calendar.open = true;
                (picker.arrival_min ? arrival : departure).focus();
            } else if (focusComposer && document.activeElement === document.body) {
                (messageInput.disabled ? resetButton : messageInput).focus();
            }
            if (reply !== null) {
                announceReply(reply);
            }
            if (failure !== null) {
                form.dispatchEvent(new CustomEvent("reception:error", {
                    detail: { message: failure, language },
                }));
            }
            schedulePoll();
            trackSilence();
        }
    }

    function sendMessage(message, transcript = false) {
        if (!connected || pending || terminalStages.has(stage) || !message) return;
        if (handoverClosed) return;
        return requestChat({
            message, revision, ...(voiceActive ? { voice: true } : {}), ...(transcript ? { transcript: true } : {}),
        });
    }

    function replyTimeout() {
        if (!calendar?.open && connected && !pending && ["welcome", "check_in"].includes(stage) &&
            (voiceActive || !document.hidden)) {
            requestChat({ timeout: true, revision, ...(voiceActive ? { voice: true } : {}) });
        }
    }

    function trackSilence() {
        clearTimeout(idleTimer);
        if (calendar?.open || dedicatedVoice || voiceActive || document.hidden || !connected || pending ||
            revision === voiceTimeoutChatRevision) return;
        if (stage === "welcome") idleTimer = setTimeout(replyTimeout, 2000);
        if (stage === "check_in" && !messageInput.value.trim()) {
            idleTimer = setTimeout(replyTimeout, 10000);
        }
    }

    messageInput.addEventListener("input", () => {
        if (voiceDraft) voiceDraft.edited = true;
        if (stage === "check_in") trackSilence();
    });
    function discardHiddenVoiceDraft() {
        if (!voiceDraft) return;
        discardVoiceDraft();
        updateControls();
    }

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) discardHiddenVoiceDraft();
        trackSilence();
    });
    window.addEventListener("pagehide", discardHiddenVoiceDraft);
    form.addEventListener("reception:voice-session", (event) => {
        if (event.detail.continueInChat) voiceTimeoutChatRevision = revision;
        voiceActive = event.detail.active;
        if (voiceActive && calendar) calendar.open = false;
        if (displayedTurn) {
            if (voiceActive) {
                displayedTurn.spoken.clear();
                displayedTurn.item.querySelector("p").textContent = "";
            } else {
                displayedTurn.item.querySelector("p").textContent = displayedTurn.result.reply;
            }
        }
        if (voiceActive && voiceWelcome && !pending) requestChat({ voice_start: true, revision });
        updateControls();
        trackSilence();
    });
    form.addEventListener("reception:voice-silence", replyTimeout);
    form.addEventListener("reception:voice-prompt-skipped", (event) => {
        if (displayedTurn?.revision !== event.detail.revision) return;
        displayedTurn.item.querySelector("p").textContent = displayedTurn.result.reply;
        log.scrollTop = log.scrollHeight;
    });
    form.addEventListener("reception:spoken", (event) => {
        const spoken = event.detail;
        if (!displayedTurn || spoken.revision !== displayedTurn.revision) return;
        displayedTurn.spoken.set(spoken.index, spoken.text);
        displayedTurn.item.querySelector("p").textContent = [...displayedTurn.spoken.values()].join(" ");
        log.scrollTop = log.scrollHeight;
    });
    form.addEventListener("reception:voice-review", (event) => {
        if (!voiceReview || !connected || pending || handoverClosed || terminalStages.has(stage) ||
            messageInput.value.trim()) {
            event.preventDefault();
            return;
        }
        voiceDraft = { text: event.detail, edited: false, revision };
        messageInput.value = voiceDraft.text;
        updateControls();
    });

    voiceReviewRetry?.addEventListener("click", () => {
        if (!voiceDraft || !voiceActive || pending) return;
        discardVoiceDraft();
        updateControls();
        form.dispatchEvent(new CustomEvent("reception:voice-retry-answer"));
    });

    form.addEventListener("reception:transcript", (event) => {
        if (!connected || pending || handoverClosed || terminalStages.has(stage)) return;
        appendMessage(event.detail, "guest");
        transcriptShown = true;
        sendMessage(event.detail, true);
    });

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const message = messageInput.value.trim();
        const transcript = voiceActive && voiceDraft && !voiceDraft.edited && message === voiceDraft.text;
        sendMessage(message, transcript);
    });

    answerButtons.forEach((button) => {
        button.addEventListener("click", () => sendMessage(button.dataset.answer));
    });
    changeDelivery.addEventListener("click", () => {
        sendMessage(language === "pl" ? "zmień sposób dostawy" : "change delivery");
    });
    typeInstead?.addEventListener("click", () => {
        form.dispatchEvent(new CustomEvent("reception:type-instead"));
        messageInput.focus();
    });
    editButtons.forEach((button) => {
        button.addEventListener("click", () => {
            if (!connected || pending || !stay?.edits.includes(button.dataset.editBooking)) return;
            form.dispatchEvent(new CustomEvent("reception:type-instead"));
            requestChat({ edit: button.dataset.editBooking, revision, ...(dedicatedVoice ? { voice: true } : {}) });
        });
    });
    calendar?.addEventListener("toggle", () => {
        if (calendar.open && voiceActive) form.dispatchEvent(new CustomEvent("reception:type-instead"));
        trackSilence();
    });
    arrival?.addEventListener("input", updateDepartureMinimum);
    dateForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!connected || pending || !picker || !calendar.open) return;
        requestChat({
            dates: Object.fromEntries([...new FormData(dateForm)].filter(([, value]) => value)),
            revision, ...(dedicatedVoice ? { voice: true } : {}),
        });
    });
    resetButton.addEventListener("click", () => requestChat({ reset: true, ...(dedicatedVoice ? { voice: true } : {}) }));
    retryButton.addEventListener("click", () => requestChat(openConversation({ resume: connected || revision !== null || app.hasAttribute("data-resume") })));
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) requestChat(openConversation({ resume: true }));
    });

    requestChat(openConversation({ resume: app.hasAttribute("data-resume") }));
})();
