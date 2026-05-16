import base64
import binascii
import os

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

from .models import Profile, PositionUserModel, AreasUserModel

def _home_for(user) -> str:
    """
    Devuelve la URL absoluta (string) a la que debe ir el usuario
    según su nivel de perfil. Cualquier nivel distinto de 0 (bloqueado)
    accede a la home; los permisos finos se aplican en cada vista.
    """
    profile = getattr(user, "profile", None)
    level = getattr(profile, "level", 0)
    url_name = "users:block" if level == 0 else "users:ping"
    return reverse(url_name)

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


VALID_LEVELS = {0, 1, 2, 3, 4, 5}


def _apply_profile_fields(profile, post):
    """Actualiza el Profile desde POST (nivel, organización, jefe)."""
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


@login_required
def user_panel(request):
    if not _is_admin(request.user):
        return render(request, 'block.html', {
            'message': 'Solo los administradores pueden acceder al panel de usuarios.',
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

    users = User.objects.select_related("profile", "profile__boss", "profile__position", "profile__area").all()
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
    if not _is_admin(request.user):
        return render(request, 'block.html', {
            'message': 'Solo los administradores pueden acceder a este catálogo.',
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

    if target.pk != request.user.pk and not _is_admin(request.user):
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

@login_required
def ping(request):
    return render(request, 'blank.html', {'response': 'PONG (200)'})


def block_user(request):
    message = "Estas bloqueado por el momento, Espera que el administrador te desbloquee"
    return render(request, "block.html", {"message": message})
