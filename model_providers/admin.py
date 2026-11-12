from django.contrib import admin

from .models import AgentConfiguration


@admin.register(AgentConfiguration)
class AgentConfigurationAdmin(admin.ModelAdmin):
    list_display = ("provider", "model")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not AgentConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
