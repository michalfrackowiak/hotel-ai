# Booking confirmations

Chat, browser voice and inbound phone bookings save the guest's selected email address or SMS number with the confirmed reservation. The booking and its delivery request commit together. The guest receives their booking reference immediately and the worker submits the confirmation afterward. Direct booking forms and the bearer API continue to use the existing name and email requirements.

Add the chosen provider settings to the deployment's ignored `.env` file.

- Email requires `HOTEL_SMTP_HOST` and `HOTEL_CONFIRMATION_FROM`. Set `HOTEL_SMTP_USER` and `HOTEL_SMTP_PASSWORD` if the server requires authentication. The default is STARTTLS on port 587. Set `HOTEL_SMTP_SSL=1` for implicit TLS on port 465, or use `HOTEL_SMTP_PORT` to override the default port.
- SMS requires `HOTEL_TWILIO_ACCOUNT_SID`, `HOTEL_TWILIO_AUTH_TOKEN`, and `HOTEL_TWILIO_SMS_FROM`. Choose a [Twilio sender](https://www.twilio.com/docs/messaging/api/message-resource#create-a-message-resource) authorized for the recipient's destination. A Polish nine-digit number defaults to `+48`. Guests enter the international country code for other destinations.

Start the optional worker with the same Compose project and files as the web service, adding `compose.confirmations.yaml`.

```sh
docker compose -f compose.yaml -f compose.confirmations.yaml up -d --build booking-confirmations
```

For the private server, keep `-f compose.server.yaml` and any Piper, Vosk or phone overrides in the command. For a Python deployment, run `python manage.py send_booking_confirmations --watch` under its process supervisor with the same Django database configuration.

Django admin lists **Booking confirmations**. Pending confirmations for an unconfigured channel remain queued. **Accepted by provider** records SMTP or Twilio acceptance. Inspect rejected, uncertain or interrupted submissions and follow up with the guest after checking the provider. The worker skips attempted submissions on subsequent runs to avoid duplicate messages if the original provider response was lost.

To check the live setup, make one test booking per configured channel with a recipient you control. Verify the confirmation contains the reference shown to the guest and check the provider acceptance status in Django admin. Automated tests exercise provider submissions with local mail and a simulated Twilio client.
