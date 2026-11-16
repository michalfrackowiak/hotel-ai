# Contact confirmation

The shared booking conversation reads back the guest's email address or SMS number after stay consent and before creating a booking. This applies to English and Polish chat, browser voice, and phone calls, including contacts entered as text.

```text
Stay consent → delivery method → contact entry → contact readback
                                               ├─ yes → revalidate stay → book
                                               ├─ no → contact entry
                                               ├─ replacement → fresh readback
                                               └─ change delivery → delivery method
```

The browser displays the exact normalized contact next to confirmation controls. Speech spells the address separators and reads phone digits individually. **Change contact details** returns to entry; guests can also submit a replacement directly. **Change delivery method** discards the pending contact. An uncertain answer or silence never confirms it.

During browser voice contact entry or confirmation, **Type instead** / **Wpisz zamiast dyktować** stops speech capture and playback and focuses the existing message field. Stay details and earlier consent remain in place. Guests may restart voice with the usual voice button. Phone callers can repeat or request reception; phone prompts do not instruct them to use browser controls.

Pending contacts live in the existing private conversation or call state. Reading back a contact creates no client, booking, or delivery record. Only an explicit affirmative for the current revision can proceed to the existing booking transaction, which rechecks availability and price and preserves retry idempotency. Delivery status continues to distinguish pending dispatch from accepted delivery requests.

Staff can edit the bilingual contact confirmation prompts in the scenario editor. The `{contact}` placeholder supplies the normalized contact for chat and its spoken representation for voice. Keep the question explicit when editing it.

This improves the opportunity to catch a transcription error or typo; it does not improve the acoustic model or verify ownership of an email address or phone number. Parser review retention and permissions remain unchanged, and audio training remains retired. The original motivation and fictional failure examples are recorded in [speech recognition findings](speech-recognition-findings.md).

## Deployment and rollback

Migration 0007 adds the two confirmation prompt keys for both languages. Existing contact-entry drafts reach readback on their next valid answer. Older saved stay quotes containing an email also require contact confirmation before they can book. The original legacy flow remains email-only.

Take a private SQLite backup before deployment. The migration is additive, but an older application cannot interpret a saved `contact_confirm` draft. A rollback must deliberately reset those affected browser/phone drafts or retain the new transition handler; do not simply run the older image against them. Never restore an old whole-database backup over bookings made after deployment.
