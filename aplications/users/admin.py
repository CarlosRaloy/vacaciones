from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import Profile

LEVEL_LABELS = {
    0: 'Bloqueado',
    1: 'Trabajador',
    2: 'Admin',
    3: 'RH',
    4: 'Líder',
    5: 'Auditor',
}


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    fk_name = 'user'


class CustomUserAdmin(UserAdmin):
    inlines = (ProfileInline,)
    list_display = UserAdmin.list_display + ('get_role',)
    search_fields = UserAdmin.search_fields

    def get_role(self, obj):
        p = getattr(obj, 'profile', None)
        if not p:
            return '—'
        return LEVEL_LABELS.get(p.level, f'Nivel {p.level}')
    get_role.short_description = 'Rol'


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'level', 'area', 'boss')
    list_filter = ('level', 'area')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
