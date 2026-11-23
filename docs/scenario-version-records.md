# Versioned booking scenario records

The reception app stores one scenario per Polish or English chat or voice scope. Each language can also have a shared template. A channel draft records the exact sealed shared version it inherits from in `base_version`. Leaving the field empty makes the draft independent. Shared templates have no parent.

A scenario can retain several versions, each with numbered identity, ordered steps and one destination per event from each source step. The graph belongs to one version. A saved step stays in its original version. Replace a transition to change its endpoints. Publication copies pinned shared defaults into the channel graph before sealing. The channel version keeps its base reference and its own effective graph. It inherits missing steps and transitions and any supported prompt field absent from the local step. Local prompt fields and positions take precedence, and only the selected channel’s wording is copied. Step configuration stores a structured JSON object behind the scenario step form. The draft graph validator checks a conservative subset of the existing booking transitions.

`published_at` identifies previously sealed versions. The scenario's `published_version` points to the current selection. Rollback selects a prior sealed version while retaining both versions and their graph records. The publish and rollback services require an active staff account with `reception.publish_scenario` and compare the current selection with the caller’s expected version. Staff confirmation also compares the latest audit ID and the reviewed graph fingerprint inside the write transaction. This catches edits made after review and selection changes that returned to the same version. Publication validates the effective graph and writes the seal, selected pointer and audit record in the same SQLite write transaction. Shared templates use structural validation. Standalone and materialized chat and voice graphs must also pass outcome coverage. SQLite triggers guard sealed records against inserts and updates, and Django delete signals protect graph records and publication history on ordinary ORM deletion. Direct SQL deletion remains available for Django flush and operator recovery. Later migrations that rebuild these tables must recreate their triggers.

Publication selects a version for its scenario scope and records staff history. Web conversations record their version selection at entry. The web renderer and booking engine use the pinned version for supported prompts and approved transitions.

## Draft editing and validation

Versioned scenario administration keeps proposed edits separate from live prompt edits. In Django admin, create a **Scenario** for the language and channel, then create a numbered **Scenario version**. Add **Scenario steps** and **Scenario transitions** with the same version. Reopen the version to see **Saved graph validation** and **Outcome coverage**. Django applies its usual model permissions to each entry. The validation preview requires permission to inspect both steps and transitions. To replace a transition, delete the draft transition and add its replacement. To remove a draft step, remove its connected transitions first. The version history and a pinned shared base identify exactly which records a draft refers to.

The draft validator checks graph structure and the approval path. It checks that the entry and required booking states exist, that steps are reachable, that events lead to permitted destinations, and that a completed booking can only follow both quote approval and contact approval. It permits explicitly designated same-step retries. Draft steps can use the supported wording and voice segment settings. The admin preview validates standalone graphs. The internal publisher validates the materialized graph and checks the retained base reference. Additional parser settings remain future work.

After saving a step, reopen it to edit its prompts. Use **Chat wording** to override a supported step's written prompt. For voice, add ordered **Voice segments** with spoken text and a whole-second pause. Arrival segments can listen during a positive pause, and the final custom arrival segment must listen. Keep the placeholders named in the form. Empty fields inherit available shared defaults and otherwise use built-in wording. Clearing a local field restores inheritance. Shared templates can store both channels' fields. This form covers the primary arrival, departure, guests, room, confirmation, delivery, contact-entry, cancellation and handover prompts. Supported outcome fields cover stay-date readback, departure and guest-count retries, unavailable and changed offers, individual room options, contact readback and validation, and receipt status and reference. Edit those fields on the step that displays them. The bilingual welcome uses its existing wording.

The structural preview accepts incomplete drafts. The stricter outcome check requires named routes for both delivery methods, the wait and immediate receipt paths, dispatch outcomes, and booking retries and corrections. It checks standalone English and Polish web chat and browser voice. Language switching, legacy drafts and inbound phone are still handled by their existing engines. The publisher protects the saved graph with its database transaction. At runtime, the booking engine identifies outcomes before applying their pinned transitions. Staff can continue changing live wording from the existing prompt editor.

## Chat suggestion labels

On a saved chat step or shared template, staff can edit the labels displayed for arrival, departure, guest-count and room suggestions, language choice, quote and contact decisions, and delivery actions. Leave a label blank to inherit the shared value or use the built-in label. The room label includes the `{room}` placeholder so guests can identify each available room.

The browser displays labels from the pinned chat scenario. A click sends the existing answer, including the selected room number, to the booking engine. The publication review lists label edits alongside wording and graph changes.

## Arrival input settings

On the arrival step of a saved chat, voice, or shared draft, choose whether to accept one arrival date or require both stay dates together. Leave the setting blank to inherit a shared setting or use the built-in behavior. Select **Arrival date or both stay dates** to explicitly override an inherited requirement.

When **Require arrival and departure together** is selected, a reply that yields only one date prompts the guest for both dates without saving a partial stay. A recognized arrival date and stay length can also supply both dates. The rule applies to typed and transcribed replies, including dates entered during the Polish welcome. Single-date suggestions are hidden in this mode. Update the arrival wording to ask for both dates when enabling the setting. The booking engine still requires an arrival date today or later and a departure date after arrival.

## Booking parser fallback

Set **Parser fallback** on an arrival, departure, or guest-count step to choose whether the configured model can interpret an answer that the built-in parser could not recognize. **Use the built-in parser only** skips that model request. **Allow the configured model fallback** overrides a shared built-in-only setting and still requires a configured provider. Leave the field blank to inherit or keep the usual fallback.

