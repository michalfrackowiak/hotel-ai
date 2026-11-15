import io
import re
import wave
from json import dumps
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from django.db import transaction

from .contacts import PHONE_DIGITS, parse_email
from .models import SPEECH_TRAINING_STAGES as TRAINABLE_STAGES
from .models import SpeechTrainingSample
from .speech import MAX_PCM_BYTES


MAX_TRAINING_SAMPLES = 250
MAX_EXPORT_SAMPLES = 50
MAX_TRANSCRIPT_LENGTH = 500
PCM_SAMPLE_RATE = 16000
CONTACT_DIGITS = re.compile(r"(?<![\w/.-])\+?\d(?:[\s().-]*\d){7,14}(?![\w/.-])")
CALENDAR_DIGITS = re.compile(r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./]\d{1,2}[./]\d{4})\b")


def training_text_allowed(text):
    if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT_LENGTH:
        return False
    if not text:
        return True
    # Booking contact turns are excluded separately. Also reject recognizable
    # contacts when they appear unexpectedly in an earlier utterance.
    if not text.isprintable() or "@" in text or parse_email(text):
        return False
    digits = re.sub(
        r"\b[^\W\d_]+\b",
        lambda match: PHONE_DIGITS.get(match[0], match[0]),
        CALENDAR_DIGITS.sub(" ", text.casefold()),
    )
    return not CONTACT_DIGITS.search(digits)


def expire_speech_training():
    samples = SpeechTrainingSample.objects
    expired = samples.exclude(pk__in=samples.unexpired().values("pk"))
    count, _ = expired.delete()
    return count


@transaction.atomic
def record_failed_transcript(*, turn_key, language, stage, pcm, transcript):
    if language not in ("en", "pl") or stage not in TRAINABLE_STAGES:
        return False
    if not isinstance(turn_key, str) or not re.fullmatch(r"[0-9a-f]{64}", turn_key):
        return False
    if not isinstance(pcm, bytes) or not 0 < len(pcm) <= MAX_PCM_BYTES or len(pcm) % 2:
        return False
    if not training_text_allowed(transcript):
        return False

    expire_speech_training()
    samples = SpeechTrainingSample.objects
    if samples.count() >= MAX_TRAINING_SAMPLES:
        return False
    _, created = samples.get_or_create(
        turn_key=turn_key, defaults={"language": language, "stage": stage, "pcm": pcm, "transcript": transcript},
    )
    return created


def pcm_to_wav(pcm):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(PCM_SAMPLE_RATE)
        wav.writeframes(pcm)
    return output.getvalue()


def corrected_samples_zip(samples):
    output = SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            manifest = []
            for index, (pcm, text, language, stage) in enumerate(samples, 1):
                stem = f"sample-{index:04}"
                audio_name = f"{stem}.wav"
                text_name = f"{stem}.txt"
                archive.writestr(audio_name, pcm_to_wav(pcm))
                archive.writestr(text_name, f"{text}\n")
                manifest.append(dumps({
                    "audio": audio_name, "transcript": text_name, "language": language, "stage": stage,
                }, ensure_ascii=False))
            archive.writestr("manifest.jsonl", "\n".join(manifest) + "\n")
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise
