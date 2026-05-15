from django.contrib import admin
from .models import VacationRequest


@admin.register(VacationRequest)
class VacationRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'kind', 'partial_type', 'date', 'event_time', 'status', 'origin', 'created')
    list_filter = ('status', 'kind', 'origin')
    search_fields = ('employee__username', 'employee__first_name', 'employee__last_name', 'reason')
    date_hierarchy = 'date'
    autocomplete_fields = ('employee', 'requested_to_leader', 'leader_acted_by', 'hr_acted_by', 'cancelled_by')
