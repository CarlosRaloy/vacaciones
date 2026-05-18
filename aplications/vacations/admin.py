"""
Admin de Vacaciones — vista pensada para soporte.

Todo el flujo (creación, aprobación líder, aprobación RH, cancelación) está
desglosado en secciones con descripción en español. Las acciones masivas
permiten forzar estados cuando un soporte tiene que "destrabar" un caso.

Recuerda: cambiar el campo `status` aquí NO envía notificaciones ni emite
correos. Es para corregir, no para operar el flujo normal — para eso usa
las bandejas de líder y RH.
"""
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import VacationRequest


# ───────────── Colores de píldoras ─────────────
STATUS_COLORS = {
    'PENDING_LEADER': '#f59e0b',
    'PENDING_HR':     '#3b82f6',
    'APPROVED':       '#10b981',
    'REJECTED':       '#ef4444',
    'CANCELLED':      '#6b7280',
}


@admin.register(VacationRequest)
class VacationRequestAdmin(admin.ModelAdmin):
    list_display = (
        'employee_name',
        'kind_display',
        'date',
        'subtype_display',
        'status_pill',
        'origin_display',
        'late_badge',
        'leader_actor',
        'hr_actor',
        'created_short',
    )
    list_filter = (
        'status',
        'kind',
        'partial_type',
        'origin',
        'late_registration',
        'employee__profile__area',
    )
    search_fields = (
        'employee__username',
        'employee__first_name',
        'employee__last_name',
        'employee__profile__payroll_number',
        'reason',
        'late_note',
    )
    date_hierarchy = 'date'
    autocomplete_fields = (
        'employee', 'requested_to_leader',
        'leader_acted_by', 'hr_acted_by',
        'cancelled_by', 'captured_by',
    )
    list_per_page = 50
    list_select_related = (
        'employee', 'employee__profile',
        'requested_to_leader', 'leader_acted_by', 'hr_acted_by', 'cancelled_by',
    )
    readonly_fields = (
        'created', 'modified',
        'leader_acted_at', 'hr_acted_at', 'cancelled_at',
    )

    fieldsets = (
        ('📌 La solicitud', {
            'fields': ('employee', 'kind', 'partial_type', 'date', 'event_time', 'reason'),
            'description': (
                '<b>Tipo</b>: <i>Día completo</i> descuenta 1 día del saldo. '
                '<i>Acumulable</i> es un permiso parcial — 3 acumulables = 1 día.<br>'
                '<b>Subtipo</b> y <b>hora del evento</b> solo aplican para acumulables.<br>'
                '<b>Motivo</b>: texto libre que escribió el trabajador.'
            ),
        }),
        ('🚦 Estado actual', {
            'fields': ('status', 'origin'),
            'description': (
                '<b style="color:#ef4444;">Atención de soporte:</b> cambiar '
                '<b>estado</b> aquí actualiza el registro pero <b>no envía '
                'notificaciones</b>. Úsalo para corregir, no para operar el '
                'flujo normal — para eso están las bandejas de líder y RH.<br><br>'
                'Estados posibles:<br>'
                '· <b>Pendiente líder</b>: esperando aprobación del líder.<br>'
                '· <b>Pendiente RH</b>: el líder ya aprobó, falta RH.<br>'
                '· <b>Aprobada</b>: cuenta para el saldo del trabajador.<br>'
                '· <b>Rechazada</b>: no cuenta para el saldo.<br>'
                '· <b>Cancelada</b>: anulada manualmente.<br><br>'
                '<b>Origen</b>: si fue el trabajador quien la pidió o el líder la registró por él.'
            ),
        }),
        ('👥 Aprobaciones — quién, cuándo', {
            'fields': (
                'requested_to_leader',
                'leader_acted_by', 'leader_acted_at',
                'hr_acted_by', 'hr_acted_at',
            ),
            'description': (
                'Trazabilidad del flujo: a qué líder se le mandó, quién aprobó '
                'o rechazó como líder y como RH, con sus fechas exactas. Las '
                'fechas se llenan automáticamente, no se pueden editar a mano.'
            ),
        }),
        ('🚫 Cancelación', {
            'fields': ('cancelled_by', 'cancelled_at'),
            'classes': ('collapse',),
            'description': 'Si la solicitud está cancelada, aquí quedó registrado quién y cuándo.',
        }),
        ('🕘 Registro tardío', {
            'fields': ('late_registration', 'late_note'),
            'classes': ('collapse',),
            'description': (
                'Solo aplica cuando la solicitud se capturó <b>después</b> de la fecha '
                'del evento (típicamente: incapacidades, olvidos del líder, '
                'permisos justificados a posteriori). <b>Nota tardía</b> explica el motivo.'
            ),
        }),
        ('🛠️ Captura y auditoría', {
            'fields': ('captured_by', 'created', 'modified'),
            'classes': ('collapse',),
            'description': (
                '<b>Capturado por</b>: quién dio de alta físicamente la solicitud '
                '(el propio trabajador, su líder, RH o un admin).<br>'
                '<b>Creado</b> y <b>Modificado</b> son timestamps automáticos.'
            ),
        }),
    )

    # ───── Columnas en el listado ─────
    @admin.display(description='Trabajador', ordering='employee__first_name')
    def employee_name(self, obj):
        return obj.employee.get_full_name() or obj.employee.username

    @admin.display(description='Tipo', ordering='kind')
    def kind_display(self, obj):
        if obj.kind == VacationRequest.Kind.FULL_DAY:
            return format_html('🌴 Día completo')
        return format_html('⏱️ Acumulable')

    @admin.display(description='Subtipo / hora')
    def subtype_display(self, obj):
        if not obj.partial_type:
            return '—'
        label = obj.get_partial_type_display()
        if obj.event_time:
            return format_html('{} · <small>{}</small>', label, obj.event_time.strftime('%H:%M'))
        return label

    @admin.display(description='Estado', ordering='status')
    def status_pill(self, obj):
        color = STATUS_COLORS.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 10px;'
            'border-radius:999px;font-size:11px;font-weight:600;'
            'white-space:nowrap;">{}</span>',
            color, obj.get_status_display(),
        )

    @admin.display(description='Origen')
    def origin_display(self, obj):
        return obj.get_origin_display()

    @admin.display(description='Tardío', boolean=True, ordering='late_registration')
    def late_badge(self, obj):
        return obj.late_registration

    @admin.display(description='Aprobó líder')
    def leader_actor(self, obj):
        u = obj.leader_acted_by
        if not u:
            return '—'
        name = u.get_full_name() or u.username
        when = obj.leader_acted_at.strftime('%d/%m/%y %H:%M') if obj.leader_acted_at else ''
        return format_html('<small>{}<br>{}</small>', name, when)

    @admin.display(description='Aprobó RH')
    def hr_actor(self, obj):
        u = obj.hr_acted_by
        if not u:
            return '—'
        name = u.get_full_name() or u.username
        when = obj.hr_acted_at.strftime('%d/%m/%y %H:%M') if obj.hr_acted_at else ''
        return format_html('<small>{}<br>{}</small>', name, when)

    @admin.display(description='Creada', ordering='created')
    def created_short(self, obj):
        return obj.created.strftime('%d/%m/%y %H:%M') if obj.created else '—'

    # ───── Acciones masivas ─────
    actions = [
        'forzar_aprobacion_rh',
        'forzar_aprobacion_lider',
        'forzar_rechazo',
        'forzar_cancelacion',
    ]

    @admin.action(description='✅ Forzar APROBADA (RH ya firmó)')
    def forzar_aprobacion_rh(self, request, queryset):
        n = queryset.update(
            status=VacationRequest.Status.APPROVED,
            hr_acted_by=request.user,
            hr_acted_at=timezone.now(),
        )
        self.message_user(
            request,
            f'{n} solicitud(es) marcadas como APROBADAS. Cuenta para el saldo de los trabajadores.',
            messages.SUCCESS,
        )

    @admin.action(description='👤 Forzar paso a Pendiente RH (líder ya firmó)')
    def forzar_aprobacion_lider(self, request, queryset):
        n = queryset.update(
            status=VacationRequest.Status.PENDING_HR,
            leader_acted_by=request.user,
            leader_acted_at=timezone.now(),
        )
        self.message_user(
            request,
            f'{n} solicitud(es) avanzadas a "Pendiente RH". Falta que RH firme.',
            messages.SUCCESS,
        )

    @admin.action(description='❌ Forzar RECHAZADA')
    def forzar_rechazo(self, request, queryset):
        n = queryset.update(
            status=VacationRequest.Status.REJECTED,
            hr_acted_by=request.user,
            hr_acted_at=timezone.now(),
        )
        self.message_user(
            request,
            f'{n} solicitud(es) marcadas como RECHAZADAS. No descontará saldo.',
            messages.SUCCESS,
        )

    @admin.action(description='🚫 Forzar CANCELADA')
    def forzar_cancelacion(self, request, queryset):
        n = queryset.update(
            status=VacationRequest.Status.CANCELLED,
            cancelled_by=request.user,
            cancelled_at=timezone.now(),
        )
        self.message_user(
            request,
            f'{n} solicitud(es) canceladas. Quedan registradas pero no cuentan para saldo.',
            messages.SUCCESS,
        )
