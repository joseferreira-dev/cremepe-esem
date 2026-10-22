"""Monta o cenário do 17º Curso de Atualização em Ética e Bioética.

Cria (ou reaproveita):
  * o curso com a imagem de capa oficial;
  * os dois vídeos (Encontro 1 — 25 de maio; Encontro 2 — 26 de maio),
    apontando para o HLS já processado (pasta media/hls/<nome>);
  * o médico, com a senha inicial padrão do CREMEPE;
  * a matrícula no curso.

Exemplo:
    python manage.py cenario_inicial --cpf 00000000000 \
        --nome "Médico de Teste" --hls "encontro-25:1,encontro-26:2"
"""

from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Medico, senha_padrao
from apps.accounts.services import criar_conta_medico
from apps.courses.models import Course, Enrollment, Progress, Video
from apps.media.services import duracao_hls, qualidades_hls

CURSO = {
    "title": "17º Curso de Atualização em Ética e Bioética para Residência Médica",
    "edition": "Webnário — 25 e 26 de maio de 2026",
    "description": (
        "Webnário do 17º Curso de Atualização em Ética e Bioética para Residência "
        "Médica, promovido pela Escola Superior de Ética Médica (ESEM) do CREMEPE, "
        "com apoio da Comissão Estadual de Residência Médica de Pernambuco (CEREMPE). "
        "Dois encontros — 25 e 26 de maio de 2026 — com carga horária total de "
        "6 horas, no formato de discussão de casos clínicos sobre ética e bioética "
        "na prática da residência médica."
    ),
}

ENCONTROS = [
    {"ordem": 1, "titulo": "Webnário — Dia 1 (25 de maio)", "hls": "encontro-25"},
    {"ordem": 2, "titulo": "Webnário — Dia 2 (26 de maio)", "hls": "encontro-26"},
]


