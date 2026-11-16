"""Probe synthetic English/Polish inference on locally approved CI endpoints."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.readiness import ProbeTarget, ReadinessProbes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tts-url", required=True)
    parser.add_argument("--stt-url", required=True)
    args = parser.parse_args(argv)
    probes = ReadinessProbes([
        ProbeTarget("piper", "tts", args.tts_url, ("en", "pl")),
        ProbeTarget("asr", "stt", args.stt_url, ("en", "pl")),
    ])
    results = []
    for name, task in (("piper", "tts"), ("asr", "stt")):
        for language in ("en", "pl"):
            success = probes(name, (language, task))
            results.append(success)
            print(f"{name}/{language}: success={success}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
