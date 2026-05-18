from datetime import date, datetime, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import VacationRequest
from .services import (
    can_request_full_day,
    can_request_partial,
    check_duplicate,
    cycle_start,
    get_balance,
)


def _is_admin(user) -> bool:
    profile = getattr(user, 'profile', None)
    return bool(getattr(profile, 'is_admin', False)) or user.is_superuser


def _is_hr(user) -> bool:
    if _is_admin(user):
        return True
    return bool(getattr(getattr(user, 'profile', None), 'is_hr', False))


def _is_leader(user) -> bool:
    if _is_admin(user):
        return True
    return bool(getattr(getattr(user, 'profile', None), 'is_leader', False))


def _is_auditor(user) -> bool:
    if _is_admin(user):
        return True
    return bool(getattr(getattr(user, 'profile', None), 'is_auditor', False))


def _search_filter(qs, q: str):
    """Filtra VacationRequest por texto libre — nombre, usuario, nómina, motivo."""
    if not q:
        return qs
    return qs.filter(
        Q(employee__first_name__icontains=q)
        | Q(employee__last_name__icontains=q)
        | Q(employee__username__icontains=q)
        | Q(employee__profile__payroll_number__icontains=q)
        | Q(reason__icontains=q)
        | Q(late_note__icontains=q)
    )


def _parse_date(value: str) -> date:
    return datetime.strptime(value, '%Y-%m-%d').date()


# Horario laboral 08:00 - 18:00
WORK_START = time(8, 0)
WORK_END = time(18, 0)
LATE_MAX = time(11, 0)           # tope llegada tarde
EARLY_LEAVE_MIN = time(14, 0)    # 4 h antes de las 18:00


def _parse_time(value: str):
    if not value:
        return None
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError('time')


def _validate_partial_time(partial_type: str, t):
    if t is None:
        return 'Indica la hora del evento.'
    if partial_type == VacationRequest.PartialType.LATE:
        if not (WORK_START <= t <= LATE_MAX):
            return 'La hora de llegada debe estar entre 08:00 y 11:00.'
    elif partial_type == VacationRequest.PartialType.EARLY_LEAVE:
        if not (EARLY_LEAVE_MIN <= t <= WORK_END):
            return 'La hora de salida debe estar entre 14:00 y 18:00.'
    return None


@login_required
def employee_info(request, user_id: int):
    """
    Devuelve info ligera del trabajador objetivo para el modal on-behalf:
    nombre, puesto, área, jefe (resuelto FK o texto manual), iniciales y saldo
    actual del ciclo. Sólo accesible para líder/RH/admin. El líder además solo
    ve a sus propios subordinados (boss=request.user); admin/RH ven a todos.
    """
    if not (_is_leader(request.user) or _is_hr(request.user)):
        return HttpResponseForbidden()
    target = get_object_or_404(User.objects.select_related(
        'profile', 'profile__position', 'profile__area', 'profile__boss',
    ), pk=user_id)
    profile = getattr(target, 'profile', None)

    # Líder puro: sólo sus subordinados directos.
    if (
        _is_leader(request.user)
        and not _is_admin(request.user)
        and not _is_hr(request.user)
    ):
        boss_id = getattr(profile, 'boss_id', None) if profile else None
        if boss_id != request.user.pk:
            return HttpResponseForbidden()

    full_name = target.get_full_name() or target.username
    initials = ''.join(p[0] for p in full_name.split()[:2]).upper() or target.username[:2].upper()

    boss = getattr(profile, 'boss', None) if profile else None
    boss_name = (
        (boss.get_full_name() or boss.username)
        if boss else (getattr(profile, 'boss_name', None) if profile else None)
    )
    position = getattr(getattr(profile, 'position', None), 'name_position', None) if profile else None
    area = getattr(getattr(profile, 'area', None), 'name_area', None) if profile else None

    bal = get_balance(target)
    return JsonResponse({
        'id': target.id,
        'name': full_name,
        'initials': initials,
        'position': position or '',
        'area': area or '',
        'boss': boss_name or '',
        'has_leader': bool(boss),
        'balance': {
            'allowance': bal.allowance,
            'used': bal.used,
            'remaining': bal.remaining,
            'partials_remainder': bal.partials_remainder,
            'cycle_end': bal.cycle_end.strftime('%d/%m/%Y'),
        },
    })


