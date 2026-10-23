"""Formulários do painel administrativo."""

import csv
import io

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from apps.accounts.models import Medico, normalize_cpf
from apps.accounts.services import criar_conta_medico
from apps.courses.models import Course, Enrollment, Video


class AdministradorForm(forms.ModelForm):
    """Cria/edita usuários com acesso ao painel (staff), sem acesso ao Django."""

    senha = forms.CharField(
        required=False,
        label="Senha",
        help_text=(
            "Deixe em branco para manter a senha atual. Ao criar um novo usuário, "
            "a senha é obrigatória."
        ),
        widget=forms.TextInput(attrs={"class": "campo", "autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "is_active")
        widgets = {
            "username": forms.TextInput(
                attrs={"class": "campo", "placeholder": "Ex.: ana.souza"}
            ),
            "first_name": forms.TextInput(
                attrs={"class": "campo", "placeholder": "Nome"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": "campo", "placeholder": "Sobrenome"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "campo", "placeholder": "nome@exemplo.com"}
            ),
            "is_active": forms.CheckboxInput(
                attrs={"class": "h-4 w-4 rounded border-slate-300 text-marca-600 focus:ring-marca-500"}
            ),
        }
        labels = {
            "username": "Usuário",
            "first_name": "Nome",
            "last_name": "Sobrenome",
            "email": "E-mail",
            "is_active": "Ativo",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._novo = self.instance.pk is None
        if self._novo:
            self.fields["senha"].required = True
            self.fields["senha"].help_text = (
                "Senha inicial que o usuário deverá usar para entrar no painel."
            )

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if not username:
            raise forms.ValidationError("Informe o usuário.")
        queryset = User.objects.filter(username=username)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Já existe um usuário com este nome.")
        return username

    def clean(self):
        limpo = super().clean()
        senha = (limpo.get("senha") or "").strip()
        if self._novo and not senha:
            raise forms.ValidationError("Informe a senha inicial do usuário.")
        return limpo

    def save(self, commit=True):
        senha = (self.cleaned_data.get("senha") or "").strip()
        usuario = super().save(commit=False)
        usuario.is_staff = True
        # O acesso ao Django admin é exclusivo de superusuários (suporte de TI).
        usuario.is_superuser = False
        if senha:
            usuario.set_password(senha)
        if commit:
            usuario.save()
            if senha and not self._novo:
                # Mantém a sessão atual do usuário se ele estiver logado.
                from django.contrib.auth import update_session_auth_hash

                update_session_auth_hash(self.instance, senha) if usuario == self.instance else None
        return usuario


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
            "course": forms.Select(attrs={"class": "campo campo-busca-select"}),
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
    email = forms.EmailField(
        required=False,
        label="E-mail",
        widget=forms.EmailInput(
            attrs={"class": "campo", "placeholder": "nome@exemplo.com"}
        ),
    )
    ativo = forms.BooleanField(required=False, initial=True, label="Ativo")

    class Meta:
        model = Medico
        fields = ("nome", "cpf", "email", "ativo")
        widgets = {
            "nome": forms.TextInput(attrs={"class": "campo", "placeholder": "Nome completo"}),
            "cpf": forms.TextInput(attrs={"class": "campo", "placeholder": "000.000.000-00"}),
            "email": forms.EmailInput(
                attrs={"class": "campo", "placeholder": "nome@exemplo.com"}
            ),
            "ativo": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-marca-600 focus:ring-marca-500"}),
        }
        labels = {"nome": "Nome completo", "cpf": "CPF", "email": "E-mail"}

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
            "medico": forms.Select(attrs={"class": "campo campo-busca-select"}),
            "course": forms.Select(attrs={"class": "campo campo-busca-select"}),
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


class MatriculaLoteForm(forms.Form):
    """Matricula vários médicos em um único curso."""

    medicos = forms.ModelMultipleChoiceField(
        queryset=Medico.objects.none(),
        label="Médicos",
        widget=forms.CheckboxSelectMultiple(
            attrs={
                "class": "h-4 w-4 rounded border-slate-300 text-marca-600 focus:ring-marca-500"
            }
        ),
    )
    min_percent = forms.DecimalField(
        required=False,
        label="Percentual exigido (%)",
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(
            attrs={"class": "campo", "step": "0.01", "min": "0", "max": "100"}
        ),
    )
    deadline = forms.DateField(
        required=False,
        label="Prazo",
        widget=forms.DateInput(attrs={"class": "campo", "type": "date"}),
    )

    def __init__(self, *args, curso=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.curso = curso
        if curso:
            self.fields["medicos"].queryset = (
                Medico.objects.filter(ativo=True)
                .exclude(enrollments__course=curso)
                .order_by("nome")
            )
            self.fields["min_percent"].initial = curso.min_percent
            self.fields["deadline"].initial = curso.deadline

    def clean_medicos(self):
        medicos = self.cleaned_data["medicos"]
        if self.curso and not medicos:
            raise forms.ValidationError("Selecione pelo menos um médico.")
        return medicos

    def save(self):
        if self.curso is None:
            raise ValueError("O curso é obrigatório para matricular em lote.")
        min_percent = self.cleaned_data.get("min_percent") or self.curso.min_percent
        deadline = self.cleaned_data.get("deadline") or self.curso.deadline
        criadas = []
        with transaction.atomic():
            for medico in self.cleaned_data["medicos"]:
                matricula, criada = Enrollment.objects.get_or_create(
                    medico=medico,
                    course=self.curso,
                    defaults={"min_percent": min_percent, "deadline": deadline},
                )
                if criada:
                    criadas.append(matricula)
        return criadas


class MedicoLoteForm(forms.Form):
    """Cadastra médicos em lote via arquivo .csv ou dados colados.

    Formato de cada linha (separador padrão vírgula)::

        Nome completo, CPF, e-mail
    """

    arquivo = forms.FileField(
        label="Arquivo .csv",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={
                "class": "campo",
                "accept": ".csv,text/csv",
            }
        ),
        help_text=(
            "Envie um arquivo .csv com as colunas: nome, CPF e e-mail "
            "(o e-mail é opcional)."
        ),
    )
    dados = forms.CharField(
        label="Ou cole os dados aqui",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "campo font-mono",
                "rows": 10,
                "placeholder": (
                    "Maria da Silva,111.222.333-44,maria@exemplo.com\n"
                    "João Pereira,555.666.777-88,joao@exemplo.com"
                ),
            }
        ),
        help_text=(
            "Uma pessoa por linha, separada por vírgula: "
            "Nome completo, CPF, e-mail. O e-mail é opcional."
        ),
    )

    def _iterar_linhas(self):
        """Gera (numero, nome, cpf, email) a partir do arquivo ou do texto colado."""
        linhas_brutas = []
        arquivo = self.cleaned_data.get("arquivo")
        if arquivo is not None:
            try:
                conteudo = arquivo.read().decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise forms.ValidationError(
                    "O arquivo precisa estar em UTF-8."
                ) from exc
            linhas_brutas = conteudo.splitlines()

        texto = (self.cleaned_data.get("dados") or "").strip()
        if texto:
            linhas_brutas.extend(texto.splitlines())

        if not linhas_brutas:
            raise forms.ValidationError("Informe pelo menos um aluno.")

        # Detecta o separador: vírgula por padrão; ponto-e-vírgula aceito como
        # alternativa para colar direto do Excel pt-BR.
        primeira = next((l for l in linhas_brutas if l.strip()), "")
        separador = ";" if (";" in primeira and "," not in primeira) else ","

        numero = 0
        cpfs_no_lote = set()
        primeira_dados = True
        for linha in linhas_brutas:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            partes = [
                parte.strip() for parte in linha.split(separador)
            ]
            if len(partes) < 2 or len(partes) > 3:
                raise forms.ValidationError(
                    f"Linha {numero + 1}: use o formato Nome, CPF, e-mail."
                )
            # Pula o cabeçalho do CSV (ex.: "nome,cpf,e-mail").
            if primeira_dados and not any(ch.isdigit() for ch in partes[1]):
                primeira_dados = False
                continue
            primeira_dados = False
            numero += 1
            partes += [""] * (3 - len(partes))
            nome, cpf, email = partes
            cpf = normalize_cpf(cpf)

            if not nome:
                raise forms.ValidationError(f"Linha {numero}: informe o nome.")
            if len(cpf) != 11:
                raise forms.ValidationError(
                    f"Linha {numero}: o CPF deve ter 11 dígitos."
                )
            if cpf in cpfs_no_lote:
                raise forms.ValidationError(
                    f"Linha {numero}: CPF repetido dentro do lote."
                )
            if Medico.objects.filter(cpf=cpf).exists():
                raise forms.ValidationError(
                    f"Linha {numero}: já existe um médico cadastrado com este CPF."
                )
            if email:
                try:
                    validate_email(email)
                except ValidationError as exc:
                    raise forms.ValidationError(
                        f"Linha {numero}: e-mail inválido."
                    ) from exc

            cpfs_no_lote.add(cpf)
            yield {"nome": nome, "cpf": cpf, "email": email}

    def clean(self):
        limpo = super().clean()
        linhas = list(self._iterar_linhas())
        if not linhas:
            raise forms.ValidationError("Nenhuma linha válida foi encontrada.")
        self.linhas = linhas
        return limpo

    def save(self):
        criados = []
        with transaction.atomic():
            for dados in self.linhas:
                medico = Medico(ativo=True, **dados)
                senha = criar_conta_medico(medico)
                medico.save()
                criados.append({"medico": medico, "senha": senha})
        return criados
