from django.contrib import admin

from .models import Job, UploadSession


@admin.register(UploadSession)
class UploadSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "video",
        "original_filename",
        "total_size",
        "total_chunks",
        "status",
        "updated_at",
    )
    list_filter = ("status",)
    search_fields = ("original_filename", "video__title")
    readonly_fields = [field.name for field in UploadSession._meta.fields]


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "video", "status", "progress", "created_at", "finished_at")
    list_filter = ("status", "kind")
    search_fields = ("video__title",)
    readonly_fields = [field.name for field in Job._meta.fields]
    actions = ("reexecutar",)

    @admin.action(description="Reenfileirar tarefa")
    def reexecutar(self, request, queryset):
        for job in queryset:
            job.status = Job.Status.QUEUED
            job.progress = 0
            job.error = ""
            job.finished_at = None
            job.save()
