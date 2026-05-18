"""
Context processors expuestos a todas las plantillas.

`vacation_cycle` calcula el progreso temporal del ciclo del usuario logueado
para que el sidebar (y cualquier otra plantilla) pueda mostrar una barra que
avisa cuando se acerca la fecha de reinicio de vacaciones.
"""
from datetime import date

from .services import cycle_start, cycle_end


def vacation_cycle(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    profile = getattr(user, 'profile', None)
    if not profile or not getattr(profile, 'hiring_date', None):
        return {}

    today = date.today()
    cs = cycle_start(profile, today)
    ce = cycle_end(profile, today)
    total = (ce - cs).days
    if total <= 0:
        return {}
    elapsed = max(0, (today - cs).days)
    progress = max(0, min(100, round((elapsed / total) * 100)))
    remaining = max(0, total - elapsed)

    # Nivel de urgencia visual: cuanto más se acerca al fin del ciclo,
    # mayor es la urgencia (corre el riesgo de perder días no tomados).
    if progress >= 85:
        level = 'critical'
    elif progress >= 65:
        level = 'warning'
    elif progress >= 35:
        level = 'normal'
    else:
        level = 'calm'

    return {
        'cycle_progress': progress,
        'cycle_level': level,
        'cycle_start_date': cs,
        'cycle_end_date': ce,
        'cycle_days_total': total,
        'cycle_days_elapsed': elapsed,
        'cycle_days_remaining': remaining,
    }