@login_required
def calendar_view(request):
    profile = getattr(request.user, 'profile', None)
    boss = getattr(profile, 'boss', None) if profile else None
    employees = []
    is_admin = _is_admin(request.user)
    if _is_leader(request.user) or _is_hr(request.user):
        employees = User.objects.exclude(pk=request.user.pk).order_by('first_name', 'username')

    balance = get_balance(request.user)

    return render(request, 'vacations/calendar.html', {
        'is_hr': _is_hr(request.user),
        'is_leader': _is_leader(request.user),
        'is_admin': is_admin,
        'is_auditor': _is_auditor(request.user),
        'boss': boss,
        'employees': employees,
        'balance': balance,
    })


@login_required
def events_json(request):
    scope = request.GET.get('scope', 'mine')
    qs = VacationRequest.objects.select_related(
        'employee', 'leader_acted_by', 'hr_acted_by', 'cancelled_by', 'captured_by',
    )

    # Quién ve qué cuando scope=all:
    #  · admin / RH / auditor → toda la organización (sin canceladas)
    #  · líder puro → sus subordinados + él mismo (sin canceladas)
    #  · cualquier otro → solo sus propias solicitudes
    is_global = _is_hr(request.user) or _is_auditor(request.user)  # ambos incluyen admin
    is_leader = _is_leader(request.user)

    if scope == 'all':
        if is_global:
            qs = qs.exclude(status=VacationRequest.Status.CANCELLED)
        elif is_leader:
            sub_ids = list(
                User.objects.filter(profile__boss=request.user).values_list('id', flat=True)
            )
            sub_ids.append(request.user.id)
            qs = qs.filter(employee_id__in=sub_ids).exclude(
                status=VacationRequest.Status.CANCELLED,
            )
        else:
            qs = qs.filter(employee=request.user)
    else:
        qs = qs.filter(employee=request.user)

    start = request.GET.get('start')
    end = request.GET.get('end')
    if start:
        qs = qs.filter(date__gte=start[:10])
    if end:
        qs = qs.filter(date__lte=end[:10])

    color_map = {
        VacationRequest.Status.PENDING_LEADER: '#f59e0b',
        VacationRequest.Status.PENDING_HR: '#3b82f6',
        VacationRequest.Status.APPROVED: '#10b981',
        VacationRequest.Status.REJECTED: '#ef4444',
        VacationRequest.Status.CANCELLED: '#9ca3af',
    }
    icon_map = {
        VacationRequest.Kind.FULL_DAY: '🌴',
        VacationRequest.Kind.PARTIAL: '⏱️',
    }
    events = []
    for r in qs:
        events.append({
            'id': r.id,
            'title': f'{icon_map[r.kind]} {r.employee.get_full_name() or r.employee.username}',
            'start': r.date.isoformat(),
            'allDay': True,
            'backgroundColor': color_map.get(r.status, '#6b7280'),
            'borderColor': color_map.get(r.status, '#6b7280'),
            'extendedProps': {
                'kind': r.kind,
                'status': r.status,
                'status_label': r.get_status_display(),
                'kind_label': r.get_kind_display(),
                'partial_type': r.partial_type or '',
                'partial_label': r.get_partial_type_display() if r.partial_type else '',
                'event_time': r.event_time.strftime('%H:%M') if r.event_time else '',
                'reason': r.reason,
                'origin': r.origin,
                'employee': r.employee.get_full_name() or r.employee.username,
                'is_mine': r.employee_id == request.user.id,
                'captured_by': (
                    r.captured_by.get_full_name() or r.captured_by.username
                ) if r.captured_by_id else '',
                'late_registration': r.late_registration,
                'late_note': r.late_note,
                'requested_to_leader_id': r.requested_to_leader_id,
                # Auditoría: quién aprobó / cancelló y cuándo
                'leader_acted_by': (
                    r.leader_acted_by.get_full_name() or r.leader_acted_by.username
                ) if r.leader_acted_by_id else '',
                'leader_acted_at': (
                    timezone.localtime(r.leader_acted_at).strftime('%d/%m/%Y %H:%M')
                ) if r.leader_acted_at else '',
                'hr_acted_by': (
                    r.hr_acted_by.get_full_name() or r.hr_acted_by.username
                ) if r.hr_acted_by_id else '',
                'hr_acted_at': (
                    timezone.localtime(r.hr_acted_at).strftime('%d/%m/%Y %H:%M')
                ) if r.hr_acted_at else '',
                'cancelled_by': (
                    r.cancelled_by.get_full_name() or r.cancelled_by.username
                ) if r.cancelled_by_id else '',
                'cancelled_at': (
                    timezone.localtime(r.cancelled_at).strftime('%d/%m/%Y %H:%M')
                ) if r.cancelled_at else '',
            },
        })
    return JsonResponse({'events': events})


