# Speech training

The browser sends voice replies to the private recognizer. To offer the optional training checkbox, set `HOTEL_SPEECH_TRAINING_ENABLED=1` in the ignored server `.env` and recreate the web service with the usual ASR Compose override. Voice booking works with the checkbox left unchecked.

The queue keeps one opted-in clip per booking turn when the hotel recognizer returns an empty transcript or the local booking parser cannot interpret it. Contact-detail questions, empty recordings, and recognizer outages are excluded. The queue covers the browser's private microphone pipeline. A wrongly recognized reply that fits the booking step can still pass the parser.

## Review a clip

1. Open the private Django admin through the SSH tunnel and go to **Reception → Speech training samples**.
2. Open an unreviewed sample. Play the clip and compare it with the recognizer's text.
3. Enter what the guest actually said as the corrected transcript and save. Delete clips with private details, unclear speech, or no training value.
4. Export reviewed samples when preparing an offline training or evaluation dataset. Keep exported voice recordings private.

The app uses an unambiguous correction for a repeated English recognition error on arrival, departure, and guest-count turns. The original transcript must fail the booking parser, and the replacement must be valid for that turn. The app does not use corrections to confirm or cancel a booking. An empty or unique error can still contribute its labeled audio and text to the exported dataset.

This replay of reviewed corrections improves repeat errors immediately. Training the Vosk language model or acoustic model is a separate offline step. Use the corrected text for language-model adaptation and the audio plus text for evaluating or training an acoustic model. Evaluate candidate models against recordings held out from training before deploying them. [Vosk model adaptation](https://alphacephei.com/vosk/adaptation)

## English model benchmark

The current recognizer uses Vosk's small US English model. We compared it with Vosk 0.22-lgraph on 60 synthetic booking replies using US English and UK English voices and a noise-added copy of the US set. The x86-64 Docker runs used one CPU and a 1.5 GiB memory limit, with the English and Polish models loaded together. [Published Vosk models](https://alphacephei.com/vosk/models)

| English model | Word error rate | Correct booking answers | Median decode | 95th-percentile decode |
| --- | ---: | ---: | ---: | ---: |
| Small 0.15 | 13.5% | 36/60 | 1.06 s | 3.20 s |
| 0.22-lgraph | 14.7% | 37/60 | 3.94 s | 8.48 s |

The larger model recognized two additional answers in the noisy subset and one fewer in the UK subset. We kept small 0.15 for its latency and similar booking results. These synthetic results measure the test environment. The corrected audio dataset will help evaluate real guest speech on the target server.

## Retention

Unreviewed clips expire after 30 days and reviewed clips after 180 days, measured from capture. The queue holds up to 250 clips. New intake removes expired clips. Run the cleanup command daily to remove expired clips even when intake is quiet.

```sh
python manage.py expire_speech_training
```
