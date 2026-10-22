# CREMEPE / ESEM — Portal de Recuperação do Curso de Ética Médica

Sistema web para o CREMEPE/ESEM (Conselho Regional de Medicina de Pernambuco / Escola Superior de
Ética Médica) que permite gravar, disponibilizar e **acompanhar a evolução** do médico no curso
de Ética Médica.

**Objetivo:** substituir o acompanhamento manual de presença (mínimo de 50% do curso) por um
portal que garante — por construção — que o médico:

- **não consiga avançar** o conteúdo que ainda não assistiu;
- **retome de onde parou**, mesmo que saia do sistema ou feche o navegador;
- tenha seu progresso **registrado e auditável** pelo CREMEPE.

---

## Como funciona (resumo)

| Necessidade | Como o sistema resolve |
|---|---|
| Gravação de 6h com arquivos > 5 GB | Upload **em blocos de 16 MB**, retomável, gravado direto no arquivo final; depois o `ffmpeg` gera **HLS** para streaming |
| Não avançar o curso | 3 camadas: limite no player, validação do heartbeat no servidor e **recusa do segmento HLS** além do ponto liberado |
| Retomar de onde parou | Heartbeat a cada 10s + `sendBeacon` ao sair; o progresso é gravado no Postgres |
| Operação sem programador | **Painel administrativo completo** em `/painel/` (cursos, gravações, médicos, matrículas, auditoria) — o Django admin fica apenas como recurso avançado |
| Regra dos 50% | `min_percent` configurável **por curso** (50% para reprovação, 100% para reciclagem) |

---

## Arquitetura

```
Navegador (médico/admin)
        │  HTTPS
        ▼
   nginx  ── /static ──────────► volume staticfiles
        ├─ /api/uploads/ ───────► Django (sem buffer: vai direto ao disco)
        ├─ /api/media/* ────────► Django (valida token + limite de avanço)
        │                             └─ X-Accel-Redirect ──► /protected-media/*
        └─ demais rotas ─────────► Django (gunicorn)

   Django (app)          ──►  PostgreSQL
   worker (ffmpeg)       ──►  media/orig, media/hls
```

Serviços (Docker Compose): `nginx`, `app` (gunicorn), `worker` (ffmpeg), `db` (PostgreSQL).

---

## Requisitos

