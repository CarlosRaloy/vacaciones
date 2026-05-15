from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

"""
Levels
0 Bloqueado
1 Trabajador
2 Administrador
3 RH
4 Jefes / Coordinadores / Lideres / Gerentes
5 Visulizador / Auditor
"""

LEVEL_BLOCKED = 0
LEVEL_WORKER = 1
LEVEL_ADMIN = 2
LEVEL_HR = 3
LEVEL_LEADER = 4
LEVEL_AUDITOR = 5


class PositionUserModel(models.Model):
    name_position = models.CharField(max_length = 100)
    description_position = models.TextField(blank=True, null=True)
    def __str__(self):
        return self.name_position

class AreasUserModel(models.Model):
    name_area = models.CharField(max_length = 100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name_area

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    level = models.SmallIntegerField(default=0) # Nivel de usuario
    hiring_date = models.DateField(blank=True, null=True) # Fecha de ingreso
    days_vacations = models.IntegerField(blank=True, null=True) # Dias de vacaciones 
    payroll_number = models.CharField(max_length=100, blank=True, null=True) # Numero de nomina
    position = models.ForeignKey(PositionUserModel, on_delete=models.SET_NULL, blank=True, null=True) # Posicion
    area = models.ForeignKey(AreasUserModel, on_delete=models.SET_NULL, blank=True, null=True) # Departamentos
    boss = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True,
        related_name='subordinates'
    ) # Jefe inmediato registrado en el sistema (level=4)
    boss_name = models.CharField(max_length=150, blank=True, null=True) # Jefe inmediato escrito a mano (fuera del sistema)
    signature = models.ImageField(upload_to='users/profile_signatures', blank=True, null=True)
    theme = models.CharField(max_length=15, default='default')
    picture = models.ImageField(upload_to='user/pictures', blank=True, null=True)
    tour = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.user.username

    @property
    def is_blocked(self) -> bool:
        return self.level == LEVEL_BLOCKED

    @property
    def is_admin(self) -> bool:
        return self.level == LEVEL_ADMIN

    @property
    def is_hr(self) -> bool:
        return self.level == LEVEL_HR

    @property
    def is_leader(self) -> bool:
        return self.level == LEVEL_LEADER

    @property
    def is_auditor(self) -> bool:
        return self.level == LEVEL_AUDITOR


@receiver(post_save, sender=User)
def ensure_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)
