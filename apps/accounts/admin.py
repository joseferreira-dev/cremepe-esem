from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Medico


# Restringe o Django admin (/admin/) ao time de suporte de TI (superusuários).
# O painel administrativo (/painel/) continua acessível a qualquer staff.
admin.site.has_permission = lambda request: (
    request.user.is_active and request.user.is_superuser
)


class MedicoInline(admin.StackedInline):
    model = Medico
    can_delete = False
    verbose_name_plural = "Dados do médico"
    fields = ("nome", "cpf", "crm", "email", "telefone", "ativo", "must_change_password")


class UserAdmin(BaseUserAdmin):
    inlines = [MedicoInline]


@admin.register(Medico)
class MedicoAdmin(admin.ModelAdmin):
    list_display = ("nome", "cpf", "crm", "email", "ativo", "must_change_password")
    list_filter = ("ativo", "must_change_password")
    search_fields = ("nome", "cpf", "crm", "email")
    readonly_fields = ("created_at",)
    actions = ("resetar_senha_provisoria", "ativar", "desativar")

    @admin.action(description="Redefinir senha provisória (obrigar troca)")
    def resetar_senha_provisoria(self, request, queryset):
        from apps.accounts.services import redefinir_senha

        for medico in queryset:
            redefinir_senha(medico)

    @admin.action(description="Ativar médicos")
    def ativar(self, request, queryset):
        queryset.update(ativo=True)

    @admin.action(description="Desativar médicos")
    def desativar(self, request, queryset):
        queryset.update(ativo=False)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