- Linux (Ubuntu 22.04/24.04 recomendado) com **Docker** e **Docker Compose v2**
- 4 vCPU, 8 GB RAM (16 GB se processar vários cursos em paralelo), SSD de 1 TB
- Domínio ou subdomínio do CREMEPE (recomendado para TLS via Let's Encrypt)

---

## Instalação (servidor do CREMEPE)

```bash
# 1. Código
git clone <repositorio> /opt/cremepe-esem && cd /opt/cremepe-esem

# 2. Configuração
cp .env.example .env
# Edite .env: DJANGO_SECRET_KEY, POSTGRES_PASSWORD, DJANGO_ALLOWED_HOSTS,
#              DJANGO_CSRF_TRUSTED_ORIGINS (https://cursos.cremepe.org.br)

# 3. Subir
docker compose up -d --build

# 4. Administrador do portal
docker compose exec app python manage.py criar_admin admin --email admin@cremepe.org.br
```

> **`DJANGO_ALLOWED_HOSTS` é obrigatório**: deve conter o domínio/IP pelo qual o portal será
> acessado (ex.: `cursos.cremepe.org.br,192.168.0.10`). Hostnames não permitidos são recusados
> com **HTTP 400** — inclusive nas chamadas internas entre containers (`nginx`, `app`).
> No ambiente local, use `localhost,127.0.0.1,nginx,app`.

O portal responde em `http://<servidor>:8080` (ou por HTTPS após configurar o certbot).

### HTTPS (recomendado)

```bash
docker compose exec app apt-get update && docker compose exec app apt-get install -y certbot
# ou use o certbot do host apontando para a porta 80/443
```

Depois, no `.env`: `SECURE_COOKIES=true` e `DJANGO_CSRF_TRUSTED_ORIGINS=https://...`.

---

## Curso de referência (17º Curso)

O cenário de demonstração reproduz o **17º Curso de Atualização em Ética e Bioética para
Residência Médica** (Escola Superior de Ética Médica / CREMEPE, com apoio da CEREMPE),
realizado em **25 e 26 de maio de 2026**, em formato de *webnário* com 6 horas:

| Encontro | Duração | Conteúdo |
|---|---|---|
| Webnário — Dia 1 (25 de maio) | 3h53 | Abertura, ética e bioética aplicada, casos clínicos |
| Webnário — Dia 2 (26 de maio) | 2h41 | Bioética e hemotransfusão, oficinas e argumentação |

A capa do curso e as miniaturas dos encontros são **quadros extraídos das próprias gravações**
(`scripts/gerar_assets_curso.sh`), e a identidade visual (cores, emblema da ESEM, marcas
CREMEPE / ESEM / CEREMPE) segue os slides do próprio curso.

---

## Identidade institucional

O portal é o **Portal de Cursos da Escola Superior de Ética Médica (ESEM) do CREMEPE**. A
estrutura é:

| Página | Endereço | Acesso |
|---|---|---|
| Apresentação da ESEM | `/` | público |
| Sobre a ESEM | `/sobre/` | público |
| Acesso aos cursos (login) | `/entrar/` | público (login por CPF) |
| Meus cursos | `/cursos/` | médico matriculado |
| Página do curso | `/cursos/<id>/` | médico matriculado |
| Painel administrativo | `/painel/` | administrador |

Os textos institucionais foram escritos a partir do conteúdo oficial da ESEM
([cremepe.org.br/escola-superior-de-etica-medica](https://www.cremepe.org.br/escola-superior-de-etica-medica)):

- Núcleo técnico permanente responsável por conferências, seminários, fóruns e cursos
  sobre ética e bioética na medicina
- Criada pelas Resoluções CREMEPE 03/2005 e 08/2011
- Pioneira no Nordeste; integra as instituições de ensino médico do Estado, com cooperação
  da Faculdade Pernambucana de Saúde (FPS) e da Universidade Federal de Pernambuco (UFPE)
- Temas em debate: relação médico-paciente, autonomia, gestão do corpo/vida/morte,
  intimidade e privacidade, responsabilidade profissional

**As referências ao 17º Curso ficam restritas à página do curso e ao cadastro dele** — a
apresentação, a página "Sobre a ESEM", o login e o menu falam da ESEM.

Verificação automática:

```bash
python scripts/conferir_identidade.py   # conteúdo da ESEM, acentos e escopo do 17º curso
```

> Os scripts de diagnóstico leem credenciais de `MEDICO_CPF`, `MEDICO_SENHA`,
> `MEDICO_SENHA_INICIAL`, `MEDICO_SENHA_NOVA` e `ADMIN_SENHA`. Nenhuma senha fica
> no repositório.

---

## Sessão e cookies

O portal usa a sessão do Django (backend de banco). Três cuidados evitam a clássica
dor de cabeça: **"sai sozinho ao recarregar"**:

| Cuidado | Por quê |
|---|---|
| Logout só por **POST** (`Sair` é um botão) | Um `GET /sair/` era acionado por clique acidental ou prefetch do navegador e derrubava a sessão sem o usuário perceber |
| `no-store` nas páginas e no login | Sem isso o navegador pode reaproveitar um 302 antigo de login (sem o `Set-Cookie`) e o acesso "não pega" |
| **Cookie órfão é apagado** pelo servidor | Se o navegador ainda enviar um `sessionid` cuja sessão não existe mais (ex.: logout em outra aba), a resposta 401/302 devolve o cookie zerado — o próximo login funciona normalmente |
| **Nomes de cookie próprios** (`cremepe_sid`, `cremepe_csrftoken`) | Extensões de navegador, proxies locais e antivírus com inspeção de HTTPS reescrevem os cookies padrão do Django (`sessionid`, `csrftoken`) com valores antigos — o login "não pegava" e a API respondia 401. Com nomes próprios, esses componentes não interferem |
| **Token de API assinado** (`Authorization: Bearer ...`) | Blindagem final: a página do curso entrega um token assinado (HMAC) com a identidade e a chave da sessão; o player o envia em todas as chamadas. Se o cabeçalho `Cookie` for reescrito por completo, **o vídeo continua reproduzindo** |
| `SessaoUnicaMiddleware` | Se houver cookies `sessionid` duplicados no cabeçalho, o servidor escolhe o que ainda existe no banco |

Diagnóstico: o log do nginx registra o `Set-Cookie` de cada resposta
(`set_cookie="..."`) e o app registra as chamadas de API sem sessão válida
(`API sem sessao valida: ... cookie_bruto=...`).

Se o navegador estiver com cookies antigos do site, uma limpeza definitiva é:
**DevTools → Application → Storage → Clear site data** (ou sair em janela anônima).

---

## Operação diária (tudo pelo painel, sem Django admin)

O **painel administrativo** fica em **`/painel/`** (ícone no topo, à direita).

### 1. Criar o curso

**Painel → Cursos → Novo curso**: título, turma/edição, imagem de capa, percentual mínimo de
aprovação (ex.: `50`), se os vídeos são liberados sequencialmente e o prazo.

### 2. Cadastrar as gravações

**Painel → Cursos → Abrir → Vídeo → Cadastrar novo vídeo** (ordem e título).
Depois, **Painel → Enviar gravação**: escolha o vídeo, selecione o arquivo e clique em
**Enviar arquivo**.

- O envio é feito em blocos de 16 MB, com **SHA-256** por bloco e **retomada**: se a conexão
  cair, basta tentar de novo — o servidor reaproveita a sessão e continua de onde parou.
- Ao concluir, o `worker` processa o arquivo e o vídeo passa a **Pronto**.

> Tempo de processamento: com *remux* (mantém a qualidade original) leva minutos; com
> transcodificação completa (720p/540p/360p) pode levar de 1 a 3 horas para um curso de 6h.
> O modo é configurável em `VIDEO_PIPELINE` (`auto`, `remux` ou `transcode`).

### 3. Cadastrar o médico reprovado

**Painel → Médicos → Novo médico**: nome, CPF, CRM e (opcionalmente) uma senha própria.

A **senha inicial padrão** do CREMEPE é gerada automaticamente:

```
PrimeiroNome (sem acento, 1ª letra maiúscula) + "@" + CPF sem traços e pontos
Ex.: José da Silva / 123.456.789-00  ->  Jose@12345678900
```

O painel mostra essa senha **uma única vez**, na tela de cadastro, para ser entregue ao
médico. No **primeiro acesso** o sistema exige a troca da senha (middleware
`MustChangePasswordMiddleware` bloqueia qualquer outra tela até a troca).

Se o médico esquecer a senha, use **Painel → Médicos → Resetar senha**: ele volta à senha
padrão e continua obrigado a trocá-la no próximo acesso.

### 4. Matricular

**Painel → Cursos → Abrir → “+ matricular médico”** ou **Painel → Matrículas → Nova matrícula**.
O `min_percent` é copiado do curso e pode ser ajustado por matrícula.

### 5. Acompanhar

- **Visão geral**: indicadores, distribuição do progresso, curso por curso, quem está mais
  adiantado e atividade recente.
- **Turma** (dentro do curso): % de cada médico, tempo em aula, sessões, última atividade e
  ações de **aprovar** / **resetar**.
- **Exportar CSV** da turma.
- **Auditoria**: registro de todas as ações administrativas e aprovações.

### 6. Regras de aprovação

- O médico é **aprovado automaticamente** quando a cobertura do curso atinge `min_percent`.
- O painel permite **aprovar/reprovar/resetar/cancelar** a matrícula manualmente.
- A cobertura é a **união das faixas assistidas** — reassistir não soma em dobro.

---

## Regras de bloqueio de avanço (o coração do sistema)

1. **Cliente** — no evento `seeking`, se a posição ultrapassar o limite, o player volta ao
   ponto liberado e avisa o médico.
2. **Heartbeat** — o servidor só credita tempo enquanto o player reproduz de verdade. A
   "margem de avanço" cresce no máximo `PROGRESS_MAX_RATE` (1,25×) o tempo real decorrido
   e é limitada a 120 s. Um salto de 10 minutos em 5 segundos é recusado e registrado como
   **evento suspeito**.
3. **Servidor** — cada segmento HLS é entregue somente se o seu instante inicial estiver
   dentro do limite; caso contrário a resposta é **403** (o arquivo não é entregue, mesmo
   com o DevTools aberto ou usando `curl`).

Adicionalmente, a URL do manifesto e de cada segmento é **assinada (HMAC)** e expira em
60 segundos.

---

## Backup

```bash
# ./scripts/backup.sh  (no host; usa pg_dump + rsync)
30 2 * * * /opt/cremepe-esem/scripts/backup.sh
```

Gera `backups/db-<timestamp>.dump` e `backups/media/`, com retenção de 30 dias.
**Teste a restauração trimestralmente.**

---

## Testes

```bash
docker compose exec app python manage.py test apps      # 101 testes automatizados
MEDICO_CPF=... MEDICO_SENHA=... python scripts/smoke_test.py  # teste de ponta a ponta
```

O `smoke_test.py` valida, contra o portal no ar: login por CPF, heartbeat, recusa de
salto de tempo, bloqueio de segmento (403) e liberação de segmento (200).

```bash
# contra um curso/vídeo específico
BASE_URL=http://servidor:8080 MEDICO_CPF=12345678901 MEDICO_SENHA=xxx \
    MATRICULA_ID=2 VIDEO_ID=5 python scripts/smoke_test.py
```

Verificação do CSS (confere se todas as classes dos templates existem no Tailwind compilado):

```bash
python scripts/conferir_css.py
```

Jornada completa do **primeiro acesso** de um médico (login com a senha inicial padrão →
troca obrigatória → acesso ao curso → reprodução):

```bash
export MEDICO_CPF=... MEDICO_SENHA_INICIAL=... MEDICO_SENHA_NOVA=...
python scripts/jornada_primeiro_acesso.py
```

Verificação da **entrega do stream** (manifesto, tags HLS, bytes do segmento, content-type):

```bash
export MEDICO_CPF=... MEDICO_SENHA=... MEDICO_SENHA_NOVA=...
python scripts/verificar_stream.py
```

> Ambos os scripts realizam a troca de senha no primeiro acesso. Para restaurar a senha inicial
> do médico depois dos testes:
> ```bash
> docker compose exec app python manage.py cenario_inicial \
>     --cpf 12345678900 --nome "José da Silva" --resetar-senha
> ```

Montar o cenário do 17º Curso (curso, dois encontros, médico e matrícula):

```bash
docker compose exec app python manage.py cenario_inicial \
    --cpf 12345678900 --nome "José da Silva" \
    --curso-id 1 --resetar-senha --zerar-progresso
```

Regerar a capa e as miniaturas a partir das gravações (após um novo upload):

```bash
docker compose exec app sh scripts/gerar_assets_curso.sh
```

## Front-end

- **Tailwind CSS v4** compilado com o CLI standalone e versionado em `static/css/tailwind.css`
  (o servidor **não** precisa de Node). Ver `docs/design-system.md`.
- Ícones SVG inline (Heroicons) via context processor — sem CDN, sem dependência externa.
- Para alterar o visual: baixe o CLI standalone (uma vez) e recompile:
  ```bash
  curl -L -o tools/tailwindcss.exe \
    https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/tailwindcss-windows-x64.exe
  tools/tailwindcss.exe -i frontend/tailwind.css -o static/css/tailwind.css --minify
  ```

### Cache dos arquivos estáticos

O `nginx` serve `/static/` com cache de 30 dias. Para que isso seja seguro, o Django usa
**`ManifestStaticFilesStorage`**: cada arquivo ganha um hash no nome
(`player.43bc9564fa1a.js`), gerado no `collectstatic`. Ao alterar o CSS ou o JS, o hash muda
e o navegador baixa a versão nova — sem precisar de recarregamento manual.

> Se você **alterar o CSS/JS e o navegador continuar mostrando a versão antiga**, force a
> atualização com **Ctrl+F5**. O hash é recalculado no próximo `collectstatic`
> (que roda sozinho a cada start do container `app`).

---

## Variáveis de ambiente importantes

| Variável | Padrão | Descrição |
|---|---|---|
| `PROGRESS_MAX_RATE` | `1.25` | Velocidade máxima que gera crédito de progresso |
| `SEGMENT_TOLERANCE_SECONDS` | `12` | Tolerância (s) além do ponto liberado para liberar o segmento (cobre o buffer do hls.js) |
| `HEARTBEAT_INTERVAL_SECONDS` | `10` | Intervalo entre batidas do player |
| `UPLOAD_CHUNK_SIZE` | `16777216` | Tamanho do bloco de upload (16 MB) |
| `HLS_TOKEN_TTL_SECONDS` | `28800` | Validade das URLs assinadas de playlist/segmento (precisa cobrir a duração do vídeo) |
| `API_TOKEN_TTL_SECONDS` | `28800` | Validade do token de API usado pelo player |
| `HLS_SEGMENT_SECONDS` | `4` | Duração de cada segmento HLS |
| `MEDIA_ROOT` | `/srv/media` | Raiz da mídia (originais + HLS) |
| `EXIGIR_TROCA_SENHA` | `true` | Exige a troca da senha inicial no 1º acesso. **Em desenvolvimento, pode ser `false`** para manter a senha no banco e não trocar a cada teste. **Em produção, mantenha `true`.** |

---

## Estrutura do projeto

```
config/            settings, urls, wsgi
apps/accounts/     médicos, login por CPF, troca de senha
apps/courses/      cursos, vídeos, matrículas, progresso
apps/media/        upload em blocos, worker ffmpeg, entrega do HLS
apps/panel/        painel administrativo (operação completa do sistema)
apps/web/          ícones, filtros de template e identidade visual
templates/         telas (Tailwind, renderização no servidor)
templates/panel/   telas do painel administrativo
static/            player.js, upload.js, app.js, vendorizados (sem CDN)
frontend/          fonte do Tailwind (compilado para static/css/tailwind.css)
tools/             Tailwind CLI standalone (uso local)
scripts/           backup, smoke test, upload via linha de comando
nginx/             proxy reverso
docs/              sistema de design e decisões técnicas
```

---

## Observações de segurança e LGPD

- Senhas com PBKDF2; sessão `HttpOnly` + `SameSite`; CSRF ativo.
- Login com mensagem genérica ("CPF ou senha inválidos") + rate limit no nginx.
- O médico só acessa as próprias matrículas e mídia (testes automatizados cobrem isso).
- Dados pessoais (nome, CPF, CRM) e logs de acesso exigem base legal, aviso de privacidade
  e política de retenção — ver `docs/decisoes-tecnicas.md`.
