# Room calendars

Django remains the source of room availability. Each room can publish confirmed stays to its own Google Calendar. Publications are all-day busy events titled “Reserved.” The event starts on check-in and ends before the checkout date. It contains no guest names, contact details, or prices. Calendar edits do not change availability or bookings in Django.

## Set up a test calendar

1. Enable the Google Calendar API in a Google Cloud project and create a service account. For local Docker, place its credential JSON at `secrets/google-calendar.json`. This path is excluded from Git and the Docker image. Keep the host file restricted and grant the container's `hotel` user (UID 10001) read access. On Linux, a file ACL such as `setfacl -m u:10001:r secrets/google-calendar.json` can grant that access. [Google service account setup](https://developers.google.com/workspace/guides/create-credentials#service-account)
2. Create a dedicated calendar for the room. Share it with the service account email and grant **Make changes to events**. Copy the actual calendar ID from **Integrate calendar** in Google Calendar settings. [Google calendar sharing](https://developers.google.com/workspace/calendar/api/concepts/sharing)
3. Enter that ID in the room's **Google calendar ID** field in Django admin. Django accepts each calendar ID on one room.

## Publish reservations

Start Django and confirm a booking for the linked room. Run one sync from the project directory.

```sh
docker compose -f compose.yaml -f compose.calendar.yaml run --build --rm calendar-sync python manage.py sync_room_calendars
```

Use the same Compose project as the web service so both processes share the same SQLite volume. For automatic publication, start the optional worker.

```sh
docker compose -f compose.yaml -f compose.calendar.yaml up --build -d calendar-sync
docker compose -f compose.yaml -f compose.calendar.yaml logs -f calendar-sync
```

The worker checks for due stays every 30 seconds after Django starts. **Calendar publications** in Django admin shows the destination, successful publication time, attempt count, and the latest error. One-shot sync exits with an error when a publication attempt fails. Attempts resume automatically after a delay of two minutes, increasing up to one hour after repeated failures.

Clearing a room's calendar ID pauses publication and retries for that room. Each attempt records the destination before sending. After a timeout or other uncertain outcome, retries use that calendar even if the room's mapping changes. If Google explicitly rejected every prior insert attempt, a corrected room mapping applies on the next attempt. Any events already published remain on the original calendar. Repeating a sync uses the same event ID to recover from a lost response without creating a second event. [Google Calendar event insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert), [Google Calendar event get](https://developers.google.com/workspace/calendar/api/v3/reference/events/get)

## Verify a live calendar

Confirm a future stay with fictional guest details, run one sync, and look for one **Reserved** event on the room calendar. Check that the first night is included, checkout day is free, and the event omits the guest's information. Run sync again and confirm the calendar still contains one event for that stay. The automated tests exercise the API contract with simulated Google responses. A live test requires a shared calendar and service-account credentials.
