import base64
import binascii
import calendar
import os
from datetime import date

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from aplications.users.forms import SignupForm, ProfileForm
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.core.files.base import ContentFile

from aplications.vacations.models import VacationRequest
from aplications.vacations.services import cycle_start as vac_cycle_start, cycle_end as vac_cycle_end
from .models import Profile, PositionUserModel, AreasUserModel

def _home_for(user) -> str:
    """
    Devuelve la URL absoluta (string) a la que debe ir el usuario
    según su nivel de perfil:
      · level 0 (bloqueado)            → vista de bloqueo
      · level 1 (trabajador)           → calendario de vacaciones
      · level 2/3/4/5 y superuser      → dashboard global
    """
    profile = getattr(user, "profile", None)
    level = getattr(profile, "level", 0)
    if user.is_superuser:
        return reverse("users:ping")
    if level == 0:
        return reverse("users:block")
    if level == 1:
        return reverse("vacations:calendar")
    return reverse("users:ping")

def login_view(request):
    registrado = request.GET.get('registrado') == '1'

    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            # redirección según nivel
            return redirect(_home_for(user))
        else:
            return render(request, 'users/login.html', {
                'error': 'El usuario o la contraseña son inválidos',
                'registrado': registrado
            })

    # GET
    return render(request, 'users/login.html', {
        'registrado': registrado
    })

@login_required
def logout_view(request):
    logout(request)
    return redirect('users:login')