@login_required
@require_POST
def create_request(request):
    as_leader = request.POST.get('as_leader') == '1'
    as_hr = request.POST.get('as_hr') == '1'
    kind = request.POST.get('kind')
    if kind not in dict(VacationRequest.Kind.choices):
        return JsonResponse({'success': False, 'message': 'Tipo inválido.'}, status=400)
    try:
        d = _parse_date(request.POST.get('date', ''))
    except ValueError:
        return JsonResponse({'success': False, 'message': 'Fecha inválida.'}, status=400)

    partial_type = request.POST.get('partial_type') or None
    event_time = None
    if kind == VacationRequest.Kind.PARTIAL:
        if partial_type not in dict(VacationRequest.PartialType.choices):
            return JsonResponse({'success': False, 'message': 'Tipo de acumulable inválido.'}, status=400)
        try:
            event_time = _parse_time(request.POST.get('event_time', ''))
        except ValueError:
            return JsonResponse({'success': False, 'message': 'Hora inválida.'}, status=400)
        err = _validate_partial_time(partial_type, event_time)
        if err:
            return JsonResponse({'success': False, 'message': err}, status=400)
    if kind == VacationRequest.Kind.FULL_DAY:
        partial_type = None

    reason = request.POST.get('reason', '').strip()
    late_note_raw = request.POST.get('late_note', '').strip()

    # Determinar trabajador objetivo y privilegios
    is_admin = _is_admin(request.user)
    captured_by = request.user
    is_late = d < date.today()

    # Trabajador puro no puede capturar fechas pasadas.
    if is_late and not (as_leader or as_hr or is_admin):
        return JsonResponse({
            'success': False,
            'message': 'No puedes registrar fechas pasadas. Pide a tu líder o a RH que la capture por ti.',
        }, status=403)

    if as_leader:
        if not _is_leader(request.user):
            return HttpResponseForbidden('Solo líderes o admin.')
        emp_id = request.POST.get('employee_id')
        employee = get_object_or_404(User, pk=emp_id)
        # Validar duplicados antes del saldo (los duplicados no liberan saldo).
        ok, msg = check_duplicate(employee, d, kind, partial_type)
        if not ok:
            return JsonResponse({'success': False, 'message': msg}, status=400)
        # Validar saldo del trabajador. El admin puede saltarse el check SOLO
        # cuando captura para OTRO trabajador (caso excepcional, p.ej. incapacidad).
        # Si admin se captura para sí mismo, respeta el saldo como cualquiera.
        bypass_balance = is_admin and employee.pk != request.user.pk
        if not bypass_balance:
            ok, msg = (
                can_request_full_day(employee) if kind == VacationRequest.Kind.FULL_DAY
                else can_request_partial(employee)
            )
            if not ok:
                return JsonResponse({'success': False, 'message': msg}, status=400)
        if is_late:
            emp_profile = getattr(employee, 'profile', None)
            if d < cycle_start(emp_profile):
                return JsonResponse({
                    'success': False,
                    'message': 'La fecha está fuera del ciclo actual del trabajador y no puede registrarse retroactivamente.',
                }, status=400)
        req = VacationRequest.objects.create(
            employee=employee, kind=kind, partial_type=partial_type,
            date=d, event_time=event_time, reason=reason,
            status=VacationRequest.Status.PENDING_HR,
            origin=VacationRequest.Origin.LEADER,
            leader_acted_by=request.user, leader_acted_at=timezone.now(),
            captured_by=captured_by,
            late_registration=is_late,
            late_note=late_note_raw if is_late else '',
        )
    elif as_hr:
        # RH (o admin) captura a nombre del trabajador, pero NO firma por el líder.
        if not _is_hr(request.user):
            return HttpResponseForbidden('Solo RH o admin.')
        emp_id = request.POST.get('employee_id')
        employee = get_object_or_404(User, pk=emp_id)
        ok, msg = check_duplicate(employee, d, kind, partial_type)
        if not ok:
            return JsonResponse({'success': False, 'message': msg}, status=400)
        # El admin salta saldo solo cuando captura para otro trabajador.
        bypass_balance = is_admin and employee.pk != request.user.pk
        if not bypass_balance:
            ok, msg = (
                can_request_full_day(employee) if kind == VacationRequest.Kind.FULL_DAY
                else can_request_partial(employee)
            )
            if not ok:
                return JsonResponse({'success': False, 'message': msg}, status=400)
        emp_profile = getattr(employee, 'profile', None)
        if is_late and d < cycle_start(emp_profile):
            return JsonResponse({
                'success': False,
                'message': 'La fecha está fuera del ciclo actual del trabajador y no puede registrarse retroactivamente.',
            }, status=400)
        leader = getattr(emp_profile, 'boss', None) if emp_profile else None
        req = VacationRequest.objects.create(
            employee=employee, kind=kind, partial_type=partial_type,
            date=d, event_time=event_time, reason=reason,
            status=VacationRequest.Status.PENDING_LEADER,
            origin=VacationRequest.Origin.EMPLOYEE,
            requested_to_leader=leader,
            captured_by=captured_by,
            late_registration=is_late,
            late_note=late_note_raw if is_late else '',
        )
    else:
        # Trabajador (o admin para sí mismo). Si llegó aquí con is_late es porque es admin.
        ok, msg = check_duplicate(request.user, d, kind, partial_type)
        if not ok:
            return JsonResponse({'success': False, 'message': msg}, status=400)
        # Admin para sí mismo SÍ respeta su saldo — el bypass es solo on-behalf.
        ok, msg = (
            can_request_full_day(request.user) if kind == VacationRequest.Kind.FULL_DAY
            else can_request_partial(request.user)
        )
        if not ok:
            return JsonResponse({'success': False, 'message': msg}, status=400)
        profile = getattr(request.user, 'profile', None)
        if is_late and d < cycle_start(profile):
            return JsonResponse({
                'success': False,
                'message': 'La fecha está fuera del ciclo actual y no puede registrarse retroactivamente.',
            }, status=400)
        leader = getattr(profile, 'boss', None) if profile else None
        req = VacationRequest.objects.create(
            employee=request.user, kind=kind, partial_type=partial_type,
            date=d, event_time=event_time, reason=reason,
            status=VacationRequest.Status.PENDING_LEADER,
            origin=VacationRequest.Origin.EMPLOYEE,
            requested_to_leader=leader,
            captured_by=captured_by,
            late_registration=is_late,
            late_note=late_note_raw if is_late else '',
        )
    return JsonResponse({'success': True, 'id': req.id, 'message': 'Solicitud registrada.'})


