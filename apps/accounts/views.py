"""Views de autenticação (login por CPF + senha)."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import AdminLoginForm, LoginByCpfForm
from .models import Medico, normalize_cpf
from .services import limpar_cookie_sessao
from apps.courses.services import record_audit


def _destino_pos_login(request, padrao: str) -> str:
    destino = request.POST.get("next") or request.GET.get("next")
    if (
        destino
        and destino != request.path
        and url_has_allowed_host_and_scheme(
            destino,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return destino
    return reverse(padrao)


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("painel_index" if request.user.is_staff else "my_courses")

    form = LoginByCpfForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        cpf = normalize_cpf(form.cleaned_data["cpf"])
        medico = (
            Medico.objects.select_related("user")
            .filter(cpf=cpf, ativo=True)
            .first()
        )
        if (
            medico
            and medico.user.is_active
            and medico.user.check_password(form.cleaned_data["senha"])
        ):
            # Separação de contas: quem é administrador entra pelo painel com
            # usuário e senha — o CPF abre apenas a área do médico.
            if medico.user.is_staff:
                messages.error(
                    request,
                    "Esta conta é administrativa. Entre pelo Acesso "
                    "administrativo com usuário e senha.",
                )
                return render(request, "accounts/login.html", {"form": form})
            login(request, medico.user)
            if medico.must_change_password and settings.EXIGIR_TROCA_SENHA:
                return redirect("password_change")
            return redirect(_destino_pos_login(request, "my_courses"))

        messages.error(request, "CPF ou senha inválidos.")

    return render(request, "accounts/login.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def admin_login_view(request):
    """Login exclusivo do painel administrativo (usuários com is_staff)."""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect(_destino_pos_login(request, "painel_index"))

    form = AdminLoginForm(
        request, data=request.POST if request.method == "POST" else None
    )
    if request.method == "POST":
        motivo_falha = "credenciais"
        if form.is_valid():
            user = form.get_user()
            if not user.is_staff:
                form.add_error(
                    None, "Esta conta não tem acesso ao painel administrativo."
                )
                motivo_falha = "conta_sem_permissao"
            elif Medico.objects.filter(user=user).exists():
                # Separação de contas: o painel não abre com a conta de médico.
                form.add_error(
                    None,
                    "Esta conta é de médico. O acesso administrativo usa uma conta "
                    "própria; na área do médico, entre com o CPF.",
                )
                motivo_falha = "conta_de_medico"
            else:
                login(request, user)
                record_audit(
                    "login_sucesso",
                    actor=request.user,
                    target=user.username,
                    ip=request.META.get("REMOTE_ADDR"),
                )
                return redirect(_destino_pos_login(request, "painel_index"))
        # Toda tentativa que não entrou fica registrada na auditoria.
        record_audit(
            "login_falha",
            actor=None,
            target=(request.POST.get("username") or "")[:150],
            detail={"motivo": motivo_falha},
            ip=request.META.get("REMOTE_ADDR"),
        )

    return render(
        request,
        "panel/login.html",
        {
            "form": form,
            "next_url": request.POST.get("next") or request.GET.get("next") or "",
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
def logout_view(request):
    """Encerra a sessao.

    Aceita apenas POST: um GET seria acionado por cliques acidentais ou por
    prefetch do navegador, derrubando a sessao do medico sem que ele perceba.
    """
    if request.method != "POST":
        return redirect("my_courses")
    era_admin = request.user.is_staff
    logout(request)
    resposta = redirect("painel_login" if era_admin else "login")
    # Apaga o cookie explicitamente (inclusive duplicatas com paths diferentes),
    # senao o navegador continua enviando um cookie orfao.
    return limpar_cookie_sessao(resposta)


@never_cache
@require_http_methods(["POST"])
def admin_logout_view(request):
    """Encerra a sessão administrativa e volta para o login do painel."""
    logout(request)
    resposta = redirect("painel_login")
    return limpar_cookie_sessao(resposta)


class MedicoPasswordChangeView(PasswordChangeView):
    template_name = "accounts/password_change.html"

    def get_success_url(self):
        return reverse(
            "painel_index" if self.request.user.is_staff else "my_courses"
        )

    def form_valid(self, form):
        response = super().form_valid(form)
        update_session_auth_hash(self.request, form.user)
        Medico.objects.filter(user=self.request.user).update(must_change_password=False)
        messages.success(self.request, "Senha alterada com sucesso.")
        return response


@login_required
def password_change_view(request):
    return MedicoPasswordChangeView.as_view()(request)
