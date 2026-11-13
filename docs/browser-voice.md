# Browser voice booking

Open the reception in a browser with Web Speech recognition and synthesis enabled, such as desktop Chrome, at `http://127.0.0.1:8080/` or a secure HTTPS deployment. Pick **English** or **Polski** and click **Start voice booking** or **Włącz rozmowę głosową**. Allow microphone access when prompted. Reception reads its current question, then listens for your answer. The text chat shows the conversation and stays available for corrections.

Say “tomorrow” or “jutro” for a relative date, or say a date with the year. For email, try “guest at example dot com” or “gosc małpa example kropka com.” You can type the address if recognition gets it wrong. Review the quoted room, dates, and price. Say “Yes” or “Tak,” or use the confirmation button, to book. The reception reads the booking reference and closes the voice session.

Use the voice button again to stop. If the browser cannot access the microphone or speech service, try again after checking site permissions or continue typing. Availability and voice quality depend on the browser and installed voices. Some browsers send microphone audio to their speech-recognition service. [Web Speech API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API)
