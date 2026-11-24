"""Bind saved admin requests to one configured controller generation."""

import re

from django.conf import settings

from .control_status import ControlStatusError


def current_dispatch_generation():
    generation = settings.HOTEL_SPEECH_CONTROL_GENERATION
    if not isinstance(generation, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{0,64}", generation):
        raise ControlStatusError("configuration")
    return generation
