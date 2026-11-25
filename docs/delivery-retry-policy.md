# Booking confirmation retry policy

The current worker submits each queued confirmation once. This document
defines the first retry increment and its acceptance checks.

## Existing outcomes

| Recorded outcome | What the worker observed | Retry decision |
| --- | --- | --- |
| Pending | No worker has claimed the request. | Eligible for the original submission. |
| Sending or interrupted | A claim was saved before the worker started provider I/O. The worker might still be running or might have stopped at any point. | Investigate the provider and worker. Age alone cannot establish the outcome. |
| Accepted by provider | SMTP returned one sent message, or Twilio created a message with a status outside the worker's failed, undelivered or canceled list. | Preserve the submission. Verify receipt separately. |
| Submission failed | The worker received a Twilio HTTP 4xx, SMTP 4xx or 5xx, recipient refusal, validation/value error, or an unsuccessful submit result. An unsuccessful Twilio result can include an existing message SID. | Preserve legacy records. The status alone lacks enough evidence to schedule a retry. |
| Provider outcome uncertain | The worker received another response class or a transport, SDK or operating-system error. | Investigate before taking further action. A timeout can occur after acceptance. |

## First automatic retry increment

Limit eligibility to a newly observed Twilio submission failure with both HTTP
429 and error code 20429. Twilio documents that requests receiving this error
are unprocessed and can be retried after backing off. Use the exact code and
status from the submission exception. [Twilio error 20429](https://www.twilio.com/docs/api/errors/20429)

The first implementation should allow one additional attempt after a delay of
at least 60 seconds, within 15 minutes of the first claim. Persist the attempt
count and next eligible time. Once the budget or time window is exhausted,
keep the outcome visible in the confirmation queue. Keep SMTP outside this
increment. Any later SMTP policy must identify the response and protocol
phase, including whether the server accepted the message after DATA.
[RFC 5321 sections 4.2.1 and 4.2.5](https://www.rfc-editor.org/rfc/rfc5321.html#section-4.2.1)

Schedule the retry and store its eligibility evidence in one database
transition. Use a conditional claim before provider I/O so competing workers
cannot claim the same attempt. Persist the final result against that attempt's
claim. A worker that disappears mid-attempt leaves it for investigation.
The existing booking reference and confirmed destination identify the same
confirmation throughout. Any future manual resend needs separate authorization,
provider reconciliation and an audit record.

## Acceptance before rollout

- Verify one eligible first failure schedules exactly one retry. Test an early
  worker poll, an expired window, repeated eligible responses and the retry limit.
- Test competing workers, a restart while waiting, a crash after claim, and
  provider acceptance followed by failure to persist the result.
- Verify that unmatched 429 responses, transport failures, legacy failed rows,
  interrupted claims and Twilio results with a message SID cannot enter the retry queue.
- Confirm that logs and admin expose the outcome and retry reason with fixed
  labels. Keep recipient data, response bodies and credentials out of logs.
- Use provider doubles for failure injection. Verify actual receipt on isolated
  staging with controlled recipients before activating a live retry policy.
  [Controlled delivery verification](delivery-verification.md)
