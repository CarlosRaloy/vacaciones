"""
Saldo de vacaciones (cálculo lazy, sin cron).

Ciclo anual definido por Profile.hiring_date:
- cycle_start: aniversario más reciente <= hoy
- cycle_end: siguiente aniversario
- Al cruzar el aniversario, el saldo vuelve a Profile.days_vacations.
  Lo no usado se pierde (no es acumulable).

3 parciales aprobados (LATE/EARLY mezclados) cuentan como 1 día completo.
Solo los grupos cerrados de 3 descuentan saldo — un parcial suelto no resta.
"""
from dataclasses import dataclass
from datetime import date

from django.contrib.auth.models import User

from .models import VacationRequest


@dataclass
class Balance:
    allowance: int           # días asignados en el Profile
    full_used: int           # días completos aprobados en el ciclo
    partials_approved: int   # parciales aprobados en el ciclo
    partials_pending: int    # parciales en cualquier estado pendiente del ciclo
    cycle_start: date
    cycle_end: date

    @property
    def partials_days_used(self) -> int:
        """Días consumidos por grupos cerrados de 3 parciales aprobados."""
        return self.partials_approved // 3

    @property
    def partials_remainder(self) -> int:
        """Parciales aprobados que aún no completan un grupo de 3 (0, 1 o 2)."""
        return self.partials_approved % 3

    @property
    def used(self) -> int:
        return self.full_used + self.partials_days_used

    @property
    def remaining(self) -> int:
        return max(self.allowance - self.used, 0)


def cycle_start(profile, ref: date | None = None) -> date:
    """
    Devuelve la fecha del aniversario más reciente del profile (<= ref).
    Si no hay hiring_date, usa el 1 de enero del año actual como aproximación.
    """
    ref = ref or date.today()
    hd = getattr(profile, 'hiring_date', None)
    if not hd:
        return date(ref.year, 1, 1)
    # Aniversario en el año de ref (clamp feb 29 → feb 28 si aplica)
    try:
        anniv_this_year = date(ref.year, hd.month, hd.day)
    except ValueError:
        anniv_this_year = date(ref.year, hd.month, 28)
    return anniv_this_year if anniv_this_year <= ref else _prev_anniv(hd, ref.year - 1)


def cycle_end(profile, ref: date | None = None) -> date:
    """Próximo aniversario (exclusivo del ciclo actual)."""
    start = cycle_start(profile, ref)
    return _next_anniv(start)


def _prev_anniv(hd: date, year: int) -> date:
    try:
        return date(year, hd.month, hd.day)
    except ValueError:
        return date(year, hd.month, 28)


def _next_anniv(start: date) -> date:
    try:
        return date(start.year + 1, start.month, start.day)
    except ValueError:
        return date(start.year + 1, start.month, 28)


def get_balance(user: User) -> Balance:
    profile = getattr(user, 'profile', None)
    allowance = int(getattr(profile, 'days_vacations', 0) or 0)
    start = cycle_start(profile)
    end = cycle_end(profile)

    qs = VacationRequest.objects.filter(employee=user, date__gte=start, date__lt=end)
    approved = qs.filter(status=VacationRequest.Status.APPROVED)
    pending = qs.filter(status__in=[
        VacationRequest.Status.PENDING_LEADER,
        VacationRequest.Status.PENDING_HR,
    ])

    return Balance(
        allowance=allowance,
        full_used=approved.filter(kind=VacationRequest.Kind.FULL_DAY).count(),
        partials_approved=approved.filter(kind=VacationRequest.Kind.PARTIAL).count(),
        partials_pending=pending.filter(kind=VacationRequest.Kind.PARTIAL).count(),
        cycle_start=start,
        cycle_end=end,
    )


def _reset_str(cycle_end: date) -> str:
    return cycle_end.strftime('%d/%m/%Y')


def can_request_full_day(user: User) -> tuple[bool, str]:
    bal = get_balance(user)
    # Reservar también lo que ya está pendiente (full_day pendientes en el ciclo)
    pending_full = VacationRequest.objects.filter(
        employee=user,
        kind=VacationRequest.Kind.FULL_DAY,
        date__gte=bal.cycle_start, date__lt=bal.cycle_end,
        status__in=[VacationRequest.Status.PENDING_LEADER, VacationRequest.Status.PENDING_HR],
    ).count()
    available = bal.remaining - pending_full
    if available < 1:
        # Distinguimos: ¿saldo cero por uso o por pendientes?
        if bal.remaining < 1:
            return False, (
                f'Ya no tienes días de vacaciones disponibles en este ciclo. '
                f'Tu saldo se reinicia el {_reset_str(bal.cycle_end)}.'
            )
        return False, (
            f'No puedes pedir otro día completo ahora: tienes {bal.remaining} día(s) '
            f'de saldo pero {pending_full} solicitud(es) pendiente(s) ya lo reservan. '
            f'Espera a que se resuelvan o se cancelen.'
        )
    return True, ''


