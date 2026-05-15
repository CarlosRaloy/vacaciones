from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from aplications.users.forms import SignupForm, ProfileForm
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.urls import reverse
from .models import Profile

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
    """Actualiza el nivel del Profile desde POST."""
    try:
        level = int(post.get('level') or 1)
    except ValueError:
        level = 1
    if level not in VALID_LEVELS:
        level = 1
    profile.level = level
    boss_id = post.get('boss') or None
    if boss_id:
        try:
            profile.boss = User.objects.get(pk=boss_id, profile__level=4)
        except User.DoesNotExist:
            profile.boss = None
    else:
        profile.boss = None
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

    users = User.objects.select_related("profile", "profile__boss").all()
    leaders = User.objects.filter(profile__level=4).order_by('first_name', 'username')
    return render(request, "users/user_panel.html", {"users": users, "leaders": leaders})

@login_required
def ping(request):
    return render(request, 'blank.html', {'response': 'PONG (200)'})


def block_user(request):
    message = "Estas bloqueado por el momento, Espera que el administrador te desbloquee"
    return render(request, "block.html", {"message": message})