@login_required
def leader_inbox(request):
    if not _is_leader(request.user):
        return HttpResponseForbidden()
    q = request.GET.get('q', '').strip()
    qs = VacationRequest.objects.filter(
        status=VacationRequest.Status.PENDING_LEADER,
    ).select_related('employee', 'employee__profile', 'requested_to_leader')
    # El líder solo ve solicitudes asignadas a él; admin ve todas.
    if not _is_admin(request.user):
        qs = qs.filter(requested_to_leader=request.user)
    qs = _search_filter(qs, q)
    pending = qs.order_by('date')
    return render(request, 'vacations/leader_inbox.html', {
        'pending': pending,
        'is_admin': _is_admin(request.user),
        'q': q,
    })


@login_required
def hr_inbox(request):
    if not _is_hr(request.user):
        return HttpResponseForbidden()
    # RH y admin ven TODAS las solicitudes; el botón de aprobar se activa
    # contextualmente según el estado (y rol).
    status_filter = request.GET.get('status', '')
    q = request.GET.get('q', '').strip()

    base_qs = VacationRequest.objects.select_related(
        'employee', 'employee__profile', 'requested_to_leader',
        'leader_acted_by', 'hr_acted_by', 'cancelled_by',
    )
    base_qs = _search_filter(base_qs, q)

    # Conteos por estado: respetan la búsqueda activa para que las píldoras
    # muestren cuántos resultados coincidentes hay en cada estado.
    counts = {
        s: base_qs.filter(status=s).count()
        for s, _ in VacationRequest.Status.choices
    }
    counts['ALL'] = base_qs.count()

    valid_statuses = dict(VacationRequest.Status.choices)
    qs = base_qs
    if status_filter in valid_statuses:
        qs = qs.filter(status=status_filter)

    requests_list = qs.order_by('-date', '-created')
    return render(request, 'vacations/hr_inbox.html', {
        'requests': requests_list,
        'is_admin': _is_admin(request.user),
        'status_filter': status_filter,
        'q': q,
        'counts': counts,
        'STATUS': VacationRequest.Status,
    })


