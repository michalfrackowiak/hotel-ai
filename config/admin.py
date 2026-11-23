from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig


WORKFLOWS = (
    ("Reception and booking scenarios", "reception", (
        ("StaffHandover", "Reception handovers", "Review conversations that need a receptionist."),
        ("ScenarioVersion", "Scenario versions", "Edit drafts and review changes before publishing a version."),
        ("ScenarioPrompt", "Legacy prompts", "Edit bilingual wording and pauses used by legacy flows."),
        ("ParserConfiguration", "Date parser settings", "Choose how Polish and English numeric dates are interpreted."),
        ("ParserSample", "Parser review", "Review captured replies and save corrections for eligible future inputs."),
    )),
    ("Rooms and reservations", "bookings", (
        ("Room", "Room inventory", "Manage room capacity, booking availability and calendar links."),
        ("Price", "Nightly prices", "Set the rate in PLN for future quotes."),
        ("Booking", "Reservations", "Find confirmed reservations and filter arrivals, stays and departures."),
        ("BookingConfirmation", "Confirmation delivery", "Check whether the email or SMS provider accepted a confirmation."),
        ("CalendarPublication", "Calendar publication", "Inspect publishing progress, retry scheduling and errors."),
    )),
    ("Voice and models", "model_providers", (
        ("SpeechSelection", "Speech assignments", "Review planned assignments and use status or preflight tools where authorized."),
        ("SpeechProvider", "Speech provider catalog", "Manage the catalog of recognition models and voices."),
        ("AgentConfiguration", "Conversation model", "Configure the model used for supported fallback tasks."),
    )),
)


class HotelAdminSite(AdminSite):
    site_header = "Neptun Resort administration"
    site_title = "Neptun Resort admin"
    index_title = "Hotel operations"
    index_template = "admin/hotel_index.html"

    def index(self, request, extra_context=None):
        response = super().index(request, extra_context)
        urls = {
            (app["app_label"], model["object_name"]): model["admin_url"]
            for app in response.context_data["app_list"]
            for model in app["models"]
            if model.get("admin_url")
        }
        workflows = []
        for title, app_label, items in WORKFLOWS:
            visible = [
                (label, description, urls[(app_label, model)])
                for model, label, description in items
                if (app_label, model) in urls
            ]
            if visible:
                workflows.append((title, visible))
        response.context_data["workflows"] = workflows
        return response


class HotelAdminConfig(AdminConfig):
    default_site = "config.admin.HotelAdminSite"
