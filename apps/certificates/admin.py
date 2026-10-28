from django.contrib import admin

from .models import Certificate


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    """Somente leitura: certificados são criados pelo portal/painel."""

    list_display = ("code", "aluno", "curso", "issued_at", "issued_by")
    search_fields = ("code", "enrollment__medico__nome", "enrollment__course__title")
    list_filter = ("issued_at",)
    readonly_fields = (
        "enrollment",
        "code",
        "issued_at",
        "issued_by",
        "pdf_path",
        "payload",
    )

    def has_add_permission(self, request):
        return False