The setting follows the pinned browser scenario for typed and transcribed replies in English and Polish. An arrival answer entered during the Polish welcome uses the arrival step's policy. Staff parser corrections still apply when parser training is enabled. Phone bookings keep their existing parser behavior.

## Retry limits

On a saved browser scenario step, enter a limit from 1 to 10 to hand over to reception when that many consecutive answers trigger the step's retry outcome. Leave the setting blank to inherit the shared value or use the default behavior. Enter 0 to disable the limit explicitly. A positive limit requires a `retry_exhausted` transition from that step to `handover` in the effective graph.

Typed and transcribed answers count against the same limit. Moving to another step resets the count. Timeouts and stale requests do not count as retries. The automatic handover opens a staff conversation without copying the rejected answer into it.

## Browser voice silence windows

Set **Voice silence window (seconds)** on a saved arrival, departure, guest-count, room selection, quote decision, delivery choice, email, phone or contact confirmation step to wait 1–20 seconds for a guest to start speaking. The setting applies to each arrival segment marked **Listen** and to the listening window after the last spoken line when applicable. Ordinary playback pauses retain their segment timing. The built-in microphone capture waits for the configured interval. With browser-provided recognition, the setting controls the app timer and the browser may stop listening sooner. Leave the field blank to inherit the shared setting or keep the built-in timing. The combined browser reception uses its pinned chat version even when the microphone is active. A booking started with a voice version uses that version. Text reply timing and phone calls keep their existing behavior.

## Browser voice interruption

At the arrival, departure or guest-count step, set **Voice interruption** to **Allow guests to skip the spoken prompt**. During browser voice playback the guest can choose **Skip prompt and speak** to read the current reply in chat and start speaking immediately. With a segmented reply, the button skips the remaining segments and uses the final listening window or the step default. The booking stays on the current step until the guest responds or the existing silence behavior applies. Leave the setting blank to inherit, or select **Play the prompt as usual** to turn off the button even when the shared template enables it. The setting applies to the pinned scenario during browser voice.

## Browser voice timeout at arrival

Set **Voice timeout action** to **End voice and continue in chat** on the arrival step. The last unanswered listening window stops browser voice and restores the full reply in chat. Earlier listening windows still advance to the next spoken segment. After voice ends this way, the chat waits on the same reply until the guest acts. The setting applies while the page is open and follows the booking's pinned version. Leave it blank to inherit, or select **Follow the usual timeout route** to override an inherited chat continuation.

## Review publication or rollback

Open a scenario version in Django admin. Staff with publication and graph access can open a review for a saved draft or for a previously published version. Review the changes between the current graph and the proposed effective graph, including inherited settings, and confirm the action. Publication validates the draft before sealing it. Rollback selects an existing sealed version in the same scope. Both actions record the actor and the previous and new selections in publication history.

The confirmation applies to the reviewed graph and selection only. If another staff action changes either, reopen the review to get a fresh comparison. The same applies after the five-minute review token expires. Restore historical records and their database guards through a SQLite backup rather than importing sealed rows through Django fixtures.

## Web conversation pinning

A fresh web draft records its language, initial channel, and the selected sealed version ID in server-side conversation state. An absent selection is recorded explicitly, so a later publication does not change that draft. Existing drafts without the pin keep their previous behavior. The pin is retained on same-language turns, offer retries, receipt polling, and when the combined page switches between typing and speech.

The combined page starts a chat draft. A client starting a fresh voice draft can send `voice: true` with `start` or `reset` to the web message endpoint. A language change resolves a version in the new language for the original channel. An explicit reset starts a new draft and can select a different channel. Inbound phone follows its own booking flow.

## Pinned web prompts

Browser chat and speech read supported prompt settings from the sealed version recorded in the draft. A later publication, rollback, or edit to the legacy prompt table does not change those settings. A chat pin supplies written wording, and a voice pin supplies ordered spoken segments. The other channel uses built-in wording. During voice playback, the conversation displays the spoken segment as it plays.

Each override applies to its exact prompt key. The English arrival override applies to direct English entry. Arrival after switching from Polish uses the built-in English prompt. Indexed browser audio uses pinned segments and the same segment key as the on-screen reply. Unindexed audio retains its existing literal-reply behavior. Unsupported prompts and steps with no override use built-in wording in pinned drafts. Drafts without a version ID keep the existing live prompt behavior. The phone renderer is unchanged. Staff configuration of additional prompt variants remains roadmap work.

## Pinned browser transitions

In browser chat and voice, the booking engine classifies input at the arrival and departure steps and validates dates before applying the corresponding transition. The recorded scenario version must contain the approved edge for that outcome. A missing edge keeps the draft on its current date step and prompts the guest again. The server logs the version, step and event without including guest input.

With the built-in input setting, a single arrival date leads to the departure step. A complete valid date range leads to the guest-count step, and a valid departure can proceed only after the arrival date. At the arrival step, unrecognized input and a listening timeout use separate handover outcomes. At the Polish welcome step, typed continuation, timeout, and valid dates use their named edges. Explicit English selection starts the English scenario for the same channel.

Guest count, availability, room selection, quote approval or rejection, delivery choice, contact entry, and contact review use their approved edges. The booking engine checks availability, room selection, and consent before applying the route. A missing edge keeps the guest at the current step before booking creation. Drafts without a selected version use the built-in flow. Inbound phone continues to use its own booking flow.

After explicit contact approval, the engine checks the pinned route for the configured wait or immediate receipt path before creating a booking. Booking creation and conversation persistence remain atomic and use the existing idempotency key. Receipt polling classifies the stored confirmation status. If a pinned receipt edge is missing after booking creation, the server logs the configuration issue and returns the actual receipt status and booking reference.
