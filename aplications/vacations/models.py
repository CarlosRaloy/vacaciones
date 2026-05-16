from django.contrib.auth.models import User
from django.db import models


class VacationRequest(models.Model):
    class Kind(models.TextChoices):
        FULL_DAY = 'FULL_DAY', 'Día completo'
        PARTIAL = 'PARTIAL', 'Acumulable'

    class PartialType(models.TextChoices):
        LATE = 'LATE', 'Llegada tarde'
        EARLY_LEAVE = 'EARLY_LEAVE', 'Salida en horas de trabajo'

    class Status(models.TextChoices):
        PENDING_LEADER = 'PENDING_LEADER', 'Pendiente de líder'
        PENDING_HR = 'PENDING_HR', 'Pendiente de RH'
        APPROVED = 'APPROVED', 'Aprobada'
        REJECTED = 'REJECTED', 'Rechazada'
        CANCELLED = 'CANCELLED', 'Cancelada'

    class Origin(models.TextChoices):
        EMPLOYEE = 'EMPLOYEE', 'Trabajador'
        LEADER = 'LEADER', 'Líder'

    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='vacation_requests')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    partial_type = models.CharField(max_length=15, choices=PartialType.choices, blank=True, null=True)
    date = models.DateField()
    event_time = models.TimeField(
        blank=True, null=True,
        help_text='Hora de llegada tarde o de salida anticipada (solo acumulables).',
    )
    reason = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_LEADER)
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.EMPLOYEE)

    requested_to_leader = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True,
        related_name='vacation_requests_to_review',
    )
    leader_acted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True,
        related_name='vacation_requests_leader_acted',
    )
    leader_acted_at = models.DateTimeField(blank=True, null=True)

    hr_acted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True,
        related_name='vacation_requests_hr_acted',
    )
    hr_acted_at = models.DateTimeField(blank=True, null=True)

    cancelled_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True,
        related_name='vacation_requests_cancelled',
    )
    cancelled_at = models.DateTimeField(blank=True, null=True)

    captured_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True,
        related_name='vacation_requests_captured',
        help_text='Quién registró físicamente la solicitud (trabajador, líder, RH o admin).',
    )

    late_registration = models.BooleanField(
        default=False,
        help_text='True si la solicitud se capturó después de la fecha del evento.',
    )
    late_note = models.TextField(
        blank=True,
        help_text='Motivo del registro tardío (accidente, olvido, etc.). Solo aplica si late_registration=True.',
    )

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created']
        indexes = [
            models.Index(fields=['employee', 'date']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.employee.username} {self.kind} {self.date} ({self.status})'

    @property
    def is_active(self) -> bool:
        return self.status in {self.Status.PENDING_LEADER, self.Status.PENDING_HR, self.Status.APPROVED}
