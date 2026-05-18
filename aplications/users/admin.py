"""
Admin de Usuarios — diseñado para que el equipo de soporte pueda corregir
incidencias sin necesidad de saber SQL ni Django.

Pautas:
  · Cada sección está agrupada y descrita en español.
  · Los roles aparecen como píldoras de color (Trabajador, Admin, RH, etc.).
  · Las acciones masivas (bloquear / desbloquear, activar / desactivar)
    están expuestas en el menú "Acciones" arriba de cada listado.
  · Los campos peligrosos (is_superuser, is_staff) están en su propia sección
    plegada con advertencia.
"""
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html

from .models import Profile, PositionUserModel, AreasUserModel


# ──────────────────────────────────────────────────────────
# Tablas de referencia para presentación
# ──────────────────────────────────────────────────────────
LEVEL_LABELS = {
    0: 'Bloqueado',
    1: 'Trabajador',
    2: 'Admin',
    3: 'RH',
    4: 'Líder',
    5: 'Auditor',
}
LEVEL_COLORS = {
    0: '#1f2937',
    1: '#64748b',
    2: '#ef4444',
    3: '#8b5cf6',
    4: '#0ea5e9',
    5: '#0d9488',
}


def role_badge(level):
    """Devuelve la píldora HTML con el rol y su color."""
    label = LEVEL_LABELS.get(level, f'Nivel {level}')
    color = LEVEL_COLORS.get(level, '#64748b')
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 10px;'
        'border-radius:999px;font-size:11px;font-weight:600;'
        'white-space:nowrap;">{}</span>',
        color, label,
    )


# ──────────────────────────────────────────────────────────
# Perfil como inline del usuario
# ──────────────────────────────────────────────────────────
class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    fk_name = 'user'
    verbose_name = 'Perfil del trabajador'
    verbose_name_plural = 'Perfil del trabajador'
    autocomplete_fields = ('position', 'area', 'boss')
    fieldsets = (
        ('🪪 Información del trabajador', {
            'fields': ('level', 'payroll_number', 'hiring_date', 'days_vacations'),
            'description': (
                '<b>Nivel</b> define qué puede ver y hacer:<br>'
                '0 = Bloqueado · 1 = Trabajador · 2 = Admin · '
                '3 = RH · 4 = Líder · 5 = Auditor.<br>'
                '<b>Días de vacaciones</b>: cuántos días le tocan al año.'
            ),
        }),
        ('🏢 Organización', {
            'fields': ('position', 'area', 'boss', 'boss_name'),
            'description': (
                'Si su jefe está registrado en el sistema (nivel = Líder), '
                'selecciónalo en <b>Jefe</b>. Si es un jefe externo, '
                'escribe el nombre en <b>Jefe (manual)</b>.'
            ),
        }),
        ('🎨 Personalización', {
            'fields': ('theme', 'picture', 'signature'),
            'classes': ('collapse',),
            'description': 'Configuración visual y firma escaneada.',
        }),
    )


