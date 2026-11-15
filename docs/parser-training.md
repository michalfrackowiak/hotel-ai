# Parser training

Open the private Django admin through the SSH tunnel and choose **Reception → Parser training samples**. Use the language, source, booking step, parse outcome, and review filters to find an example. This queue stores the text passed to the parser. The speech training queue handles audio and transcription separately.

Each sample shows the original input, the original extracted result, and **Succeeded**, which means the result passed the current booking step's checks. A plausible result can still be wrong. Review the input and enter the intended result in **Corrected result**, then save. Clear that field or delete the sample to remove its rule.

Examples include an ISO date such as `2027-07-12`, a stay such as `2027-07-12 / 2027-07-15`, a duration such as `for three nights`, a guest count such as `2`, and a room code or option number. A contact correction can resolve a misheard separator while preserving the email's address characters or the phone number's digits.

Approved corrections apply when the same input recurs in the same language, source, and booking step. The parser checks the result against the current dates and offered rooms. Conflicting corrections leave the original parser in control. Explicit dates, counts, and available room choices keep their direct meaning.

## Enable and maintain the queue

Set `HOTEL_PARSER_TRAINING_ENABLED=1` in the ignored deployment `.env` and recreate the reception web service. If the deployment also runs the phone service, recreate it with the same setting. The web page displays a review notice while collection is enabled.

The queue holds up to 1,000 examples. Unreviewed rows and contact rows expire after 30 days. Other corrected rows expire after 180 days. Intake removes expired samples. Run `python manage.py expire_parser_samples` daily in the web service to remove expired samples during quiet periods. Access to the list and changes uses Django's Parser sample permissions.
