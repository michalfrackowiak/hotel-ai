# Booking API

Send `Authorization: Bearer <BOOKING_API_KEY>` with each request. Keep the key on the server for integrations and configure it in the local `.env` file. Dates are `YYYY-MM-DD` in the hotel's Europe/Warsaw time zone. Checkout is exclusive, so one guest can check in the day another checks out.

## Find available rooms

`GET /api/availability/?check_in=2027-06-10&check_out=2027-06-12&guests=2`

Each offer includes the room's ID, code, capacity, nightly rate, total stay price, and currency. A successful response returns `rooms: []` when no room fits the dates and party size.

```json
{
  "rooms": [
    {
      "room_id": 1,
      "room_code": "ROOM-2",
      "capacity": 2,
      "nightly_rate": "120.00",
      "total_price": "240.00",
      "currency": "PLN"
    }
  ]
}
```

## Confirm a booking

`POST /api/bookings/` with `Content-Type: application/json`

Send the guest's details, the selected room and quoted rate, and a UUID idempotency key. Keep the same key and payload when retrying after a timeout.

```json
{
  "room_id": 1,
  "check_in": "2027-06-10",
  "check_out": "2027-06-12",
  "guests": 2,
  "name": "Ada Example",
  "email": "ada@example.com",
  "nightly_rate": "120.00",
  "idempotency_key": "27fac955-d32a-455b-8a87-e57f2dcfe4eb"
}
```

The first successful confirmation returns HTTP `201` and a `booking` object with the reference, room ID and code, stay dates, guest count, and price. A retry returns HTTP `200` and the same reference. Validation errors return HTTP `400`, missing or invalid credentials return `401`, and booking conflicts return `409`. The conflict code distinguishes an unavailable room, a changed rate, and an idempotency key reused with a different payload. Request a fresh offer after an availability or price conflict.
