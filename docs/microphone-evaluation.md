# Physical microphone evaluation

This is the remaining human-operated speech check. Synthetic Piper-to-Vosk clips and simulated browser capture cannot measure microphone quality, accent variation or room noise. Use consenting staff and fictional booking details only. The retired audio-training queue stays disabled; this protocol requires no retained recordings.

## Procedure

1. Record device model, browser version, microphone/headset and room conditions. Open the hotel reception with English selected, then repeat with Polish selected. Check microphone permission and audible playback.
2. Try the phrases below once in a quiet room and once with ordinary background conversation. Use the same microphone distance in each condition. Record the visible transcript verbatim, parsed result, expected result, whether correction worked, and any busy/error feedback. Do not count a corrected attempt as initially correct.
3. Confirm that arrival/departure, guest count and selected room match the spoken request. At contact readback, verify every character or digit. Deliberately reject one candidate and enter a correction using **Type instead**. Check that microphone capture stops, the full question remains visible and the stay is preserved.
4. Stop before final contact approval on the public site and reset the conversation. Final booking/dispatch tests belong in isolated staging. Do not use real contact details or room inventory for test bookings.

| Purpose | English | Polish |
| --- | --- | --- |
| Relative stay | From tomorrow for three nights | Od jutra na trzy noce |
| Two dates | From 28 December 2027 to 2 January 2028 | Od 28 grudnia 2027 do 2 stycznia 2028 |
| Guests | Two adults and one child | Dwie osoby dorosłe i jedno dziecko |
| Room option | The second option | Druga opcja |
| Email | guest at example dot com | gosc małpa example kropka com |
| Phone | plus four eight five zero one two three four five six seven | plus cztery osiem pięć zero jeden dwa trzy cztery pięć sześć siedem |
| Uncertain approval | I am not sure | Nie jestem pewien |
| Negative approval | No | Nie |

Use dates still in the future when running this protocol. The guest-count phrase tests existing parser behavior; report failure rather than assuming support for a new interpretation. The example phone number is a test input only, never a dispatch destination.

## Record and interpret

Use one row per attempt: language, condition, intended phrase, visible transcript, parser result, expected result, initial outcome, correction outcome and elapsed response time if measured. Keep results in a private local worksheet until checked for accidental personal information. Summaries should separate correct extraction, invalid rejection, wrong-but-valid extraction, successful correction and abandonment. A syntactically valid address or number is not proof of intended identity.

No physical-microphone result is recorded yet. Do not mark this roadmap gate complete from synthetic or simulated tests. After collecting results, prioritize reproducible failures and evaluate each change against the same phrases and conditions.