_REDIRECT_TARGETS = {'leader_inbox', 'hr_inbox'}


def _next_redirect(request, default: str):
    target = request.POST.get('next', '') or default
    if target not in _REDIRECT_TARGETS:
        target = default
    return redirect(f'vacations:{target}')


@login_required
@require_POST
def leader_act(request, pk):
    if not _is_leader(request.user):
        return HttpResponseForbidden()
    req = get_object_or_404(VacationRequest, pk=pk)
    # Un líder solo puede actuar sobre solicitudes asignadas a él; admin bypass.
    if not _is_admin(request.user) and req.requested_to_leader_id != request.user.id:
        messages.error(request, 'Esta solicitud no está asignada a ti.')
        return _next_redirect(request, 'leader_inbox')
    action = request.POST.get('action')
    if req.status != VacationRequest.Status.PENDING_LEADER:
        messages.error(request, 'La solicitud ya no está pendiente del líder.')
        return _next_redirect(request, 'leader_inbox')
    if action == 'approve':
        req.status = VacationRequest.Status.PENDING_HR
    elif action == 'reject':
        req.status = VacationRequest.Status.REJECTED
    else:
        return JsonResponse({'success': False, 'message': 'Acción inválida'}, status=400)
    req.leader_acted_by = request.user
    req.leader_acted_at = timezone.now()
    req.save()
    messages.success(request, 'Acción registrada.')
    return _next_redirect(request, 'leader_inbox')


@login_required
@require_POST
def hr_act(request, pk):
    if not _is_hr(request.user):
        return HttpResponseForbidden()
    req = get_object_or_404(VacationRequest, pk=pk)
    action = request.POST.get('action')
    if req.status != VacationRequest.Status.PENDING_HR:
        messages.error(request, 'La solicitud ya no está pendiente de RH.')
        return _next_redirect(request, 'hr_inbox')
    if action == 'approve':
        req.status = VacationRequest.Status.APPROVED
    elif action == 'reject':
        req.status = VacationRequest.Status.REJECTED
    else:
        return JsonResponse({'success': False, 'message': 'Acción inválida'}, status=400)
    req.hr_acted_by = request.user
    req.hr_acted_at = timezone.now()
    req.save()
    messages.success(request, 'Acción registrada.')
    return _next_redirect(request, 'hr_inbox')


@login_required
@require_POST
def cancel_request(request, pk):
    """
    Solo se pueden cancelar solicitudes que sigan pendientes
    (PENDING_LEADER o PENDING_HR). Una vez aprobada — o rechazada /
    cancelada — la solicitud queda en firme.

    Permisos:
      · Trabajador (dueño): puede cancelar su propia solicitud pendiente
        siempre que la fecha aún no haya llegado.
      · Líder, RH o admin: pueden cancelar cualquier solicitud pendiente
        en cualquier momento (anterior a la aprobación).
    """
    req = get_object_or_404(VacationRequest, pk=pk)
    user = request.user
    is_owner = req.employee_id == user.id
    is_priv = _is_leader(user) or _is_hr(user)

    if req.status == VacationRequest.Status.APPROVED:
        return JsonResponse({
            'success': False,
            'message': 'La solicitud ya fue aprobada y no puede cancelarse.',
        }, status=400)
    if req.status in {VacationRequest.Status.CANCELLED, VacationRequest.Status.REJECTED}:
        return JsonResponse({'success': False, 'message': 'No se puede cancelar.'}, status=400)

    if is_priv:
        pass
    elif is_owner:
        if req.date <= date.today():
            return JsonResponse({'success': False, 'message': 'Ya pasó la fecha límite para cancelar.'}, status=400)
    else:
        return HttpResponseForbidden()

    req.status = VacationRequest.Status.CANCELLED
    req.cancelled_by = user
    req.cancelled_at = timezone.now()
    req.save()
    return JsonResponse({'success': True, 'message': 'Solicitud cancelada.'})
