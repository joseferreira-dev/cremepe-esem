"""Formulários do painel administrativo."""

from django import forms

from apps.accounts.models import Medico, normalize_cpf
from apps.accounts.services import criar_conta_medico
from apps.courses.models import Course, Enrollment, Video


class CursoForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = (
            "title",
            "edition",
            "description",
            "cover",
            "status",
            "min_percent",
            "sequential",
            "unlock_threshold",
            "deadline",
        )
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "campo", "placeholder": "Ex.: 17º Curso de Atualização em Ética e Bioética"}
            ),
            "edition": forms.TextInput(
                attrs={"class": "campo", "placeholder": "Ex.: 17º Curso — Turma A"}
            ),
            "description": forms.Textarea(
                attrs={"class": "campo", "rows": 4, "placeholder": "Descrição do curso"}
            ),
            "status": forms.Select(attrs={"class": "campo"}),
            "min_percent": forms.NumberInput(
                attrs={"class": "campo", "step": "0.01", "min": "0", "max": "100"}
            ),
            "sequential": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-marca-600 focus:ring-marca-500"}),
            "unlock_threshold": forms.NumberInput(
                attrs={"class": "campo", "step": "0.01", "min": "0", "max": "100"}
            ),
            "deadline": forms.DateInput(attrs={"class": "campo", "type": "date"}),
            "cover": forms.ClearableFileInput(attrs={"class": "campo"}),
        }
        labels = {
            "title": "Título do curso",
            "edition": "Turma / Edição",
            "description": "Descrição",
            "cover": "Imagem de capa",
            "status": "Situação",
            "min_percent": "Percentual mínimo para aprovação (%)",
            "sequential": "Liberar os vídeos sequencialmente",
            "unlock_threshold": "% do vídeo anterior necessário para liberar o próximo",
            "deadline": "Prazo para conclusão",
        }


class VideoForm(forms.ModelForm):
    class Meta:
        model = Video
        fields = ("course", "order", "title", "is_required")
        widgets = {
            "course": forms.Select(attrs={"class": "campo"}),
            "order": forms.NumberInput(attrs={"class": "campo", "min": "1"}),
            "title": forms.TextInput(attrs={"class": "campo", "placeholder": "Ex.: Encontro 1"}),
            "is_required": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-marca-600 focus:ring-marca-500"}),
        }
        labels = {
            "course": "Curso",
            "order": "Ordem de exibição",
            "title": "Título do vídeo",
            "is_required": "Vídeo obrigatório",
        }


class MedicoForm(forms.ModelForm):
    # Declarado aqui para aceitar a máscara (000.000.000-00); a normalização
    # acontece em clean_cpf.
    cpf = forms.CharField(
        label="CPF",
        max_length=14,
        widget=forms.TextInput(attrs={"class": "campo", "placeholder": "000.000.000-00"}),
    )
    senha_provisoria = forms.CharField(
        required=False,
        label="Senha provisória",
        help_text=(
            "Deixe em branco para usar a senha padrão do CREMEPE "
            "(PrimeiroNome@CPFsemseparadores). O médico será obrigado a trocá-la."
        ),
        widget=forms.TextInput(attrs={"class": "campo", "autocomplete": "new-password"}),
    )
    email = forms.EmailField(required=False, label="E-mail")
    ativo = forms.BooleanField(required=False, initial=True, label="Ativo")

    class Meta:
        model = Medico
        fields = ("nome", "cpf", "crm", "telefone", "email", "ativo")
        widgets = {
            "nome": forms.TextInput(attrs={"class": "campo", "placeholder": "Nome completo"}),
            "cpf": forms.TextInput(attrs={"class": "campo", "placeholder": "000.000.000-00"}),
            "crm": forms.TextInput(attrs={"class": "campo", "placeholder": "PE-00000"}),
            "telefone": forms.TextInput(attrs={"class": "campo", "placeholder": "(81) 99999-0000"}),
            "email": forms.EmailInput(attrs={"class": "campo"}),
            "ativo": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-marca-600 focus:ring-marca-500"}),
        }
        labels = {"nome": "Nome completo", "cpf": "CPF", "crm": "CRM", "telefone": "Telefone"}

    def clean_cpf(self):
        cpf = normalize_cpf(self.cleaned_data.get("cpf", ""))
        if len(cpf) != 11:
            raise forms.ValidationError("Informe um CPF com 11 dígitos.")
        queryset = Medico.objects.filter(cpf=cpf)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Já existe um médico cadastrado com este CPF.")
        return cpf

    def save(self, commit=True):
        medico = super().save(commit=False)
        senha = (self.cleaned_data.get("senha_provisoria") or "").strip()
        if not commit:
            return medico

        if medico.user_id is None:
            # Conta nova: senha padrão do CREMEPE e troca obrigatória.
            self.senha_utilizada = criar_conta_medico(medico, senha or None)
            medico.save()
        else:
            medico.user.email = medico.email
            if senha:
                medico.user.set_password(senha)
                medico.user.save(update_fields=["email", "password"])
                medico.must_change_password = False
            medico.save()
        return medico


class MatriculaForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ("medico", "course", "min_percent", "deadline")
        widgets = {
            "medico": forms.Select(attrs={"class": "campo"}),
            "course": forms.Select(attrs={"class": "campo"}),
            "min_percent": forms.NumberInput(attrs={"class": "campo", "step": "0.01", "min": "0", "max": "100"}),
            "deadline": forms.DateInput(attrs={"class": "campo", "type": "date"}),
        }
        labels = {
            "medico": "Médico",
            "course": "Curso",
            "min_percent": "Percentual exigido (%)",
            "deadline": "Prazo",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["medico"].queryset = Medico.objects.filter(ativo=True).order_by("nome")
        self.fields["course"].queryset = Course.objects.filter(status=Course.Status.ACTIVE).order_by("-created_at")

    def clean(self):
        limpo = super().clean()
        medico = limpo.get("medico")
        curso = limpo.get("course")
        if medico and curso:
            if Enrollment.objects.filter(medico=medico, course=curso).exclude(
                pk=self.instance.pk
            ).exists():
                raise forms.ValidationError("Este médico já está matriculado neste curso.")
        return limpo
