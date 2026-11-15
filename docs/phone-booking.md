# Book by phone

Hotel-ai accepts inbound calls through Twilio Programmable Voice. The Resort Neptun welcome gives callers two seconds to press **2** or say **English**. Otherwise, reception continues in Polish. Callers provide their arrival date, departure date or length of stay, and total guest count. The same booking engine checks availability and quotes up to two rooms. The caller chooses a room by voice or keypad, explicitly confirms the stay, and provides an email address or phone number before the app creates the booking and queues its confirmation. Twilio handles speech recognition and playback. [Twilio Gather](https://www.twilio.com/docs/voice/twiml/gather) · [Twilio voice webhook timeout](https://www.twilio.com/docs/usage/webhooks/webhooks-connection-overrides#limit-on-voice-call-requests)

## Connect the dedicated SIM number

Choose the route your carrier and equipment support. Both routes keep the dedicated mobile number as the number guests dial.

- **Carrier call forwarding.** Configure a voice-capable Twilio number to use the webhook below. Call that number directly first, then configure unconditional forwarding on the dedicated SIM to the Twilio number. Check with your carrier that your plan can forward to that destination and ask how it bills the forwarded call. The destination rules and forwarding options depend on the carrier and plan. [Twilio incoming voice configuration](https://www.twilio.com/docs/usage/webhooks/voice-webhooks) · [Orange Polska call forwarding](https://www.orange.pl/poradnik/na-czym-polega-przekierowanie-polaczen-i-jak-je-wlaczyc/)
- **Cellular-to-SIP gateway.** Insert the SIM in a voice-capable cellular gateway compatible with the carrier. Route incoming cellular calls to a SIP PBX, then route the calls from the PBX to an authenticated Twilio Programmable Voice SIP Domain. Configure the domain's call-control webhook below. Set up a Twilio credential list or IP access control list for the SIP route and verify signaling and media on the chosen equipment. [Gateway inbound routing example](https://support.yeastar.com/hc/en-us/articles/226140347-How-to-use-one-TG-GSM-Gateway-for-Incoming-and-Outgoing-Calls) · [Twilio inbound SIP domains](https://www.twilio.com/docs/voice/api/sending-sip)

## Start the phone service

Configure the following in the ignored `.env` file. Use the auth token for the Twilio account that owns the number or SIP domain. Keep the account token and Django secret out of Git and logs.

```dotenv
DJANGO_SECRET_KEY=
HOTEL_TWILIO_AUTH_TOKEN=
HOTEL_TELEPHONY_PUBLIC_BASE_URL=https://voice.example.com
HOTEL_PHONE_RECEPTION_NUMBER=
HOTEL_PHONE_PORT=8081
```

Set `HOTEL_TELEPHONY_PUBLIC_BASE_URL` to the exact public HTTPS origin without a path, query string, or trailing slash. Fill in the secret values, then start both services and seed the demo inventory if starting with an empty database.

```sh
docker compose -f compose.yaml -f compose.telephony.yaml up --build --wait
docker compose -f compose.yaml -f compose.telephony.yaml exec web python manage.py seed_demo
```

The phone service runs Gunicorn on a separate loopback port and shares the booking database with the local browser and admin service. Gunicorn serves only the health check and the signed voice webhooks. Configure a public reverse proxy with a trusted TLS certificate. Forward `POST` requests for `/telephony/` to `http://127.0.0.1:8081`, or to the `HOTEL_PHONE_PORT` you chose. Preserve the request path, form body and `X-Twilio-Signature` header. Set the upstream `Host` header to `127.0.0.1` or add the public hostname to `DJANGO_ALLOWED_HOSTS`. Keep Django admin, the booking API and the local demo on the private web listener. [Twilio webhook security](https://www.twilio.com/docs/usage/webhooks/webhooks-security) · [Deploying Django](https://docs.djangoproject.com/en/5.2/howto/deployment/)

In the Twilio Console, set the voice number's incoming call handler or the SIP domain's call-control webhook to **POST** `https://voice.example.com/telephony/incoming/`, using your own public origin and the final slash. Twilio gets each subsequent callback URL from the returned TwiML. Keep the URL free of query parameters. [Twilio incoming voice webhooks](https://www.twilio.com/docs/usage/webhooks/voice-webhooks)

Set `HOTEL_PHONE_RECEPTION_NUMBER` to a separate staff queue number in international format starting with `+`. Use a destination that Twilio may dial and that routes directly to staff. A guest who stays silent for ten seconds or gives an unrelated response at the arrival-date prompt is routed to this number. If the destination is unset or Twilio cannot connect, the caller hears a localized unavailable message. [Twilio Dial](https://www.twilio.com/docs/voice/twiml/dial)

Start the [booking confirmation worker](booking-confirmations.md) with the same database to send emails and texts after the call.

## Verify the live phone route

On a Twilio trial, call from a verified test number. A normal booking takes eight callbacks after the initial webhook. Twilio's trial caps action and redirect chains at ten hops, so run repeated invalid-input checks in a separate call. Use an upgraded Twilio account to test transfer to the staff phone number. [Twilio Voice trial limits](https://www.twilio.com/docs/usage/trials/try-out-voice#global-limits)

1. Call the Twilio number directly or test an authenticated SIP call to the domain. Confirm that the language menu plays, then repeat the test by calling the dedicated SIM number from another phone.
2. Press **2** and try “tomorrow,” “for two nights,” and “two guests.” Select a room, say **Yes**, choose email, and dictate a test address using “at” and “dot.” Check the booking reference and the email using an inbox you control.
3. Call again and let the language menu time out. Pick a different future arrival date, say “na trzy noce” and “dwie osoby,” and choose a room by keypad. Say **Tak**, choose SMS, and use an authorized test recipient number. Check the booking reference and the text message.
4. In separate calls, say **English** at the menu and let the arrival prompt time out. Try an unrelated question in Polish and verify both calls reach the staff queue. Try an unavailable stay and a call that hangs up at the quote. Check that unfinished calls leave no reservation. Test the webhook with an unsigned POST and verify that it is rejected.

If Twilio gets a `403`, compare the exact public URL and account token and verify that the proxy preserves the signature header and complete form body. A `404` can indicate that the app has no phone configuration or that the callback reached the wrong listener. Inspect status codes and Twilio's error details while keeping request bodies and tokens out of logs. [Twilio signature validation](https://www.twilio.com/docs/usage/webhooks/webhooks-security)

## Call data and cleanup

Twilio and its speech provider process audio during the call. Hotel-ai uses unrecorded calls and handles audio transcripts transiently. The phone draft stores the Twilio call identifier, conversation state, and structured stay details. The selected email address or phone number is stored with the reservation in Django. Use contacts you control for live delivery tests. [Twilio speech recognition](https://www.twilio.com/docs/voice/twiml/gather#speechmodel)

Drafts expire after 20 minutes of inactivity. After arrival-date collection, three consecutive missed turns end the call. Schedule the cleanup command to clear expired draft data and delete terminal call records after 24 hours.

```sh
docker compose -f compose.yaml -f compose.telephony.yaml exec web python manage.py expire_phone_calls
```