class Command(BaseCommand):
    help = "Cria o curso, as gravações, o médico e a matrícula do cenário."

    def add_arguments(self, parser):
        parser.add_argument("--cpf", required=True)
        parser.add_argument("--nome", default="Médico de Teste")
        parser.add_argument("--crm", default="PE-00000")
        parser.add_argument("--email", default="")
        parser.add_argument(
            "--capa",
            default="",
            help=(
                "Imagem de capa (padrão: a extraída do slide de abertura do vídeo, "
                "gerada por scripts/gerar_assets_curso.sh)."
            ),
        )
        parser.add_argument(
            "--sem-hls",
            action="store_true",
            help="Não associar as gravações já processadas.",
        )
        parser.add_argument(
            "--resetar-senha",
            action="store_true",
            help="Restaura a senha inicial padrão e exige nova troca no 1º acesso.",
        )
        parser.add_argument(
            "--zerar-progresso",
            action="store_true",
            help="Zera o progresso do médico no curso (volta a 0%%).",
        )
        parser.add_argument(
            "--curso-id",
            type=int,
            default=None,
            help="ID do curso a configurar (padrão: busca pelo título).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.accounts.models import normalize_cpf

        cpf = normalize_cpf(options["cpf"])
        nome = options["nome"]

        # ------------------------------------------------------------------
        # Curso
        # ------------------------------------------------------------------
        if options["curso_id"]:
            curso = Course.objects.filter(pk=options["curso_id"]).first()
            if curso is None:
                curso = Course(pk=options["curso_id"])
        else:
            curso = Course.objects.filter(title=CURSO["title"]).first()

        if curso is None:
            curso = Course.objects.filter(edition=CURSO["edition"]).first()

        if curso is None:
            curso = Course.objects.order_by("pk").first()

        if curso is None:
            curso = Course()
        else:
            curso.title = CURSO["title"]

        curso.edition = CURSO["edition"]
        curso.description = CURSO["description"]
        curso.status = Course.Status.ACTIVE
        curso.min_percent = 50
        curso.sequential = True
        curso.save()
        self.stdout.write(f"Curso #{curso.pk}: {curso.title}")

        # A capa padrão é o slide de abertura extraído da gravação.
        if options["capa"]:
            capa = Path(options["capa"])
        else:
            gerada = Path(settings.MEDIA_ROOT) / "cortes" / "capa-17-curso.jpg"
            capa = gerada if gerada.exists() else Path("static/img/banner-curso.jpg")

        if capa.exists():
            if curso.cover:
                curso.cover.delete(save=False)
            with open(capa, "rb") as arquivo:
                curso.cover.save("capa-17-curso.jpg", File(arquivo), save=True)
            self.stdout.write(f"  capa aplicada a partir de {capa}")

        # ------------------------------------------------------------------
        # Gravações
        # ------------------------------------------------------------------
        raiz_hls = Path(settings.MEDIA_ROOT) / "hls"
        for encontro in ENCONTROS:
            video, criado = Video.objects.get_or_create(
                course=curso,
                order=encontro["ordem"],
                defaults={"title": encontro["titulo"]},
            )
            video.title = encontro["titulo"]
            pasta = raiz_hls / encontro["hls"]
            if not options["sem_hls"] and pasta.exists():
                destino = raiz_hls / str(video.pk)
                if destino.exists() and destino != pasta:
                    destino.unlink()
                if pasta != destino:
                    pasta.rename(destino)
                video.hls_path = f"hls/{video.pk}"
                video.duration = round(duracao_hls(destino), 2)
                video.qualities = qualidades_hls(destino)
                video.poster = f"hls/{video.pk}/poster.jpg"
                video.status = Video.Status.READY
                video.error_message = ""
            video.save()
            self.stdout.write(
                f"  Vídeo {video.order}. {video.title} "
                f"[{video.get_status_display()}] {video.duration or 0:.0f}s"
                + (" (novo)" if criado else "")
            )

        # ------------------------------------------------------------------
        # Médico
        # ------------------------------------------------------------------
        medico = Medico.objects.filter(cpf=cpf).first()
        senha = None
        if medico is None:
            from django.contrib.auth.models import User

            user = User.objects.create_user(username=f"medico-{cpf}")
            medico = Medico.objects.create(
                user=user,
                cpf=cpf,
                nome=nome,
                crm=options["crm"],
                email=options["email"],
                ativo=True,
            )
            senha = criar_conta_medico(medico)
            medico.save()
            self.stdout.write(f"Médico criado: {medico.nome} (CPF {medico.cpf})")
        else:
            medico.nome = nome
            medico.crm = options["crm"]
            if options["email"]:
                medico.email = options["email"]
            medico.save()
            if options["resetar_senha"]:
                from apps.accounts.services import preservar_sessoes, redefinir_senha

                redefinir_senha(medico)
                senha = senha_padrao(medico.nome, medico.cpf)
                # Em desenvolvimento, mantem a sessao aberta no navegador.
                sessoes = preservar_sessoes(medico.user)
                if sessoes:
                    self.stdout.write(f"  {sessoes} sessao(oes) mantida(s)")
            self.stdout.write(f"Médico já existia: {medico.nome} (CPF {medico.cpf})")

        if senha:
            self.stdout.write(f"  senha inicial: {senha}")
            self.stdout.write(
                f"  troca de senha obrigatória: {medico.must_change_password}"
            )

        # ------------------------------------------------------------------
        # Matrícula
        # ------------------------------------------------------------------
        matricula, _ = Enrollment.objects.get_or_create(
            medico=medico,
            course=curso,
            defaults={"min_percent": curso.min_percent},
        )
        if options["zerar_progresso"]:
            Progress.objects.filter(enrollment=matricula).delete()
            matricula.status = Enrollment.Status.ENROLLED
            matricula.approved_at = None
            matricula.approved_by = None
            matricula.save(
                update_fields=["status", "approved_at", "approved_by"]
            )
            self.stdout.write("  progresso zerado")
        self.stdout.write(f"Matrícula #{matricula.pk} ({matricula.get_status_display()})")

        self.stdout.write(self.style.SUCCESS("Cenário pronto."))
        self.stdout.write(
            f"ACESSO_MEDICO=cpf:{cpf} senha:{senha or '(mantida)'}"
        )
