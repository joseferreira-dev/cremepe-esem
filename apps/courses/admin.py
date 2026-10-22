from django.contrib import admin

from .models import AuditLog, Course, Enrollment, Progress, Video, WatchSession
from .services import course_coverage, reset_progress


class VideoInline(admin.TabularInline):
    model = Video
    extra = 0
    fields = ("order", "title", "status", "duration", "is_required")
    readonly_fields = ("status", "duration")
    ordering = ("order",)


class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 0
    fields = ("medico", "status", "min_percent", "deadline", "approved_at")
    autocomplete_fields = ("medico",)
    readonly_fields = ("approved_at",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "edition",
        "status",
        "min_percent",
        "video_count",
        "total_duration_formatado",
        "matriculados",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("title", "edition", "description")
    inlines = [VideoInline, EnrollmentInline]
    fieldsets = (
        (None, {"fields": ("title", "edition", "description", "status")}),
        (
            "Regras de aprovação",
            {
                "fields": ("min_percent", "sequential", "unlock_threshold", "deadline"),
                "description": (
                    "min_percent: percentual mínimo do curso a ser assistido para aprovação. "
                    "Ex.: 50 para turmas de reprovação, 100 para reciclagem."
                ),
            },
        ),
    )

    @admin.display(description="Duração total")
    def total_duration_formatado(self, obj: Course) -> str:
        from .services import format_hours

        return format_hours(obj.total_duration)

    @admin.display(description="Matriculados")
    def matriculados(self, obj: Course) -> int:
        return obj.enrollments.count()


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "order", "status", "duration_formatado", "created_at")
    list_filter = ("status", "course")
    search_fields = ("title", "course__title")
    list_editable = ("order",)
    readonly_fields = ("status", "duration", "qualities", "error_message")
    fields = (
        "course",
        "order",
        "title",
        "is_required",
        "status",
        "duration",
        "qualities",
        "original_filename",
        "original_path",
        "hls_path",
        "poster",
        "error_message",
    )

    @admin.display(description="Duração")
    def duration_formatado(self, obj: Video) -> str:
        from .services import format_hours

        return format_hours(obj.duration) if obj.duration else "—"

    actions = ("reprocessar",)

    @admin.action(description="Reenfileirar processamento")
    def reprocessar(self, request, queryset):
        from apps.media.services import enqueue_transcode

        for video in queryset:
            enqueue_transcode(video)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "medico",
        "course",
        "status",
        "min_percent",
        "cobertura_formatada",
        "ultima_atividade",
        "approved_at",
    )
    list_filter = ("status", "course")
    search_fields = ("medico__nome", "medico__cpf", "course__title")
    autocomplete_fields = ("medico", "course")
    list_select_related = ("medico", "course")
    readonly_fields = ("approved_at", "approved_by")
    actions = ("aprovar", "reprovar", "resetar_progresso", "marcar_em_andamento")

    @admin.display(description="Cobertura")
    def cobertura_formatada(self, obj: Enrollment) -> str:
        return f"{course_coverage(obj):.1f}%"

    @admin.display(description="Última atividade")
    def ultima_atividade(self, obj: Enrollment) -> str:
        progress = (
            Progress.objects.filter(enrollment=obj)
            .order_by("-last_seen_at")
            .values_list("last_seen_at", flat=True)
            .first()
        )
        return progress.strftime("%d/%m/%Y %H:%M") if progress else "—"

    @admin.action(description="Aprovar matrícula")
    def aprovar(self, request, queryset):
        from django.utils import timezone

        count = 0
        for enrollment in queryset:
            enrollment.status = Enrollment.Status.APPROVED
            enrollment.approved_at = timezone.now()
            enrollment.approved_by = request.user
            enrollment.save(update_fields=["status", "approved_at", "approved_by"])
            count += 1
        self.message_user(request, f"{count} matrícula(s) aprovada(s).")

    @admin.action(description="Reprovar matrícula")
    def reprovar(self, request, queryset):
        count = queryset.update(status=Enrollment.Status.REJECTED)
        self.message_user(request, f"{count} matrícula(s) reprovada(s).")

    @admin.action(description="Resetar progresso")
    def resetar_progresso(self, request, queryset):
        for enrollment in queryset:
            reset_progress(enrollment, actor=request.user)
        self.message_user(request, "Progresso resetado.")

    @admin.action(description="Marcar como em andamento")
    def marcar_em_andamento(self, request, queryset):
        queryset.update(status=Enrollment.Status.IN_PROGRESS)


@admin.register(Progress)
class ProgressAdmin(admin.ModelAdmin):
    list_display = (
        "enrollment",
        "video",
        "last_position",
        "furthest",
        "covered",
        "watch_seconds",
        "suspicious",
        "last_seen_at",
    )
    list_filter = ("video__course",)
    search_fields = ("enrollment__medico__nome", "enrollment__medico__cpf")
    readonly_fields = [field.name for field in Progress._meta.fields]


@admin.register(WatchSession)
class WatchSessionAdmin(admin.ModelAdmin):
    list_display = ("enrollment", "video", "started_at", "ended_at", "ip", "device")
    list_filter = ("video__course", "watchdog")
    search_fields = ("enrollment__medico__nome",)
    readonly_fields = [field.name for field in WatchSession._meta.fields]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "target", "ip")
    list_filter = ("action",)
    search_fields = ("target", "detail__medico")
    readonly_fields = [field.name for field in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