def signup(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            form.save()
            # redirigimos con ?registrado=1
            return redirect(f"{reverse('users:login')}?registrado=1")
    else:
        form = SignupForm()

    return render(request, 'users/signup.html', {
        'form': form
    })

@login_required
def update_profile(request):
    profile = request.user.profile

    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data

            # Actualiza theme
            profile.theme = data.get("theme") or profile.theme

            # No borres la foto si el usuario no subió una nueva
            new_picture = data.get("picture")
            if new_picture:
                profile.picture = new_picture

            profile.save()

            # Redirección correcta según nivel (admin y compras al panel)
            return redirect(_home_for(request.user))
    else:
        # Precarga theme actual para que el select salga con el valor actual
        form = ProfileForm(initial={"theme": getattr(profile, "theme", "")})

    return render(
        request,
        "users/update_profile.html",
        {
            "profile": profile,
            "user": request.user,
            "form": form,
        },
    )

def _is_admin(user) -> bool:
    return bool(getattr(getattr(user, 'profile', None), 'is_admin', False)) or user.is_superuser


def _can_manage_users(user) -> bool:
    """Admin, superuser y RH pueden gestionar usuarios, puestos y áreas."""
    if _is_admin(user):
        return True
    return bool(getattr(getattr(user, 'profile', None), 'is_hr', False))


def _tenure(start: date | None, end: date | None = None):
    """
    Devuelve un dict con la antigüedad expresada en años / meses / días entre
    `start` (fecha de contratación) y `end` (por defecto hoy). Devuelve None
    si no hay fecha de contratación o si está en el futuro.
    """
    if not start:
        return None
    end = end or date.today()
    if start > end:
        return None
    years = end.year - start.year
    months = end.month - start.month
    days = end.day - start.day
    if days < 0:
        months -= 1
        prev_month = end.month - 1 if end.month > 1 else 12
        prev_year = end.year if end.month > 1 else end.year - 1
        days += calendar.monthrange(prev_year, prev_month)[1]
    if months < 0:
        years -= 1
        months += 12
    total_days = (end - start).days
    # Cumplemes: hoy coincide con el día del mes en que entró → 🎂 al lado de la fecha.
    is_month_anniversary = start.day == end.day
    # Aniversario anual completo: además coincide el mes y ya pasó al menos un año.
    is_year_anniversary = (
        is_month_anniversary
        and start.month == end.month
        and total_days > 0
    )
    return {
        'years': years,
        'months': months,
        'days': days,
        'total_days': total_days,
        'is_month_anniversary': is_month_anniversary,
        'is_year_anniversary': is_year_anniversary,
    }


VALID_LEVELS = {0, 1, 2, 3, 4, 5}


def _apply_profile_fields(profile, post):
    """Actualiza el Profile desde POST (nivel, organización, jefe)."""
    previous_boss_id = profile.boss_id  # para detectar cambio de jefe al final
    try:
        level = int(post.get('level') or 1)
    except ValueError:
        level = 1
    if level not in VALID_LEVELS:
        level = 1
    profile.level = level

    # Nº nómina
    profile.payroll_number = (post.get('payroll_number') or '').strip() or None

    # Fecha de ingreso y días de vacaciones asignados
    hiring = (post.get('hiring_date') or '').strip()
    if hiring:
        try:
            from datetime import datetime as _dt
            profile.hiring_date = _dt.strptime(hiring, '%Y-%m-%d').date()
        except ValueError:
            pass  # ignora valor inválido, conserva el actual
    else:
        profile.hiring_date = None

    days_v = (post.get('days_vacations') or '').strip()
    if days_v:
        try:
            profile.days_vacations = int(days_v)
        except ValueError:
            pass
    else:
        profile.days_vacations = None

    # Puesto y Área (FK)
    pos_id = post.get('position') or None
    profile.position = PositionUserModel.objects.filter(pk=pos_id).first() if pos_id else None
    area_id = post.get('area') or None
    profile.area = AreasUserModel.objects.filter(pk=area_id).first() if area_id else None

    # Jefe: dos modos — "list" (FK boss a líder) o "manual" (boss_name texto).
    boss_mode = post.get('boss_mode') or 'list'
    if boss_mode == 'manual':
        profile.boss = None
        profile.boss_name = (post.get('boss_name') or '').strip() or None
    else:
        boss_id = post.get('boss') or None
        if boss_id:
            try:
                profile.boss = User.objects.get(pk=boss_id, profile__level=4)
                profile.boss_name = None
            except User.DoesNotExist:
                profile.boss = None
                profile.boss_name = None
        else:
            profile.boss = None
            profile.boss_name = None

    profile.save()

    # Si el jefe inmediato cambió, las solicitudes que estaban "Pendiente de
    # líder" siguen apuntando al jefe viejo (o a None si no había). Reasigna
    # esas solicitudes al jefe actual para que el nuevo líder pueda aprobarlas
    # desde su bandeja / calendario.
    if profile.boss_id != previous_boss_id:
        VacationRequest.objects.filter(
            employee_id=profile.user_id,
            status=VacationRequest.Status.PENDING_LEADER,
        ).update(requested_to_leader=profile.boss)


@login_required
def user_panel(request):
    if not _can_manage_users(request.user):
        return render(request, 'block.html', {
            'message': 'Solo administradores y RH pueden acceder al panel de usuarios.',
        }, status=403)

    if request.method == "POST":
        action = request.POST.get("action")
        user_id = request.POST.get("user_id")

        if action == "create":
            try:
                user = User.objects.create_user(
                    username=request.POST["username"],
                    password=request.POST["password"],
                    first_name=request.POST["first_name"],
                    last_name=request.POST["last_name"],
                    email=request.POST.get("email", "").strip()
                )
                _apply_profile_fields(user.profile, request.POST)
                return JsonResponse({"success": True, "message": "Usuario creado exitosamente."})
            except Exception as e:
                return JsonResponse({"success": False, "message": f"Error al crear usuario: {str(e)}"})

        elif action == "edit" and user_id:
            try:
                user = get_object_or_404(User, pk=user_id)
                user.first_name = request.POST["first_name"]
                user.last_name = request.POST["last_name"]
                user.email = request.POST.get("email", "").strip()
                user.save()
                _apply_profile_fields(user.profile, request.POST)
                return JsonResponse({"success": True, "message": "Usuario actualizado correctamente."})
            except Exception as e:
                return JsonResponse({"success": False, "message": f"Error al editar usuario: {str(e)}"})

        elif action == "delete" and user_id:
            try:
                user = get_object_or_404(User, pk=user_id)
                if user.pk == request.user.pk:
                    return JsonResponse({"success": False, "message": "No puedes eliminar tu propio usuario."})
                user.delete()
                return JsonResponse({"success": True, "message": "Usuario eliminado correctamente."})
            except Exception as e:
                return JsonResponse({"success": False, "message": f"Error al eliminar usuario: {str(e)}"})

    users = list(User.objects.select_related(
        "profile", "profile__boss", "profile__position", "profile__area",
    ).all())
    today = date.today()
    for u in users:
        hd = getattr(getattr(u, 'profile', None), 'hiring_date', None)
        u.tenure = _tenure(hd, today)
    leaders = User.objects.filter(profile__level=4).order_by('first_name', 'username')
    positions = PositionUserModel.objects.all().order_by('name_position')
    areas = AreasUserModel.objects.all().order_by('name_area')
    return render(request, "users/user_panel.html", {
        "users": users,
        "leaders": leaders,
        "positions": positions,
        "areas": areas,
    })


# ──────────────────────── Catálogos: Puestos y Áreas ────────────────────────

def _catalog_panel(request, model, name_attr, panel_title, list_title, template):
    """Helper común para los paneles de catálogo (Puestos / Áreas)."""
    if not _can_manage_users(request.user):
        return render(request, 'block.html', {
            'message': 'Solo administradores y RH pueden acceder a este catálogo.',
        }, status=403)

    if request.method == 'POST':
        action = request.POST.get('action')
        pk = request.POST.get('id')
        name = (request.POST.get('name') or '').strip()
        description = (request.POST.get('description') or '').strip()

        if action == 'create':
            if not name:
                return JsonResponse({'success': False, 'message': 'El nombre es obligatorio.'}, status=400)
            obj = model(**{name_attr: name})
            if hasattr(obj, 'description'):
                obj.description = description
            else:
                obj.description_position = description
            obj.save()
            return JsonResponse({'success': True, 'message': 'Creado correctamente.', 'id': obj.pk, 'name': name})

        if action == 'edit' and pk:
            obj = get_object_or_404(model, pk=pk)
            if not name:
                return JsonResponse({'success': False, 'message': 'El nombre es obligatorio.'}, status=400)
            setattr(obj, name_attr, name)
            if hasattr(obj, 'description'):
                obj.description = description
            else:
                obj.description_position = description
            obj.save()
            return JsonResponse({'success': True, 'message': 'Actualizado correctamente.'})

        if action == 'delete' and pk:
            obj = get_object_or_404(model, pk=pk)
            obj.delete()
            return JsonResponse({'success': True, 'message': 'Eliminado correctamente.'})

        return JsonResponse({'success': False, 'message': 'Acción inválida.'}, status=400)

    items = model.objects.all().order_by(name_attr)
    return render(request, template, {
        'items': items,
        'panel_title': panel_title,
        'list_title': list_title,
        'name_attr': name_attr,
    })


@login_required
def positions_panel(request):
    return _catalog_panel(
        request,
        model=PositionUserModel,
        name_attr='name_position',
        panel_title='Puestos',
        list_title='Catálogo de Puestos',
        template='users/_catalog_panel.html',
    )


@login_required
def areas_panel(request):
    return _catalog_panel(
        request,
        model=AreasUserModel,
        name_attr='name_area',
        panel_title='Áreas',
        list_title='Catálogo de Áreas',
        template='users/_catalog_panel.html',
    )


# ──────────────────────── Firma ────────────────────────

@login_required
@require_POST
def signature_act(request):
    """
    POST con:
      - user_id: id del usuario destino
      - action: 'save' | 'delete'
      - data_url: dataURL del PNG (solo en save)
    Solo admin puede actuar sobre otros usuarios; cualquier user puede sobre sí mismo.
    """
    user_id = request.POST.get('user_id')
    action = request.POST.get('action', 'save')
    target = get_object_or_404(User, pk=user_id) if user_id else request.user

    if target.pk != request.user.pk and not _can_manage_users(request.user):
        return JsonResponse({'success': False, 'message': 'No autorizado.'}, status=403)

    profile = target.profile

    # Siempre borrar la imagen previa del filesystem antes de cualquier acción.
    old_path = None
    if profile.signature:
        try:
            old_path = profile.signature.path
        except (ValueError, NotImplementedError):
            old_path = None

    if action == 'delete':
        if profile.signature:
            profile.signature.delete(save=False)
        profile.signature = None
        profile.save()
        return JsonResponse({'success': True, 'message': 'Firma eliminada.'})

    if action != 'save':
        return JsonResponse({'success': False, 'message': 'Acción inválida.'}, status=400)

    data_url = request.POST.get('data_url') or ''
    if ';base64,' not in data_url:
        return JsonResponse({'success': False, 'message': 'Imagen inválida.'}, status=400)
    header, b64 = data_url.split(';base64,', 1)
    if 'image/png' not in header:
        return JsonResponse({'success': False, 'message': 'Solo se acepta PNG.'}, status=400)
    try:
        png_bytes = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return JsonResponse({'success': False, 'message': 'No se pudo decodificar la imagen.'}, status=400)

    # Si había firma previa, borrarla del filesystem (usar el storage del field).
    if profile.signature:
        profile.signature.delete(save=False)

    filename = f'user_{target.pk}.png'
    profile.signature.save(filename, ContentFile(png_bytes), save=True)

    # Borra el archivo huérfano si el storage le puso un sufijo random distinto.
    if old_path and os.path.exists(old_path):
        try:
            new_path = profile.signature.path
            if new_path != old_path:
                os.remove(old_path)
        except (ValueError, NotImplementedError, OSError):
            pass

    return JsonResponse({
        'success': True,
        'message': 'Firma guardada.',
        'url': profile.signature.url,
    })

def _initials(full_name: str, fallback: str) -> str:
    parts = [p for p in full_name.split() if p]
    if parts:
        return ''.join(p[0] for p in parts[:2]).upper()
    return (fallback[:2] or '??').upper()


def _dashboard_scope_users(request_user):
    """
    Devuelve los usuarios que entran en el dashboard según el rol del que mira.
    - Admin / RH / Auditor / Superuser → todos los activos.
    - Líder → solo sus subordinados directos.
    - Cualquier otro → solo a sí mismo (datos limitados, fallback).
    """
    profile = getattr(request_user, 'profile', None)
    is_admin = bool(getattr(profile, 'is_admin', False)) or request_user.is_superuser
    is_hr = bool(getattr(profile, 'is_hr', False))
    is_auditor = bool(getattr(profile, 'is_auditor', False))
    is_leader = bool(getattr(profile, 'is_leader', False))

    qs = User.objects.select_related(
        'profile', 'profile__area', 'profile__position', 'profile__boss',
    ).filter(is_active=True)

    if is_admin or is_hr or is_auditor:
        return qs.exclude(profile__level=0)
    if is_leader:
        return qs.filter(profile__boss=request_user)
    return qs.filter(pk=request_user.pk)


@login_required
def ping(request):
    """
    Home / Dashboard de la organización. La página es siempre la misma, pero el
    conjunto de trabajadores que ve cada usuario está acotado en
    _dashboard_scope_users. El filtrado por año / mes / área / trabajador se
    aplica en el cliente para que los filtros respondan al instante.
    """
    workers_qs = _dashboard_scope_users(request.user)
    worker_ids = list(workers_qs.values_list('id', flat=True))

    workers = []
    today = date.today()
    for u in workers_qs:
        p = getattr(u, 'profile', None)
        full = (u.get_full_name() or u.username).strip()
        area = getattr(getattr(p, 'area', None), 'name_area', None) if p else None
        area_id = getattr(getattr(p, 'area', None), 'pk', None) if p else None
        position = getattr(getattr(p, 'position', None), 'name_position', None) if p else None
        hiring = getattr(p, 'hiring_date', None) if p else None
        tenure_years = None
        cycle_progress_pct = None
        if hiring and hiring <= today:
            tenure_years = round((today - hiring).days / 365.25, 2)
            cs = vac_cycle_start(p, today)
            ce = vac_cycle_end(p, today)
            total = (ce - cs).days
            if total > 0:
                elapsed = max(0, (today - cs).days)
                cycle_progress_pct = round((elapsed / total) * 100, 1)
        workers.append({
            'id': u.id,
            'name': full or u.username,
            'username': u.username,
            'initials': _initials(full, u.username),
            'level': getattr(p, 'level', 0) if p else 0,
            'area_id': area_id,
            'area': area or 'Sin área',
            'position': position or 'Sin puesto',
            'payroll': getattr(p, 'payroll_number', None) if p else None,
            'hiring_date': hiring.isoformat() if hiring else None,
            'tenure_years': tenure_years,
            'cycle_progress_pct': cycle_progress_pct,
            'allowance': int(getattr(p, 'days_vacations', 0) or 0),
        })

    # Catálogo de áreas presentes en el set de trabajadores.
    seen_areas = {}
    for w in workers:
        seen_areas.setdefault(w['area_id'], w['area'])
    areas = [{'id': aid, 'name': name} for aid, name in seen_areas.items()]
    areas.sort(key=lambda x: (x['name'] or '').lower())

    # Solicitudes asociadas a esos trabajadores.
    requests_qs = (
        VacationRequest.objects
        .filter(employee_id__in=worker_ids)
        .values(
            'id', 'employee_id', 'date', 'kind', 'partial_type', 'status',
            'late_registration', 'created', 'leader_acted_at', 'hr_acted_at',
        )
    )
    requests_data = []
    years = set()
    for r in requests_qs:
        d = r['date']
        years.add(d.year)
        requests_data.append({
            'id': r['id'],
            'employee_id': r['employee_id'],
            'date': d.isoformat(),
            'year': d.year,
            'month': d.month,
            'weekday': d.weekday(),  # 0 = lunes
            'kind': r['kind'],
            'partial_type': r['partial_type'],
            'status': r['status'],
            'late_registration': bool(r['late_registration']),
        })

    years_sorted = sorted(years, reverse=True)
    if today.year not in years_sorted:
        years_sorted.insert(0, today.year)

    dashboard_payload = {
        'workers': workers,
        'areas': areas,
        'years': years_sorted,
        'requests': requests_data,
        'current_year': today.year,
        'current_month': today.month,
    }

    return render(request, 'dashboard.html', {
        'dashboard_payload': dashboard_payload,
        'total_workers': len(workers),
        'total_requests': len(requests_data),
    })


def block_user(request):
    message = "Estas bloqueado por el momento, Espera que el administrador te desbloquee"
    return render(request, "block.html", {"message": message})