# ──────────────────────────────────────────────────────────
# Usuario (con perfil embebido)
# ──────────────────────────────────────────────────────────
class CustomUserAdmin(UserAdmin):
    inlines = (ProfileInline,)
    list_display = (
        'username',
        'get_full_name_display',
        'role_pill',
        'get_area',
        'get_payroll',
        'is_active_display',
        'last_login_display',
    )
    list_display_links = ('username', 'get_full_name_display')
    list_filter = (
        'is_active',
        'profile__level',
        'profile__area',
        'profile__position',
    )
    search_fields = (
        'username', 'first_name', 'last_name', 'email',
        'profile__payroll_number',
    )
    list_per_page = 50
    list_select_related = ('profile', 'profile__area')

    fieldsets = (
        ('🔐 Cuenta', {
            'fields': ('username', 'password', 'is_active'),
            'description': (
                'Para <b>resetear contraseña</b>: clic en el enlace azul '
                '"esta forma" que aparece junto al campo password.'
            ),
        }),
        ('📇 Información personal', {
            'fields': ('first_name', 'last_name', 'email'),
        }),
        ('⚠️ Permisos avanzados', {
            'fields': ('is_staff', 'is_superuser', 'groups', 'user_permissions'),
            'classes': ('collapse',),
            'description': (
                '<b style="color:#ef4444;">SOLO PARA SUPERADMINS.</b> '
                'No tocar a menos que sepas lo que haces. El control normal '
                'de permisos se hace en el campo <b>Nivel</b> del perfil.'
            ),
        }),
        ('🕘 Información del sistema', {
            'fields': ('last_login', 'date_joined'),
            'classes': ('collapse',),
        }),
    )
    readonly_fields = ('last_login', 'date_joined')

    @admin.display(description='Nombre completo', ordering='first_name')
    def get_full_name_display(self, obj):
        return obj.get_full_name() or obj.username

    @admin.display(description='Rol', ordering='profile__level')
    def role_pill(self, obj):
        p = getattr(obj, 'profile', None)
        if not p:
            return '—'
        return role_badge(p.level)

    @admin.display(description='Área', ordering='profile__area__name_area')
    def get_area(self, obj):
        p = getattr(obj, 'profile', None)
        return p.area.name_area if p and p.area else '—'

    @admin.display(description='Nómina')
    def get_payroll(self, obj):
        p = getattr(obj, 'profile', None)
        return (p.payroll_number if p else None) or '—'

    @admin.display(description='Activo', boolean=True, ordering='is_active')
    def is_active_display(self, obj):
        return obj.is_active

    @admin.display(description='Último acceso', ordering='last_login')
    def last_login_display(self, obj):
        if not obj.last_login:
            return format_html('<span style="color:#9ca3af;">Nunca</span>')
        return obj.last_login.strftime('%d/%m/%Y %H:%M')

    # ───── Acciones masivas ─────
    actions = [
        'bloquear_usuarios', 'desbloquear_a_trabajador',
        'desactivar_cuentas', 'activar_cuentas',
    ]

    @admin.action(description='🚫 Bloquear (nivel 0 — no puede entrar al sistema)')
    def bloquear_usuarios(self, request, queryset):
        n = 0
        for u in queryset:
            if hasattr(u, 'profile'):
                u.profile.level = 0
                u.profile.save(update_fields=['level'])
                n += 1
        self.message_user(
            request,
            f'{n} usuario(s) bloqueado(s). En su próximo login los enviará '
            f'a la pantalla de bloqueo.',
            messages.SUCCESS,
        )

    @admin.action(description='✅ Desbloquear (volver a nivel Trabajador)')
    def desbloquear_a_trabajador(self, request, queryset):
        n = 0
        for u in queryset:
            if hasattr(u, 'profile'):
                u.profile.level = 1
                u.profile.save(update_fields=['level'])
                n += 1
        self.message_user(
            request,
            f'{n} usuario(s) regresaron a Trabajador (nivel 1). Si requieren '
            f'otro rol, edítalos individualmente.',
            messages.SUCCESS,
        )

    @admin.action(description='🔒 Desactivar cuenta (no puede iniciar sesión)')
    def desactivar_cuentas(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, f'{n} cuenta(s) desactivada(s).', messages.SUCCESS)

    @admin.action(description='🔓 Reactivar cuenta')
    def activar_cuentas(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, f'{n} cuenta(s) reactivada(s).', messages.SUCCESS)


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# ──────────────────────────────────────────────────────────
# Puestos
# ──────────────────────────────────────────────────────────
@admin.register(PositionUserModel)
class PositionAdmin(admin.ModelAdmin):
    list_display = ('name_position', 'description_position', 'usuarios_count')
    search_fields = ('name_position',)

    fieldsets = (
        (None, {
            'fields': ('name_position', 'description_position'),
            'description': 'Catálogo de puestos. Cada usuario puede tener un puesto asignado.',
        }),
    )

    @admin.display(description='Usuarios con este puesto')
    def usuarios_count(self, obj):
        return Profile.objects.filter(position=obj).count()


# ──────────────────────────────────────────────────────────
# Áreas
# ──────────────────────────────────────────────────────────
@admin.register(AreasUserModel)
class AreaAdmin(admin.ModelAdmin):
    list_display = ('name_area', 'description', 'usuarios_count')
    search_fields = ('name_area',)

    fieldsets = (
        (None, {
            'fields': ('name_area', 'description'),
            'description': 'Catálogo de áreas o departamentos.',
        }),
    )

    @admin.display(description='Usuarios en esta área')
    def usuarios_count(self, obj):
        return Profile.objects.filter(area=obj).count()


# ──────────────────────────────────────────────────────────
# Perfil (vista directa)
# ──────────────────────────────────────────────────────────
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user_full_name',
        'role_pill',
        'area',
        'position',
        'payroll_number',
        'hiring_date',
        'days_vacations',
        'boss_display',
    )
    list_filter = ('level', 'area', 'position')
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name',
        'payroll_number',
    )
    list_select_related = ('user', 'area', 'position', 'boss')
    autocomplete_fields = ('user', 'boss', 'position', 'area')
    list_per_page = 50

    fieldsets = (
        ('👤 Usuario vinculado', {
            'fields': ('user',),
            'description': 'Cada perfil pertenece a un solo usuario.',
        }),
        ('🪪 Datos del trabajador', {
            'fields': ('level', 'payroll_number', 'hiring_date', 'days_vacations'),
            'description': (
                '<b>Nivel</b>: 0 Bloqueado · 1 Trabajador · 2 Admin · '
                '3 RH · 4 Líder · 5 Auditor.<br>'
                '<b>Fecha de contratación</b> determina cuándo se reinicia '
                'su ciclo de vacaciones cada año.<br>'
                '<b>Días de vacaciones</b>: cantidad que le toca en cada ciclo.'
            ),
        }),
        ('🏢 Organización', {
            'fields': ('position', 'area', 'boss', 'boss_name'),
            'description': (
                'Si el líder está dentro del sistema, selecciónalo en <b>Jefe</b>. '
                'Si es alguien externo, escribe su nombre en <b>Jefe (manual)</b>.'
            ),
        }),
        ('🎨 Personalización', {
            'fields': ('theme', 'picture', 'signature', 'tour'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Trabajador', ordering='user__first_name')
    def user_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    @admin.display(description='Rol', ordering='level')
    def role_pill(self, obj):
        return role_badge(obj.level)

    @admin.display(description='Jefe asignado')
    def boss_display(self, obj):
        if obj.boss:
            return obj.boss.get_full_name() or obj.boss.username
        if obj.boss_name:
            return format_html(
                '<span style="color:#94a3b8;">{} (externo)</span>',
                obj.boss_name,
            )
        return '—'