_ACTIVE_STATUSES = (
    'PENDING_LEADER', 'PENDING_HR', 'APPROVED',
)


def check_duplicate(user: User, day: date, kind: str, partial_type: str | None) -> tuple[bool, str]:
    """
    Reglas de unicidad por (employee, date) en solicitudes activas
    (pendientes o aprobadas; rechazadas/canceladas no cuentan):

    - Si hay un FULL_DAY activo el día X: no se puede crear NADA más para X.
    - Si pides FULL_DAY el día X: no debe haber ningún parcial activo para X.
    - Si pides PARTIAL: no se permite otro PARTIAL del MISMO subtipo el día X
      (puedes tener LATE + EARLY_LEAVE el mismo día, pero no dos LATE).
    """
    same_day = VacationRequest.objects.filter(
        employee=user,
        date=day,
        status__in=_ACTIVE_STATUSES,
    )
    has_full = same_day.filter(kind=VacationRequest.Kind.FULL_DAY).exists()
    if has_full:
        return False, 'Ya tienes una solicitud de día completo activa para esa fecha.'

    if kind == VacationRequest.Kind.FULL_DAY:
        if same_day.filter(kind=VacationRequest.Kind.PARTIAL).exists():
            return False, 'Ya tienes un permiso activo para esa fecha. Cancélalo si quieres pedir el día completo.'
        return True, ''

    # kind == PARTIAL
    if partial_type and same_day.filter(
        kind=VacationRequest.Kind.PARTIAL,
        partial_type=partial_type,
    ).exists():
        label = dict(VacationRequest.PartialType.choices).get(partial_type, partial_type)
        return False, f'Ya tienes un permiso de "{label}" activo para esa fecha.'

    return True, ''


def can_request_partial(user: User) -> tuple[bool, str]:
    """
    Reglas:
      1. Si remaining < 1 (sin saldo disponible), no se permite ningún permiso
         nuevo — antes la lógica permitía acumular 1 o 2 sueltos porque
         `1 // 3 == 0`, lo cual no tenía sentido para alguien con saldo 0.
      2. Si hay saldo, el nuevo permiso proyectado no debe completar un grupo
         de 3 que exceda el allowance:
           proyectado = approved + pending + 1
           días_proyectados = proyectado // 3
           total = full_used + full_pending + días_proyectados
           rechaza si total > allowance.
    """
    bal = get_balance(user)

    pending_full = VacationRequest.objects.filter(
        employee=user,
        kind=VacationRequest.Kind.FULL_DAY,
        date__gte=bal.cycle_start, date__lt=bal.cycle_end,
        status__in=[VacationRequest.Status.PENDING_LEADER, VacationRequest.Status.PENDING_HR],
    ).count()
    available = bal.remaining - pending_full

    # Sin saldo disponible: bloqueo total (no permitir acumular permisos sueltos).
    if available < 1:
        if bal.allowance < 1:
            return False, (
                f'Tu perfil no tiene días de vacaciones asignados. '
                f'Pide a RH que configure tu saldo antes de solicitar permisos.'
            )
        if bal.remaining < 1:
            return False, (
                f'Ya no tienes días de vacaciones disponibles en este ciclo. '
                f'Tu saldo se reinicia el {_reset_str(bal.cycle_end)}.'
            )
        return False, (
            f'No puedes pedir otro permiso ahora: tu saldo está totalmente '
            f'reservado por {pending_full} solicitud(es) pendiente(s). '
            f'Espera a que se resuelvan o se cancelen.'
        )

    # Con saldo: proyectar grupo de 3.
    projected_partials = bal.partials_approved + bal.partials_pending + 1
    projected_days_from_partials = projected_partials // 3
    projected_total = bal.full_used + pending_full + projected_days_from_partials
    if projected_total > bal.allowance:
        return False, (
            f'No puedes pedir este permiso: completaría un grupo de 3 (cuenta como 1 día) '
            f'y ya no tienes saldo. '
            f'Tu saldo se reinicia el {_reset_str(bal.cycle_end)}.'
        )
    return True, ''
