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

    def test_malformed_named_year_does_not_fall_back_to_yearless_date(self, _today):
        for language, prefix, day, checkout in (
            ("en", "arriving from", "June 21st", "to tomorrow"),
            ("pl", "przyjadę od", "21 czerwca", "do jutra"),
        ):
            for suffix in (" 27", " 027", " 02027", ", 27", ",027"):
                named = day + suffix
                for reply in (named, f"{prefix} {named}", f"{prefix} {named} {checkout}"):
                    with self.subTest(language=language, reply=reply):
                        self.assertIsNone(parse_stay_input("check_in", reply, language))
                departure = f"checking out on {named}" if language == "en" else f"wyjadę {named}"
                self.assertIsNone(parse_stay_input("check_out", departure, language, check_in=date(2027, 6, 20)))

    def test_named_dates_keep_explicit_years_and_numeric_durations(self, _today):
        arrival = date(2027, 6, 21)
        for language, reply, expected in (
            ("en", "arriving June 21st, 2027", arrival),
            ("en", "arriving June 21st 14 nights", StayDates(arrival, date(2027, 7, 5))),
            ("pl", "przyjadę 21 czerwca 2028 r. dziękuję", date(2028, 6, 21)),
            ("pl", "przyjadę 21 czerwca 14 nocy", StayDates(arrival, date(2027, 7, 5))),
        ):
            with self.subTest(language=language, reply=reply):
                self.assertEqual(parse_stay_input("check_in", reply, language), expected)

    def test_bilingual_month_abbreviations_and_noisy_ranges(self, _today):
        for language, reply, expected in (
            ("en", "Oct. 5, 2027", date(2027, 10, 5)),
            ("en", "5 Sept 2027", date(2027, 9, 5)),
            ("pl", "5 paź. 2027 r.", date(2027, 10, 5)),
            ("pl", "5 wrz.", date(2027, 9, 5)),
            ("en", "um, from 29 Dec. to Jan. 2 thanks", StayDates(date(2026, 12, 29), date(2027, 1, 2))),
            ("pl", "yyy od 29 gru. do 2 sty., dziękuję", StayDates(date(2026, 12, 29), date(2027, 1, 2))),
            ("en", "arriving Dec. 5 for two nights", StayDates(date(2026, 12, 5), date(2026, 12, 7))),
            ("pl", "przyjadę 5 gru. na dwie noce", StayDates(date(2026, 12, 5), date(2026, 12, 7))),
        ):
            with self.subTest(language=language, reply=reply):
                self.assertEqual(parse_stay_input("check_in", reply, language), expected)

    def test_month_abbreviations_preserve_year_and_ambiguity_validation(self, _today):
        for language, reply in (
            ("en", "from Dec. 5 027 to Dec. 7"),
            ("pl", "od 5 gru. 027 do 7 gru."),
            ("en", "arriving Dec. 5 or Jan. 2"),
            ("pl", "od 5 gru. albo 2 sty."),
            ("en", "arriving Decc 5"),
            ("pl", "przyjadę 5 gruu"),
        ):
            with self.subTest(language=language, reply=reply):
                self.assertIsNone(parse_stay_input("check_in", reply, language))

    def test_disabled_numeric_formats_are_rejected_in_bilingual_replies(self, _today):
        expected = date(2026, 12, 5)
        for language, arrival_prefix, range_link, checkout_prefix, localized, named, departure, relative, duration in (
            ("en", "from", "to", "checking out", ("12", "05"), "December 5", "December 7", "tomorrow", "for two nights"),
            ("pl", "od", "do", "wyjadę", ("05", "12"), "5 grudnia", "7 grudnia", "jutro", "na dwie noce"),
        ):
            first, second = localized
            configurations = ParserConfiguration.objects.filter(language=language)
            for field, numeric in (
                ("allow_iso_dates", "2026-12-05"),
                ("allow_slash_dates", f"{first}/{second}/2026"),
                ("allow_dot_dates", f"{first}.{second}.2026"),
                ("allow_dash_dates", f"{first}-{second}-2026"),
                ("allow_yearless_dates", f"{first}/{second}"),
                ("allow_two_digit_years", f"{first}/{second}/26"),
            ):
                with self.subTest(language=language, setting=field):
                    self.assertEqual(parse_stay_input("check_in", numeric, language), expected)
                    configurations.update(**{field: False})
                    self.assertIsNone(parse_stay_input("check_in", numeric, language))
                    self.assertIsNone(parse_stay_input("check_in", f"{arrival_prefix} {numeric}", language))
                    self.assertIsNone(parse_stay_input(
                        "check_in", f"{arrival_prefix} {numeric} {range_link} {departure}", language,
                    ))
                    self.assertIsNone(parse_stay_input(
                        "check_out", f"{checkout_prefix} {numeric}", language, check_in=date(2026, 12, 1),
                    ))
                    configurations.update(**{field: True})

            configurations.update(allow_dash_dates=False, allow_yearless_dates=False)
            self.assertEqual(parse_stay_input("check_in", f"{arrival_prefix} 2026-12-05", language), expected)
            self.assertEqual(parse_stay_input("check_in", f"{arrival_prefix} {named}", language), expected)
            self.assertEqual(parse_stay_input("check_in", relative, language), date(2026, 11, 16))
            self.assertEqual(
                parse_stay_input("check_out", duration, language, check_in=expected), date(2026, 12, 7),
            )

    def test_year_first_slash_and_dot_require_both_settings(self, _today):
        expected = date(2026, 12, 5)
        for language, prefix in (("en", "arriving"), ("pl", "przyjadę")):
            configurations = ParserConfiguration.objects.filter(language=language)
            for separator, field in (("/", "allow_slash_dates"), (".", "allow_dot_dates")):
                numeric = separator.join(("2026", "12", "05"))
                for iso, localized in ((True, True), (False, True), (True, False)):
                    with self.subTest(language=language, separator=separator, iso=iso, localized=localized):
                        configurations.update(allow_iso_dates=iso, **{field: localized})
                        self.assertEqual(
                            parse_stay_input("check_in", f"{prefix} {numeric}", language),
                            expected if iso and localized else None,
                        )

    def test_yearless_checkout_uses_the_arrival_year_in_both_languages(self, _today):
        arrival = date(2027, 12, 10)
        for language, standalone, embedded, pair in (
            ("en", "December 20", "checking out 12/20", "from December 10 2027 to December 20"),
            ("pl", "20 grudnia", "wyjadę 20.12", "od 10 grudnia 2027 do 20 grudnia"),
        ):
            for reply in (standalone, embedded):
                with self.subTest(language=language, reply=reply):
                    self.assertEqual(
                        parse_stay_input("check_out", reply, language, check_in=arrival), date(2027, 12, 20),
                    )
            self.assertEqual(parse_stay_input("check_in", pair, language), StayDates(arrival, date(2027, 12, 20)))

    def test_checkout_anchor_preserves_day_order_and_explicit_or_relative_dates(self, _today):
        arrival = date(2027, 12, 10)
        for language, earlier, new_year, explicit, relative in (
            ("en", "December 9", "January 2", "December 20 2026", "checking out tomorrow"),
            ("pl", "9 grudnia", "2 stycznia", "20 grudnia 2026", "wyjadę jutro"),
        ):
            with self.subTest(language=language):
                self.assertEqual(parse_stay_input("check_out", earlier, language, check_in=arrival), date(2027, 12, 9))
                self.assertEqual(parse_stay_input("check_out", new_year, language, check_in=arrival), date(2027, 1, 2))
                self.assertEqual(parse_stay_input("check_out", explicit, language, check_in=arrival), date(2026, 12, 20))
                self.assertEqual(parse_stay_input("check_out", relative, language, check_in=arrival), date(2026, 11, 16))

    def test_yearless_same_month_checkout_respects_leap_years(self, _today):
        for language, reply in (("en", "February 29"), ("pl", "29 lutego")):
            with self.subTest(language=language):
                self.assertEqual(
                    parse_stay_input("check_out", reply, language, check_in=date(2028, 2, 10)), date(2028, 2, 29),
                )
                self.assertIsNone(parse_stay_input("check_out", reply, language, check_in=date(2027, 2, 10)))

    def test_fractional_relative_offsets_do_not_become_calendar_dates(self, _today):
        for language, offset, arrival, departure, range_end, valid in (
            ("en", "in 12.5 days", "arriving", "departing", "to December 8", "in three days"),
            ("pl", "za 5.12 dnia", "przyjadę", "wyjadę", "do 8 grudnia", "za trzy dni"),
        ):
            with self.subTest(language=language):
                for reply in (offset, f"{arrival} {offset}", f"{arrival} {offset} {range_end}"):
                    self.assertIsNone(parse_stay_input("check_in", reply, language))
                self.assertIsNone(parse_stay_input(
                    "check_out", f"{departure} {offset}", language, check_in=date(2026, 11, 16),
                ))
                for punctuation_variant in (offset.replace(".", ","), offset.replace(" ", " +", 1)):
                    self.assertIsNone(parse_stay_input("check_in", f"{arrival} {punctuation_variant}", language))
                self.assertEqual(
                    parse_stay_input("check_in", f"{arrival} {valid}", language), date(2026, 11, 18),
                )

    def test_unrepresentable_duration_keeps_valid_arrival(self, _today):
        for language, prefix, duration in (
            ("en", "from", "for one night"),
            ("pl", "od", "na jedną noc"),
        ):
            with self.subTest(language=language):
                self.assertEqual(
                    parse_stay_input("check_in", f"{prefix} 9999-12-31 {duration}", language), date.max,
                )
                self.assertIsNone(parse_stay_input("check_out", duration, language, check_in=date.max))
                self.assertIsNone(parse_stay_input("check_out", "1", language, check_in=date.max))
                self.assertEqual(parse_stay_input("check_out", duration, language, check_in=date(9999, 12, 30)), date.max)

    def test_date_arithmetic_rejects_invalid_year_without_raising(self, _today):
        for language, month_duration, relative in (
            ("en", "for one month", "tomorrow"),
            ("pl", "na jeden miesiąc", "jutro"),
        ):
            with self.subTest(language=language):
                self.assertIsNone(parse_stay_input("check_out", month_duration, language, check_in=date.max))
                self.assertIsNone(parse_stay_input("check_in", relative, language, today=date.max))
