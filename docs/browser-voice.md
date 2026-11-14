# Browser voice booking

Open the reception in a modern browser, such as desktop Chrome, at `http://127.0.0.1:8080/` or a secure HTTPS deployment. Pick **English** or **Polski** and click **Start voice booking** or **Włącz rozmowę głosową**. Allow microphone access when prompted. Wait for **Listening…** or **Słucham…**, then answer and pause briefly to send your reply. The text chat shows the conversation and stays available for corrections.

Say “tomorrow” or “jutro” for check-in. When asked about checkout, you can say “in three days” or “za trzy dni,” which means three calendar days from today in the hotel's time zone. The reception reads back both dates before asking for the number of guests. You can also give a calendar date with the year in English or Polish.

For email, try “guest at example dot com” or “gosc małpa example kropka com.” You can type the address if recognition gets it wrong. Review the quoted room, dates, and price. Say “Yes” or “Tak,” or use the confirmation button, to book. The reception reads the booking reference and closes the voice session.

Use the voice button again to stop. If the browser cannot access the microphone, check this site's microphone permission and the selected input device, or continue typing. Where hotel speech recognition is configured, the browser sends each spoken reply to the hotel's server for transcription. It pauses microphone capture while reception speaks and releases the microphone when the voice session ends. Other deployments can use the browser's speech recognition service, which may send microphone audio to its vendor. [Web Speech API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API)

The [Piper service](server-deployment.md) generates English and Polish replies when enabled. The browser uses its installed speech synthesis when Piper is unavailable.
