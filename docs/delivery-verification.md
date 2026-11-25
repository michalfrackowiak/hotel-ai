# Verify booking confirmation delivery and staff transfer

Run this rehearsal on an isolated staging deployment with a new booking database, a dedicated Compose project and unused loopback ports. Use fictional room and stay details, one authorized test inbox, one authorized test mobile number and a staffed test queue. Use dedicated staging provider credentials that can send real messages and handle real calls, plus a dedicated inbound test number or SIP domain. Keep production forwarding, production inventory and guest data outside the rehearsal. Store credentials and recipient details in private staging configuration.

## Prepare staging

Follow the [booking confirmation setup](booking-confirmations.md) for SMTP and SMS and the [phone setup](phone-booking.md) for the signed staging webhook and `HOTEL_PHONE_RECEPTION_NUMBER`. Use the dedicated HTTPS origin for `HOTEL_TELEPHONY_PUBLIC_BASE_URL`. The phone webhook needs public HTTPS access. Keep the web and admin listener private.

From the staging checkout, with its ignored `.env` configured, give every Compose command the same project and overlays. The example uses the base configuration. Include any additional staging overlays consistently.

```sh
docker compose -p hotel-ai-delivery-check -f compose.yaml -f compose.telephony.yaml -f compose.confirmations.yaml up --build --wait
docker compose -p hotel-ai-delivery-check -f compose.yaml -f compose.telephony.yaml -f compose.confirmations.yaml exec web python manage.py seed_demo
```

Check that the seeded inventory is available, the worker is running, and the admin confirmation queue is empty before making test bookings.

Run the configuration check in each process with the same Compose project and overlays. Read the SMTP and SMS lines from the worker and the staff transfer line from the phone service. Each invocation reports only the settings loaded in that process.

```sh
docker compose -p hotel-ai-delivery-check -f compose.yaml -f compose.telephony.yaml -f compose.confirmations.yaml exec booking-confirmations python manage.py check_delivery_configuration
docker compose -p hotel-ai-delivery-check -f compose.yaml -f compose.telephony.yaml -f compose.confirmations.yaml exec phone python manage.py check_delivery_configuration
```

The check prints fixed labels and makes no provider requests. Use the live checks below to verify receipt and transfer.

## Run the checks

1. Complete an English chat booking using the test inbox for email confirmation. Confirm the displayed contact during readback and record the fictional booking reference. In **Booking confirmations**, find the matching Email record and check its submission status. Check the received email for the same reference, stay dates, room, guest count and total. Pass requires one matching email at the test inbox and one accepted submission in admin.
2. Repeat with a Polish chat booking and the authorized test mobile number for SMS. Use non-overlapping dates if reusing the same room. Check the received message and the matching SMS record. Pass requires one matching SMS at the handset and one accepted submission in admin.
3. Call the staging voice number. Select English, then stay on the line through both arrival listening windows. Have someone answer the staff test queue and confirm two-way audio. Repeat in Polish and verify that the guest reaches the same queue. Pass requires an answered queue leg with two-way audio for each language and no new booking from either call. Record the proxy or provider outcome separately from the caller and staff observations. A ringing queue or successful callback alone leaves the transfer unverified.

An **Accepted by provider** record confirms submission. If the email or SMS has yet to arrive, record provider acceptance and receipt as separate outcomes. For **Failed**, **Uncertain** or **Sending or interrupted**, inspect the delivery provider before any further action. Keep uncertain or interrupted attempts in their current state and avoid resending them, since the provider may already have accepted the original attempt. Use a new fictional booking for any subsequent controlled trial.

## Record and clean up

Record the app revision, date and time zone, channels tested, matching receipt or answered-call evidence, and pass, fail or unverified for each case. Keep recipient addresses, phone numbers, request bodies, credentials and provider identifiers out of shared notes. Check the provider and test recipients for duplicates before removing the staging evidence.

Remove the staging webhook route or restore the previous test-number configuration. Stop the dedicated worker and phone service and remove the disposable database only after confirming the Compose project is the one created for this rehearsal. Retain only redacted results.
