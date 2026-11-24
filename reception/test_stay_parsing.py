from datetime import date
from unittest.mock import patch

from django.test import TestCase

from .models import ParserConfiguration
from .parsing import StayDates, parse_stay_input


@patch("reception.parsing.timezone.localdate", return_value=date(2026, 11, 15))
class NoisyStayParsingTests(TestCase):
    def test_arrival_and_duration_in_one_sentence(self, _today):
        for language, text, expected in (
            ("en", "strike of will be the from tomorrow for three days", StayDates(date(2026, 11, 16), date(2026, 11, 19))),
            ("en", "tomorrow for three days", StayDates(date(2026, 11, 16), date(2026, 11, 19))),
            ("en", "tomorrow and we plan to stay for a week. Thank you.", StayDates(date(2026, 11, 16), date(2026, 11, 23))),
            ("pl", "yyy nasz przyjazd będzie od jutra na trzy dni", StayDates(date(2026, 11, 16), date(2026, 11, 19))),
            ("pl", "jutro na trzy dni", StayDates(date(2026, 11, 16), date(2026, 11, 19))),
            ("pl", "od pojutrze na trzy noce", StayDates(date(2026, 11, 17), date(2026, 11, 20))),
            ("pl", "od pojutrza przez dwa tygodnie", StayDates(date(2026, 11, 17), date(2026, 12, 1))),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_stay_input("check_in", text, language), expected)

    def test_calendar_ranges_with_boundary_and_year_rollover(self, _today):
        for language, text, expected in (
            ("en", "please book from December twenty-ninth to January 2nd thanks", StayDates(date(2026, 12, 29), date(2027, 1, 2))),
            ("en", "from the 21st of November until the 23rd of November", StayDates(date(2026, 11, 21), date(2026, 11, 23))),
            ("en", "from 2027-06-10 to 2027-06-15 please", StayDates(date(2027, 6, 10), date(2027, 6, 15))),
            ("pl", "od 29.12 do 02.01", StayDates(date(2026, 12, 29), date(2027, 1, 2))),
            ("pl", "chcę pokój od dwudziestego pierwszego listopada do dwudziestego trzeciego listopada", StayDates(date(2026, 11, 21), date(2026, 11, 23))),
            ("pl", "od jutra do za trzy dni", StayDates(date(2026, 11, 16), date(2026, 11, 18))),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_stay_input("check_in", text, language), expected)

    def test_clear_single_date_or_checkout_duration_inside_a_reply(self, _today):
        self.assertEqual(parse_stay_input("check_in", "we'll arrive the day after tomorrow thank you", "en"), date(2026, 11, 17))
        self.assertEqual(parse_stay_input("check_in", "Przyjadę jutro, dziękuję", "pl"), date(2026, 11, 16))
        self.assertEqual(parse_stay_input("check_in", "um, tomorrow, please", "en"), date(2026, 11, 16))
        for language, text, expected in (
            ("en", "we would like to stay for three nights please", date(2026, 12, 2)),
            ("en", "we will be checking out on December 2nd", date(2026, 12, 2)),
            ("pl", "będziemy na trzy noce, dziękuję", date(2026, 12, 2)),
            ("pl", "wyjedziemy 4 grudnia, dziękuję", date(2026, 12, 4)),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_stay_input("check_out", text, language, check_in=date(2026, 11, 29)), expected)

    def test_conflicting_or_invalid_date_fragments_are_not_combined(self, _today):
        for language, text in (
            ("en", "is the restaurant open tomorrow"),
            ("en", "not tomorrow for three days"),
            ("en", "from tomorrow for three days or for four days"),
            ("en", "from tomorrow to the day after tomorrow or the 24th"),
            ("en", "arriving June 21.2027"),
            ("en", "arriving from 2027-02-30 to 2027-03-03"),
            ("pl", "nie od jutra na trzy dni"),
            ("pl", "od 30 listopada do 0.12"),
            ("pl", "od 1 czerwca do 2 czerwca do 3 czerwca"),
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_stay_input("check_in", text, language))
        self.assertEqual(parse_stay_input("check_in", "from tomorrow for zero days", "en"), date(2026, 11, 16))
        self.assertEqual(parse_stay_input("check_in", "from tomorrow to today", "en"), date(2026, 11, 16))

    def test_competing_checkout_fragments_require_a_checkout_reply(self, _today):
        for language, text in (
            ("en", "from tomorrow for two days for three days"),
            ("en", "from tomorrow for three days and five nights"),
            ("en", "from tomorrow to the day after tomorrow for three days"),
            ("pl", "od jutra na trzy dni i na pięć nocy"),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_stay_input("check_in", text, language), date(2026, 11, 16))

    def test_numeric_component_widths_in_both_languages(self, _today):
        for language, prefix, localized_short_year, localized_long_year, malformed_localized in (
            ("en", "arriving", "2/3/27", "2/3/2027", "2/3/027"),
            ("pl", "przyjadę", "3.2.27", "3.2.2027", "3.2.027"),
        ):
            for text in ("2027-2-3", "2027-02-03", localized_short_year, localized_long_year):
                for reply in (text, f"{prefix} {text}"):
                    with self.subTest(language=language, reply=reply):
                        self.assertEqual(parse_stay_input("check_in", reply, language), date(2027, 2, 3))
            for text in ("2027-02-003", "2027-02-0003", "2027-02/03", "002/03", malformed_localized):
                for reply in (text, f"{prefix} {text}"):
                    with self.subTest(language=language, reply=reply):
                        self.assertIsNone(parse_stay_input("check_in", reply, language))

    def test_numeric_width_changes_respect_date_configuration(self, _today):
        for language, prefix, short_year in (
            ("en", "arriving", "2/3/27"),
            ("pl", "przyjadę", "3/2/27"),
        ):
            ParserConfiguration.objects.filter(language=language).update(
                allow_iso_dates=False, allow_two_digit_years=False, allow_dash_dates=False,
            )
            for text in ("2027-2-3", short_year):
                for reply in (text, f"{prefix} {text}"):
                    with self.subTest(language=language, reply=reply):
                        self.assertIsNone(parse_stay_input("check_in", reply, language))
            ParserConfiguration.objects.filter(language=language).update(allow_iso_dates=True)
            self.assertEqual(
                parse_stay_input("check_in", f"{prefix} 2027-2-3", language), date(2027, 2, 3),
            )

    def test_departure_does_not_replace_a_malformed_arrival(self, _today):
        for language, prefix, checkout, malformed_localized in (
            ("en", "from", "to tomorrow", "2/3/027"),
            ("pl", "od", "do jutra", "3.2.027"),
        ):
            for arrival in (malformed_localized, "2027-02-003", "2027-02/03", "002/03"):
                reply = f"{prefix} {arrival} {checkout}"
                with self.subTest(language=language, reply=reply):
                    self.assertIsNone(parse_stay_input("check_in", reply, language))
