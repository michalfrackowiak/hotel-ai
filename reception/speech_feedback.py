from .conversation import recognizes_booking_answer
from .models import SpeechTrainingSample
from .speech_training import MAX_TRAINING_SAMPLES, training_text_allowed


CORRECTABLE_STAGES = {"check_in", "check_out", "guests"}


def _normalized(text):
    return " ".join(text.casefold().split()).rstrip(".!?。！？").rstrip()


def corrected_booking_transcript(state, text):
    """Apply one unambiguous staff-reviewed correction to an invalid English transcript."""
    stage = state["stage"]
    if state["language"] != "en" or stage not in CORRECTABLE_STAGES or not training_text_allowed(text):
        return text
    key = _normalized(text)
    if not key or recognizes_booking_answer(state, text):
        return text

    # Keep the original samples for staff review and limit the runtime read to
    # transcripts. Never fetch the audio when applying a correction.
    rows = list(
        SpeechTrainingSample.objects.unexpired().filter(language="en", stage=stage)
        .exclude(corrected_transcript="")
        .values_list("transcript", "corrected_transcript")[:MAX_TRAINING_SAMPLES + 1]
    )
    if len(rows) > MAX_TRAINING_SAMPLES:
        return text
    candidates = {_normalized(corrected) for raw, corrected in rows if _normalized(raw) == key}
    if len(candidates) == 1:
        candidate = candidates.pop()
        if candidate and training_text_allowed(candidate) and recognizes_booking_answer(state, candidate):
            return candidate
    return text
