from datetime import date, datetime, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import VacationRequest
from .services import can_request_full_day, can_request_partial, get_balance


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
        'boss': boss,
        'employees': employees,
        'balance': balance,
    })


@login_required
def events_json(request):
    scope = request.GET.get('scope', 'mine')
    qs = VacationRequest.objects.select_related('employee')

    if scope == 'all' and (_is_hr(request.user) or _is_leader(request.user)):
        qs = qs.exclude(status=VacationRequest.Status.CANCELLED)
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

    # Determinar trabajador objetivo y privilegios
    is_admin = _is_admin(request.user)
    captured_by = request.user

    if as_leader:
        if not _is_leader(request.user):
            return HttpResponseForbidden('Solo líderes o admin.')
        emp_id = request.POST.get('employee_id')
        employee = get_object_or_404(User, pk=emp_id)
        # Validar saldo del trabajador (a menos que sea admin)
        if not is_admin:
            ok, msg = (
                can_request_full_day(employee) if kind == VacationRequest.Kind.FULL_DAY
                else can_request_partial(employee)
            )
            if not ok:
                return JsonResponse({'success': False, 'message': msg}, status=400)
        req = VacationRequest.objects.create(
            employee=employee, kind=kind, partial_type=partial_type,
            date=d, event_time=event_time, reason=reason,
            status=VacationRequest.Status.PENDING_HR,
            origin=VacationRequest.Origin.LEADER,
            leader_acted_by=request.user, leader_acted_at=timezone.now(),
            captured_by=captured_by,
        )
    elif as_hr:
        # RH (o admin) captura a nombre del trabajador, pero NO firma por el líder.
        if not _is_hr(request.user):
            return HttpResponseForbidden('Solo RH o admin.')
        emp_id = request.POST.get('employee_id')
        employee = get_object_or_404(User, pk=emp_id)
        if not is_admin:
            ok, msg = (
                can_request_full_day(employee) if kind == VacationRequest.Kind.FULL_DAY
                else can_request_partial(employee)
            )
            if not ok:
                return JsonResponse({'success': False, 'message': msg}, status=400)
        emp_profile = getattr(employee, 'profile', None)
        leader = getattr(emp_profile, 'boss', None) if emp_profile else None
        req = VacationRequest.objects.create(
            employee=employee, kind=kind, partial_type=partial_type,
            date=d, event_time=event_time, reason=reason,
            status=VacationRequest.Status.PENDING_LEADER,
            origin=VacationRequest.Origin.EMPLOYEE,
            requested_to_leader=leader,
            captured_by=captured_by,
        )
    else:
        # Trabajador (o admin para sí mismo)
        if not is_admin:
            ok, msg = (
                can_request_full_day(request.user) if kind == VacationRequest.Kind.FULL_DAY
                else can_request_partial(request.user)
            )
            if not ok:
                return JsonResponse({'success': False, 'message': msg}, status=400)
        profile = getattr(request.user, 'profile', None)
        leader = getattr(profile, 'boss', None) if profile else None
        req = VacationRequest.objects.create(
            employee=request.user, kind=kind, partial_type=partial_type,
            date=d, event_time=event_time, reason=reason,
            status=VacationRequest.Status.PENDING_LEADER,
            origin=VacationRequest.Origin.EMPLOYEE,
            requested_to_leader=leader,
            captured_by=captured_by,
        )
    return JsonResponse({'success': True, 'id': req.id, 'message': 'Solicitud registrada.'})


@login_required
def leader_inbox(request):
    if not _is_leader(request.user):
        return HttpResponseForbidden()
    pending = VacationRequest.objects.filter(
        status=VacationRequest.Status.PENDING_LEADER,
    ).select_related('employee').order_by('date')
    return render(request, 'vacations/leader_inbox.html', {'pending': pending})


@login_required
def hr_inbox(request):
    if not _is_hr(request.user):
        return HttpResponseForbidden()
    pending = VacationRequest.objects.filter(
        status=VacationRequest.Status.PENDING_HR,
    ).select_related('employee').order_by('date')
    return render(request, 'vacations/hr_inbox.html', {'pending': pending})


@login_required
@require_POST
def leader_act(request, pk):
    if not _is_leader(request.user):
        return HttpResponseForbidden()
    req = get_object_or_404(VacationRequest, pk=pk)
    action = request.POST.get('action')
    if req.status != VacationRequest.Status.PENDING_LEADER:
        messages.error(request, 'La solicitud ya no está pendiente del líder.')
        return redirect('vacations:leader_inbox')
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
    return redirect('vacations:leader_inbox')


@login_required
@require_POST
def hr_act(request, pk):
    if not _is_hr(request.user):
        return HttpResponseForbidden()
    req = get_object_or_404(VacationRequest, pk=pk)
    action = request.POST.get('action')
    if req.status != VacationRequest.Status.PENDING_HR:
        messages.error(request, 'La solicitud ya no está pendiente de RH.')
        return redirect('vacations:hr_inbox')
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
    return redirect('vacations:hr_inbox')


@login_required
@require_POST
def cancel_request(request, pk):
    """
    Trabajador (dueño): solo si la fecha aún no llegó.
    Líder, RH o admin: en cualquier momento, incluso aprobada.
    """
    req = get_object_or_404(VacationRequest, pk=pk)
    user = request.user
    is_owner = req.employee_id == user.id
    is_priv = _is_leader(user) or _is_hr(user)

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
