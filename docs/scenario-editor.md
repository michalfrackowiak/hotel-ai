# Booking scenario editor

Open the private Django admin through the SSH tunnel and choose **Reception → Booking scenario prompts**. Filter by language, then select the step you want to edit. **Web check in** is the English opening when the visitor opens the English page directly. **Check in** is used after language selection.

- **Chat text** controls text-only booking. When blank, chat follows the voice wording. During active voice booking, chat displays the segment being spoken.
- **Voice segments** play in the order shown. Enter the spoken text and a whole number of seconds to pause after each segment. Fill an empty row to add a segment. Use **Restore built-in voice script** to restore the original speech and timing.
- **Listen during pause** is available on arrival prompts. The final arrival segment must give the guest time to reply. Pauses elsewhere are silent before the next segment or the normal reply window.
- Keep the placeholders shown in the form. The booking system fills in the dates, room options, prices, guest count, and reference when they apply.

Save and open a new booking in the selected language to hear or read the result. Browser and phone voice use the saved voice script. Chat uses the saved written text. Existing booking stages and confirmation requirements continue to govern the conversation.
