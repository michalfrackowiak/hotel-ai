(() => {
    const account = document.querySelector("#guest-account");
    const form = document.querySelector("#chat-form");
    const prepare = document.querySelector("#guest-google-prepare");
    const button = document.querySelector("#guest-google-button");
    const signOut = document.querySelector("#guest-sign-out");
    const status = document.querySelector("#guest-account-status");
    const language = () => form.closest(".chat-footer").lang || document.documentElement.lang;
    const polish = {
        loading: "Ładowanie logowania Google…",
        ready: "Wybierz przycisk Google, aby się zalogować.",
        signingIn: "Logowanie…",
        signingOut: "Wylogowywanie…",
        retry: "Ponów logowanie Google",
        busy: "Poczekaj na odpowiedź recepcji i spróbuj ponownie.",
        unavailable: "Logowanie jest teraz niedostępne. Możesz kontynuować rezerwację bez konta.",
        signOutError: "Nie udało się wylogować. Spróbuj ponownie.",
    };
    const english = {
        loading: "Loading Google sign-in…",
        ready: "Select the Google button to sign in.",
        signingIn: "Signing in…",
        signingOut: "Signing out…",
        retry: "Retry Google sign-in",
        busy: "Wait for reception to respond, then try again.",
        unavailable: "Sign-in is unavailable. You can continue booking without an account.",
        signOutError: "Couldn't sign out. Try again.",
    };
    const copy = () => language() === "pl" ? polish : english;
    let sdkPromise;
    let changing = false;

    function loadGoogle() {
        if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id);
        if (!sdkPromise) {
            sdkPromise = new Promise((resolve, reject) => {
                const script = document.createElement("script");
                script.src = `https://accounts.google.com/gsi/client?hl=${language()}`;
                script.async = true;
                function fail() {
                    clearTimeout(timer);
                    script.remove();
                    reject(new Error("Google sign-in unavailable"));
                }
                const timer = setTimeout(fail, 10000);
                script.onload = () => {
                    clearTimeout(timer);
                    if (window.google?.accounts?.id) resolve(window.google.accounts.id);
                    else fail();
                };
                script.onerror = fail;
                document.head.append(script);
            }).catch((error) => {
                sdkPromise = undefined;
                throw error;
            });
        }
        return sdkPromise;
    }

    async function request(url, payload = null, method = "POST") {
        const headers = { Accept: "application/json" };
        if (method === "POST") headers["X-CSRFToken"] = form.elements.csrfmiddlewaretoken.value;
        if (payload !== null) headers["Content-Type"] = "application/json";
        const abort = new AbortController();
        const timeout = setTimeout(() => abort.abort(), 12000);
        try {
            const response = await fetch(url, {
                method,
                credentials: "same-origin",
                cache: "no-store",
                headers,
                ...(payload === null ? {} : { body: JSON.stringify(payload) }),
                signal: abort.signal,
            });
            if (!response.ok) return null;
            return await response.json();
        } finally {
            clearTimeout(timeout);
        }
    }

    function reloadReception(resume) {
        const url = new URL(window.location.href);
        url.searchParams.set("lang", language());
        if (resume) url.searchParams.set("continue", "1");
        else url.searchParams.delete("continue");
        window.location.replace(url);
    }

    function setSessionBusy(busy) {
        return form.dispatchEvent(new CustomEvent("reception:guest-session-change", {
            cancelable: true,
            detail: { busy },
        }));
    }

    function resetButton() {
        button.replaceChildren();
        button.hidden = true;
        prepare.hidden = false;
        prepare.disabled = false;
        prepare.textContent = copy().retry;
        if (!form.contains(document.activeElement)) prepare.focus();
    }

    async function changeSession(signedIn, credential = null) {
        if (changing) return;
        if (!setSessionBusy(true)) {
            status.textContent = copy().busy;
            return;
        }
        changing = true;
        status.textContent = signedIn ? copy().signingIn : copy().signingOut;
        if (signOut) signOut.disabled = true;
        try {
            const result = signedIn
                ? await request(account.dataset.signInUrl, { credential })
                : await request(account.dataset.signOutUrl);
            if (result?.signed_in === signedIn) {
                reloadReception(signedIn && result.draft_preserved);
                return;
            }
        } catch {
            // The server may have completed the request before the response was interrupted.
            try {
                const result = await request(account.dataset.sessionUrl, null, "GET");
                if (result?.signed_in === signedIn) {
                    reloadReception(signedIn);
                    return;
                }
            } catch {
                // Keep the booking page usable if the network is still down.
            }
        }
        changing = false;
        setSessionBusy(false);
        status.textContent = signedIn ? copy().unavailable : copy().signOutError;
        if (signedIn) resetButton();
        else {
            signOut.disabled = false;
            signOut.focus();
        }
    }

    prepare?.addEventListener("click", async () => {
        prepare.disabled = true;
        status.textContent = copy().loading;
        try {
            const google = await loadGoogle();
            const challenge = await request(account.dataset.nonceUrl);
            if (!challenge?.client_id || !challenge.nonce) throw new Error("Google nonce unavailable");
            google.initialize({
                client_id: challenge.client_id,
                nonce: challenge.nonce,
                ux_mode: "popup",
                callback: ({ credential }) => {
                    if (typeof credential === "string" && credential) changeSession(true, credential);
                },
            });
            button.hidden = false;
            google.renderButton(button, {
                theme: "outline",
                size: "large",
                text: "signin_with",
                locale: language(),
                click_listener: () => form.dispatchEvent(new Event("reception:type-instead")),
            });
            prepare.hidden = true;
            status.textContent = copy().ready;
            if (!form.contains(document.activeElement)) {
                (button.querySelector("iframe, button") || button).focus();
            }
        } catch {
            status.textContent = copy().unavailable;
            resetButton();
        }
    });
    signOut?.addEventListener("click", () => changeSession(false));
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) reloadReception(true);
    });
})();
