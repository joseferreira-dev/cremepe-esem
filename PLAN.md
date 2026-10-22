# CREMEPE / ESEM — Portal de Recuperação do Curso de Ética Médica

> **Status (21/10/2026):** F0 (infra) + F1 (domínio, admin, upload em blocos, worker ffmpeg)
> **implementados e validados de ponta a ponta**. F2 (player + bloqueio de avanço) implementado
> com 30 testes automatizados + teste de fumaça. F3 (painéis/relatórios) entregue em versão
> inicial. F4 (hardening, LGPD, TLS) pendente — ver seção 17.

**Sistema web para hosted internally no servidor do CREMEPE (Linux), que permita:**

1. Cadastro de cursos e **upload das gravações** do curso de Ética Médica (6h, até 3 vídeos, >5 GB por vídeo).
2. Cadastro de médicos reprovados e **matrícula** em um curso.
3. Acompanhamento **em tempo real** da evolução (progresso) do médico no curso.
4. **Impossibilidade de avançar** (pular) o conteúdo que ainda não foi visto.
5. **Persistência do progresso** — o médico pode pausar, sair do sistema e voltar de onde parou.
6. Painel do **administrador** com cadastro e acompanhamento da evolução de todos.

---

## 1. Mapeamento requisito → solução

| # | Requisito | Solução técnica |
|---|-----------|-----------------|
| R1 | Upload de vídeos > 5 GB | Upload **chunked com retomada** (blocos de 8–16 MB) direto para disco via nginx; nada passa em memória. Aceita `.mp4/.mkv/.webm`. |
| R2 | Múltiplos vídeos por curso | Tabela `Video` com FK para `Course` + campo `ordem`. Player encadeia a playlist na ordem. |
| R3 | Não avançar o curso | **3 camadas**: (a) clamp no `<video>` do lado do cliente; (b) recusa de heartbeats que avançam além do tempo real; (c) **o servidor recusa a entrega de segmentos HLS** acima da posição liberada. |
| R4 | Salvar evolução / retomar | `Progress` + `ProgressInterval` gravados a cada heartbeat (10 s) e no `sendBeacon` ao sair. Ao voltar, o vídeo retoma na última posição e com o teto de avanço recalculado. |
| R5 | Admin cadastra e acompanha | Admin do Django + painel próprio com progresso por médico, filtros, CSV e auditoria de sessões. |
| R6 | Regra dos 50% | `Course.min_percent` (padrão 50%) + unlocking sequencial de vídeos + estado da matrícula (Matriculado / Em andamento / Aprovado / Reprovado). |
| R7 | Hospedar no servidor local Linux | Docker Compose (nginx + app + worker + postgres) num único host, storage em disco local, backup por `pg_dump` + `rsync`. |

---

## 2. Stack recomendada

### 2.1 Escolha principal: **Python + Django + HTMX (renderização no servidor)**

| Camada | Tecnologia | Justificativa |
|---|---|---|
| Backend | **Python 3.12 + Django 6.x** | Admin CRUD "de graça" (curso, vídeo, médico, matrícula) — é exatamente o que o Requirement R5 pede; ORM + migrations; auth/groups/permissões prontos; deploy simples com gunicorn. |
| Frontend | **Django Templates + HTMX + Bootstrap 5** (assets locais) | Sem build, sem Node, sem SPA. Ágil o suficiente para o player (que é JS puro: hls.js). Servido pela própria app, sem CDN (servidor pode não ter internet). |
| Banco | **PostgreSQL 16** (SQLite no piloto) | Escritas concorrentes de heartbeat + logs de auditoria; `JSONB` para intervalos; backup com `pg_dump`. Trocar SQLite↔Postgres é 1 linha no Django. |
| Vídeo | **HLS (hls.js)** gerado por **ffmpeg** | HLS dá granularidade de tempo por segmento → é o que permite **bloquear avanço no servidor** de forma real (um MP4 progressivo só daria ranges de bytes). |
| Upload | **Chunked/resumable** (HTML5 + JS próprio, ~150 linhas) | Sem dependência externa; retoma após queda de conexão; cada bloco vai direto ao disco. |
| Servidor web | **nginx** | TLS interno, `client_max_body_size 0`, `proxy_request_buffering off` no upload, entrega dos segmentos de mídia via `X-Accel-Redirect` (os bytes não passam pelo Python). |
| Processamento | **Worker** (comando Django + tabela `jobs` no Postgres) | Transcodificação e geração de thumbnail/PDF em background, sem Celery/Redis (complexidade desnecessária aqui). |
| Auth | Django auth + sessão + **CPF + senha** (ou e-mail) | Login simples para o médico; `is_staff` para o admin. |

### 2.2 Por que **não** outras opções

- **Node/React/Next**: exige build, Node no servidor, mais uma linguagem a manter. Benefício zero para ~50–200 usuários e uma tela de player.
- **WordPress/Drupal**: não dá controle fino sobre heartbeat/segmentos nem sobre o bloqueio de avanço sem plugin custom.
- **Moodle**: pesado, GPL, difícil de "cuscar" o relatório de 50% no formato que o CREMEPE precisa.
- **Google Forms/Sheets + YouTube**: não garante "não avançar" nem rastreabilidade local (LGPD/dados em nuvem).
- **Somente MP4 progressivo com Range**: simples, mas o bloqueio de avanço passa a ser dependente de índice de keyframes e é contornável; HLS dá controle por tempo, que é o requisito real.

### 2.3 Variante "mínima" (se quiser o máximo de simplificação)

Uma única imagem Docker com Gunicorn + SQLite + Caddy (TLS automático) e mídia servida pela própria app (`sendfile`). Roda em `docker compose up`. Funciona com 1–2 médicos simultâneos; para a escala real, use a stack acima.

---

## 3. Decisões de arquitetura (os 5 pontos críticos)

### 3.1 Arquivos grandes — upload e entrega

**Upload (admin)**
1. O admin cria o curso, cadastra o vídeo e inicia o upload.
2. O front faz `POST /api/uploads/{id}/chunk` com `Content-Range: bytes X-Y/Z`; cada bloco é gravado em `media/tmp/{id}.part` e um manifesto `.json` guarda os blocos concluídos.
3. Queda de internet → retoma de onde parou (consulta o manifesto).
4. Concluído → arquivo final em `media/orig/{video_id}.mp4` e **job de processamento** é enfileirado.

**Processamento (ffmpeg)**
- Tentativa 1 (rápida): **remux** `-c copy` para HLS (`-hls_time 4 -hls_playlist_type vod`) → quase instantâneo e mantém a qualidade original.
- Se falhar (codec exótico, sem keyframes): **transcode** para H.264 720p/540p/360p + AAC 96k (`-vf scale`, CRF 23, `-preset veryfast`) com hardware acceleration se o servidor tiver (QSV/VAAPI/NVENC).
- Saída: `media/hls/{video_id}/720p/{seg}.ts` + `master.m3u8` + `poster.jpg`.
- Original é arquivado (pasta `orig/`) e pode ser removido após 30 dias (economiza ~15 GB por curso).

**Entrega (streaming)**
- O player **não** recebe a URL real do arquivo: recebe um **manifest com URLs assinadas** (`?t=<HMAC>&exp=<unix>`), válidas por 60 s.
- O nginx entrega os segmentos com `X-Accel-Redirect` (sem passar pelo Python) e `Cache-Control: public, max-age=31536000` (segmentos são imutáveis).

> **Resultado:** ~2 Mbps por aluno em 720p → 50 alunos simultâneos ≈ 100 Mbps. Requisitar NIC gigabit no servidor.

### 3.2 "Não deve ser possível avançar" — 3 camadas

**Camada A — Cliente (UX)**
No evento `seeking` do `<video>`: se `currentTime > tetoLiberado + 2 s`, o player faz `currentTime = tetoLiberado` e exibe "Não é possível avançar neste curso". Também desabilita o arrastar da barra além do ponto liberado (a barra mostra a zona bloqueada em cinza).

**Camada B — Heartbeat (integridade do dado)**
A cada 10 s (e em `pause`, `ended`, `visibilitychange`, `beforeunload` via `navigator.sendBeacon`) o player envia `{video_id, position, playing}`.

O servidor valida antes de creditar:

```
wall      = agora - último_heartbeat          # segundos reais
avanco    = position - última_posição
credito   = min(avanco, max(0, wall * TAXA_MAX + GRACA))
```

Com `TAXA_MAX = 1.25` (aceita 1,25× de velocidade, como usam em aulas) e `GRACA = 5 s`.
- Se `avanco > credito` → o excedente é **descartado** (registra `suspeito++` na auditoria) e o servidor responde com a posição máxima autorizada → o cliente se corrige.
- A "posição máxima" (high-water mark) **só cresce**; nunca diminui. Voltar e reassistir é permitido e não penaliza.

**Camada C — Servidor (trava real do arquivo)**
Como HLS é indexado por tempo, cada segmento tem um instante inicial conhecido (`seg_start`). O endpoint que serve o segmento:

```
GET /media/{video_id}/{qualidade}/seg{N}.ts?token=...
  1. valida HMAC + expiração + matrícula ativa do médico no curso
  2. libera SE  seg_start <= furthest_video + TOLERANCIA (8 s, cobre segmento em voo)
  3. senão → 403  (o vídeo simplesmente não carrega além do ponto)
```

Assim, mesmo com DevTools aberto ou script próprio, o conteúdo além do ponto liberado **não é servido**. É a camada que dá garantia real ao requisito.

> **Observação honesta:** um usuário determinado ainda pode falsificar heartbeats (enviar posição avançada com carimbo de tempo manipulado). As camadas B e C reduzem isso a um esforçotrivial (escrever um script de heartbeat). Se for necessário, o passo seguinte é **assinatura do heartbeat** com o relógio do servidor + verificação de consistência de sessions. Vou implementar B e C no MVP e deixar o "modo estrito" configurável.

### 3.3 Cálculo do progresso (o "50%")

Três indicadores, todos derivados de `ProgressInterval` (faixas `[início, fim]` já vistas, mescladas):

| Indicador | Cálculo | Uso |
|---|---|---|
| **Cobertura** | `Σ(intervalos mesclados) / duração_total_curso` | Critério de aprovação (≥ `min_percent`, padrão 50%). |
| **Posição atual** | última posição assistida | "Continuar de onde parei". |
| **Tempo em aula** | soma das durações dos intervalos | Evidência/auditoria ("ficou 4h32min em aula"). |

Se o admin quiser o critério mais estrito (common no CREMEPE: "assistir o vídeo inteiro"), basta `min_percent = 100` ou marcar o vídeo como obrigatório.

**Unlocking sequencial:** por padrão, o vídeo N+1 só é liberado quando o N atinge ≥ 95% de cobertura (configurável por curso). Isso garante "não avançar o curso" também no nível de vídeo, não só dentro do vídeo.

### 3.4 Persistência / retomada

- Tabela `Progress` (1 por matrícula×vídeo): `last_position`, `furthest_seconds`, `covered_seconds`, `total_watch_seconds`, `last_seen_at`, `completed_at`.
- Tabela `ProgressInterval` (`progress_id`, `start`, `end`) — mesclada a cada heartbeat; fonte da cobertura.
- Gravação: heartbeat a cada 10 s → Postgres. Sem cache: o banco suporta folgado.
- Ao reabrir o curso: o player recebe `last_position` e `furthest` e retoma no ponto exato.
- `sendBeacon` no `beforeunload` garante o último ponto mesmo fechando a aba.

### 3.5 Auditoria e evidências

`WatchSession` (início/fim, IP, user-agent, dispositivo) + `AuditLog` (ações do admin: matrícula criada, aprovação manual, reset de progresso, exclusão de vídeo). Base para fiscalizar o recreate do CREMEPE e para LGPD (o médico precisa saber que há log de acesso).

---

## 4. Arquitetura de componentes

```
                    ┌───────────────────────────────┐
   Navegador ──────►│  nginx  (TLS interno :8443)   │
   (médico/admin)   │  ├─ /admin, /curso  → gunicorn│
                    │  ├─ /media/*       → X-Accel  │
                    │  └─ /upload        → gunicorn │
                    └───────────┬───────────────────┘
                                │
                     ┌──────────▼───────────┐      ┌──────────────┐
                     │  Django (gunicorn)   │─────►│  PostgreSQL  │
                     │  app + hls.js player │      │  (volume)    │
                     └──────────┬───────────┘      └──────────────┘
                                │ enfileira job
                     ┌──────────▼───────────┐
                     │  worker (ffmpeg)     │──► media/orig, media/hls
                     └──────────────────────┘

        Volumes:  media/ (origen + HLS)   db/ (PGDATA)   static/
```

---

## 5. Modelo de dados

```
User (django auth)
 └─ 1:1 Medico (crm, cpf, nome, email, telefone, ativo, turma_ultimo_curso)

Course
  id, titulo, descricao, edition_label ("1ª turma 2026"), status(rascunho/ativo/arquivado),
  min_percent(50), sequencial(True), prazo_matricula, created_by, created_at

Video
  id, course(FK), ordem, titulo, duracao_s, arquivo_original, hls_path,
  status(uploading/queued/processing/ready/error), poster, erro_msg,
  obrigatorio(True), created_at

Enrollment (matrícula)
  id, medico(FK), course(FK), status(matriculado/em_andamento/aprovado/reprovado/desativado),
  min_percent_aplicado, coverage_percent, aprovado_em, aprovado_por,
  created_at, deadline

Progress (1 por matrícula×vídeo)
  id, enrollment(FK), video(FK), last_position, furthest_seconds,
  covered_seconds, total_watch_seconds, last_seen_at, completed_at, unique(enrollment,video)

ProgressInterval
  id, progress(FK), start_s, end_s   (índice: (progress_id, start_s))

WatchSession
  id, enrollment(FK), video(FK), started_at, ended_at, ip, user_agent, device, watchdog

AuditLog
  id, user(FK|null), acao, entidade, entidade_id, detalhes(jsonb), ip, created_at

Job
  id, kind(transcode/poster/thumbnail), video(FK), status, progress, error, created_at, finished_at
```

Índices: `Enrollment(course, status)`, `Enrollment(medico)`, `Progress(enrollment)`, `ProgressInterval(progress_id, start_s)`, `AuditLog(created_at)`.

---

## 6. Rotas principais

**Público / médico**
| Rota | Descrição |
|---|---|
| `GET /` | Login (CPF/senha) ou lista de cursos matriculados |
| `GET /curso/{id}` | Player do curso (lista de vídeos, progresso, botão continuar) |
| `GET /api/player/{enrollment}/{video}` | JSON: duração, last_position, furthest, segmentos liberados, token |
| `POST /api/progress/heartbeat` | Heartbeat (valida e credita) |
| `GET /media/{video}/{q}/seg{N}.ts?token=` | Segmento (403 se além do limite) |

**Admin**
| Rota | Descrição |
|---|---|
| `/admin/` | Admin do Django (CRUD de Course, Video, Medico, Enrollment, auditoria) |
| `GET /admin/painel/` | Dashboard: % médio do curso, curva de evasão, médicos em risco de prazo |
| `GET /admin/curso/{id}/medicos/` | Uma linha por médico: cobertura, última atividade, sessões,aprovar/resetar |
| `GET /admin/curso/{id}/relatorio.csv` | Export CSV (opcional: XLSX) |
| `POST /admin/upload/{video}` | Upload chunked |
| `POST /admin/enrollment/{id}/aprovar` | Aprovação manual (com justificativa na auditoria) |

---

## 7. Pipelines

**Upload → disponível para o aluno**
```
1. Admin cria curso e vídeo (arquivo vazio)
2. "Enviar arquivo" → upload por blocos (8–16 MB) com retomada → media/orig/{id}.mp4
3. Job 'transcode' enfileirado → worker roda ffmpeg
4. ffprobe extrai duração → Progress/Unlock calculados → status 'ready'
5. Vídeo aparece na playlist do curso
6. Aluno recebe alerta (e-mail opcional) e assiste
```

**Assistindo**
```
1. Aluno abre /curso/{id} → servidor devolve vídeos liberados + progresso
2. Player carrega manifest assinado → hls.js
3. Heartbeat a cada 10 s → servidor credita intervalos e devolve "furthest"
4. Cliente aplica o teto; servidor bloqueia segmentos além do teto
5. Ao sair: sendBeacon → última posição gravada
6. Ao voltar: retoma de onde parou
7. Ao atingir min_percent: status 'aprovado' + data; opcional: e-mail + certificado PDF
```

---

## 8. Painéis

**Do médico**: cards com % de cobertura do curso, badge "Aprovado / Em andamento", botão "Continuar de onde parei", lista de vídeos com marca de concluído.

**Do admin** (além do admin do Django):
- Visão geral: quantos médicos por curso, % médio, reprovados pendentes, prazos.
- Tela "turma": tabela com médico, cobertura, última atividade, nº de sessões, ações (aprovar, resetar progresso, desativar).
- Relatório de evasão: em que minuto as pessoas abandonam (útil para você melhorar o curso).
- Export CSV.

---

## 9. Segurança, LGPD e auditoria

- Senhas com hash (Django PBKDF2), sessão com cookie `HttpOnly`/`SameSite=Lax`, `Secure` + `CSRF` ativo.
- **Login por CPF**: CPF armazenado com hash/busca normalizada (só dígitos); login valida CPF+senha; mensagem de erro genérica ("CPF ou senha inválidos") para não revelar quais CPFs existem. Rate limit (django-axes) + `LIMIT_REQ` no nginx.
- **TLS**: com internet e domínio do CREMEPE → **Let's Encrypt** (certbot) automático; sem domínio → certificado de CA interna (distribuição manual aos médicos, mais frágil) ou Cloudflare Tunnel. Nunca expor o app em HTTP puro para a internet.
- **Força de senha do primeiro acesso**: senha temporária gerada pelo admin + troca obrigatória no 1º login (`must_change_password`).

- **Dado pessoal sensível**: nome + CPF + CRM + log de acesso → LGPD. Medidas: acesso só ao próprio médico (exceto admin autorizado), auditoria de leitura, retenção (excluir/anonimizar após X anos), consentimento/aviso de privacidade na tela de login.
- Autorização: `is_staff` = admin; médico só enxerga as próprias matrículas. Teste automatizado garante que A não acessa B.
- **Upload**: validar `Content-Type`/extensão, limitar tamanho (config), nome de arquivo gerado pelo sistema (nada de path traversal), antivírus opcional (ClamAV em container, fase 4).
- **Força bruta**: throttling no login (django-axes) + `LIMIT_REQ` no nginx.

---

## 10. Infraestrutura no servidor Linux (CREMEPE)

**Recomendado: Docker Compose** (reprodutível, atualizações com `docker compose pull && up -d`).

Servidor de referência: Ubuntu 22.04/24.04 LTS — 8 vCPU, 16 GB RAM, SSD 1 TB (ver seção 11), rede gigabit.
*(Docker já instalado no servidor do CREMEPE → a F0 começa direto pelo `compose`.)*

Serviços do `docker-compose.yml`:
```
nginx      → proxy + TLS + mídia (X-Accel) + limites de upload
app        → gunicorn (4 workers) + Django
worker     → python manage.py runworker  (ffmpeg)
db         → postgres:16 (volume)
```

Volumes: `./media` (orig + hls), `./db` (PGDATA), `./static`, `./backups`.

**nginx — pontos críticos**
```nginx
client_max_body_size 0;              # upload sem limite de corpo (chunks)
proxy_request_buffering off;          # upload vai direto ao disco
proxy_read_timeout 3600s;             # upload longo
location /media/ { internal; add_header X-Accel-Expires 3600; }   # uso interno via X-Accel-Redirect
location /hls/   { expires 30d; add_header Cache-Control "public, immutable"; }
```

**Backup** (diário, cron/systemd timer): `pg_dump -Fc` + `rsync` dos HLS + retenção 30 dias + cópia externa (HD/USB). **Testar a restauração 1× por trimestre.**

**Sem internet?** (servidor local costuma ter) → então: baixar as imagens Docker e as wheels Python numa máquina com internet e levar por pen drive; assets front (Bootstrap, HTMX, hls.js, Chart.js) **vendorizados no repositório** (sem CDN).

---

## 11. Dimensionamento e capacidade

**Espaço em disco (estimativa por curso de 6h, 3 vídeos):**

| Item | Tamanho |
|---|---|
| Originais (5 GB × 3) | ~15 GB |
| HLS transcodificado 720p (2 Mbps) | ~5,5 GB |
| HLS 540p + 360p (variantes ABR) | ~3 GB |
| Picos temporários (ffmpeg) | ~5 GB |
| **Total por curso (sem arquivar original)** | **~9 GB** |
| 10 cursos/ano | ~90 GB/ano → **SSD de 1 TB** dá folga confortável |

**Memória/CPU:** app ~300 MB, worker ffmpeg até 2 GB, Postgres ~1 GB → 16 GB de RAM comfortable.
**Transcodificação:** remux ≈ tempo real × 10 (minutos); transcode completo ≈ 1–2 h por curso de 6 h em CPU. → **fazer o processamento de madrugada**; o admin ve status "processando 43%".

**Rede:** 720p ≈ 2 Mbps/aluno; 50 simultâneos ≈ 100 Mbps (ok em gigabit). Variantes ABR (360p) caem para ~500 kbps/aluno em conexões fracas — relevante para os médicos assistindo pelo celular.

---

## 12. Roadmap de implementação (1 desenvolvedor)

| Fase | Entregáveis | Esforço |
|---|---|---|
| **F0 — Infra** | Servidor, Docker Compose (nginx/app/worker/db), TLS interno, volumes, backup automático, repositório Git, CI de lint/test | 3–4 dias |
| **F1 — Domínio + Admin** | Models + migrations, admin do Django (curso, vídeo, médico, matrícula), **upload chunked com retomada**, fila de jobs, worker ffmpeg (remux→transcode), duração/thumbnail | 6–8 dias |
| **F2 — Player + Anti-avanço** | Player HLS (hls.js), camada A (clamp), camada B (heartbeat validado), camada C (bloqueio de segmento no servidor), retomada de posição, unlocking sequencial | 8–10 dias |
| **F3 — Painéis e Relatórios** | Dashboard admin, tela "turma por curso", filtros, CSV, auditoria, estados da matrícula e aprovação (≥ min%), e-mail de aprovação | 5–7 dias |
| **F4 — Hardening e entrega** | Testes automatizados (progresso, autorização, upload), testes de carga (50 usuários), LGPD (aviso, retenção, anonimização), manual do usuário, treinamento do admin, plano de rollback | 4–6 dias |
| | **Total** | **~26–35 dias** (2,5–3,5 meses) |

**MVP prioritário (se houver prazo curto): F0 + F1 + F2 + o mínimo de F3** → sistema funcional em ~3 semanas.

---

## 13. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Upload de 5 GB cai e corrompe | Blocos + retomada + checksum por bloco + merge atômico (grava em `.part`, renomeia no fim) |
| Servidor sem internet | Imagens Docker e wheels pré-carregadas; assets vendorizados; sem CDN |
| Usuário burlar o "não avançar" | 3 camadas; heartbeat validado por tempo real; token de segmento com expiração curta; log de `suspeito` para auditoria |
| Gravação do curso é ruim (áudio ruim, sem legenda) | Thumbnail + checagem de áudio; opcional: extração/legenda automática (Whisper) para acessibilidade — fase opcional |
|_CPU do servidor insuficiente | Remux como padrão; transcode em madrugada; verificar `ffmpeg -hwaccels` e ativar QSS/VAAPI se houver GPU/iGPU |
| Crescimento de disco | Política de arquivar/limpar originais; alerta de uso de disco no painel |
| Migração de dados do Excel (médicos já cadastrados) | Importador CSV/TXT (F1) — planejar o formato do CSV atual do CREMEPE |

---

## 14. Testes e critérios de aceite

**Funcionais**
1. Admin cria curso com 3 vídeos → upload → processamento → "ready" em ≤ 2 h (remux: minutos).
2. Médico matriculado abre o curso e o player inicia no último ponto após sair e voltar.
3. Arrastar a barra além do ponto assistido → player volta ao limite e mostra aviso.
4. Chamada direta (curl) a um segmento além do limite → **403**.
5. Heartbeat com posição 10 min à frente → servidor não credita; resposta traz a posição autorizada.
6. Assistir 100% → status vira "Aprovado" automaticamente (≥ min_percent) e aparece no relatório.
7. Médico A não acessa dados do médico B (teste automatizado).
8. Upload interrompido no meio → retoma do bloco exato.

**Não funcionais**
- 50 usuários simultâneos sem degradação; p95 de resposta da API < 400 ms.
- Queda de energia no meio do upload → integridade preservada.
- Backup + restauração testados.

---

## 15. Decisões já tomadas (com o solicitante)

| Decisão | Escolha | Impacto na implementação |
|---|---|---|
| Meta de aprovação | **Configurável por curso** (`Course.min_percent`, padrão 50%) | Admin define 50% (reprovação) ou 100% (reciclagem) na edição do curso. |
| Autenticação do médico | **CPF + senha** | Login customizado por CPF (médico já é identificado pelo CRM/CPF no CREMEPE). Senha forte gerada pelo admin, com troca obrigatória no 1º acesso; reset pelo admin. Sem e-mail obrigatório. |
| Onde o médico assiste | **Internet (servidor com saída)** | TLS real; amplia público (casa/celular). Requer domínio do CREMEPE ou certificado interno confiável nos dispositivos dos médicos. |
| Servidor | **Linux com Docker já instalado** | F0 encurta: sem etapa de instalação de Docker. Bastam: `docker compose up`, DNS interno e certificado. |

### 15.1 Sub-decisões que restam (não bloqueiam o início do F0/F1)

1. **Desbloqueio sequencial**: o médico precisa terminar o vídeo 1 (≥95%) para liberar o 2? → padrão: sim, configurável por curso.
2. **Campos do cadastro do médico**: CRM, nome, CPF, e-mail, telefone, situação (ativo/inativo)? → define o tratamento LGPD.
3. **Quantos médicos por turma** e **quantas turmas/ano** → valida o dimensionamento (se <50 usuários simultâneos, SQLite já resolve).
4. **Materiais complementares** (apostila/PDF) junto do vídeo? → download com controle de acesso (barato).
5. **Certificado de conclusão em PDF** ao atingir a meta? (opcional, ~1 dia)
6. **Importação da lista atual de reprovados** (CSV/XLSX) — há formato padronizado? → importador (F1).
7. **Domínio/TLS**: existe domínio do CREMEPE (ex.: `cursos.cremepe.org.br`) para Let's Encrypt, ou será certificado interno distribuído aos médicos?
8. **Prazo desejado** para o MVP e **quem opera** o sistema no dia a dia (admin do CREMEPE ou TI interno) → define o nível de polimento da interface.

---

## 16. Glossário

- **Cobertura**: percentual do conteúdo efetivamente visto (união das faixas assistidas) — é o que conta para os 50%.
- **High-water mark / `furthest`**: maior ponto já alcançado no vídeo; define até onde o médico pode avançar.
- **Heartbeat**: batida periódica (10 s) do player com a posição atual; base da persistência e da validação anti-avanço.
- **Segmento HLS**: pedaço de ~4 s do vídeo, endereçável por URL — a unidade que o servidor bloqueia.
- **Unlocking**: liberação progressiva dos vídeos conforme a cobertura do anterior.

---

## 17. Status da implementação (21/10/2026)

### 17.1 O que já está pronto e validado

| Fase | Entregue | Validação |
|---|---|---|
| **F0 — Infra** | Docker Compose (nginx + app + worker + PostgreSQL), volumes, entrada automática com espera das migrations, backup `pg_dump`+`rsync`, assets vendorizados (sem CDN) | `docker compose up -d --build` sobe tudo do zero |
| **F1 — Domínio + Admin** | Models (Curso, Vídeo, Matrícula, Progresso, Sessão, Auditoria), admin do Django completo, **upload em blocos retomável** (16 MB/bloco, SHA-256 opcional), worker ffmpeg com *remux* → HLS (fallback: transcodificação 720p/540p/360p), poster, duração via ffprobe, comandos `criar_admin` e `seed_demo` | Upload real de arquivo → HLS gerado → 11 segmentos por playlist |
| **F2 — Player + anti-avanço** | Player HLS (hls.js), camada A (clamp do `seeking`), camada B (heartbeat com margem de avanço), camada C (servidor recusa o segmento além do limite), retomada de posição, desbloqueio sequencial, limite de velocidade | 30 testes automatizados + `scripts/smoke_test.py` (salto de 10 min recusado e marcado como suspeito; segmento além do limite → 403) |
| **F3 — Painéis** (v1) | Painel por curso, tela da turma (cobertura, tempo em aula, sessões, última atividade), CSV, aprovação automática por cobertura, ações em lote no admin | Páginas renderizando 200; CSV gerado |

### 17.2 Desvios em relação ao plano original (e por quê)

1. **`ProgressInterval` virou campo JSON (`Progress.intervals`)** em vez de tabela separada.
   Motivo: a lista de faixas é curta e sempre lida/escrita junto do progresso; o JSON elimina
   o join e a necessidade de mesclar linhas. Sem impacto funcional.
2. **Modelo de "margem de avanço" no lugar de "tempo real × 1,25 + tolerância"**.
   O servidor acumula uma *margem* que só cresce enquanto o player reproduz (limitada a 120 s).
   Isso fecha a brecha em que o médico pausava, esperava e dava um salto único: o salto é
   limitado à margem remanescente, e cada evento é registrado como **suspeito**.
3. **Sem HTMX** — as telas são Django Templates + Bootstrap; o único JavaScript além do
   `hls.js` é o player e o uploader. Menos dependências, menos build.
4. **Código montado via bind mount** (`./apps`, `./config`, `./templates`, `./static`) em vez de
   copiado só na imagem: facilita a manutenção no servidor e o hot reload em desenvolvimento.

### 17.3 Faltando (F4) e próximos passos

- [ ] **TLS**: Let's Encrypt no nginx + `SECURE_COOKIES=true` + `DJANGO_CSRF_TRUSTED_ORIGINS`.
- [ ] **LGPD**: aviso de privacidade no login, política de retenção/anonimização dos logs,
      base legal e consentimento documentados pelo CREMEPE.
- [ ] **Importador** da lista de reprovados (CSV/XLSX) — depende do formato atual.
- [ ] **Certificado de conclusão em PDF** (opcional).
- [ ] **Materiais complementares** (apostila) com download controlado (opcional).
- [ ] **Teste de carga** com 50 médicos simultâneos e medição de banda.
- [ ] **Alerta de disco** e política de arquivamento dos arquivos originais.
- [ ] **Monitoramento**: healthcheck do `/healthz` + systemd timer do backup.
- [ ] Decidir as sub-decisões da seção 15.1 (sequência, campos do cadastro, volume, TLS,
      prazo e operador).

---

## 18. Rodada 2 — design, painel administrativo e teste com o curso real

### 18.1 Design (Tailwind + identidade CREMEPE)

- **Tailwind CSS v4** com o **CLI standalone** (`tools/tailwindcss.exe`): o CSS é compilado no
  ambiente de desenvolvimento e versionado em `static/css/tailwind.css`. **O servidor Docker
  não precisa de Node nem de acesso à internet.**
- Paleta extraída do `logotipo-cremepe.png` e do `banner.jpg` do 17º Curso:
  `marca-500 #009965` (verde do logotipo), `turquesa-500 #00a396`, `pacifico-500 #0079b0`,
  `abissal-900 #00455f` (rodapé do banner), `ardesia-500 #595959` (cinza do logotipo).
- Ícones **SVG inline** (Heroicons) via context processor — sem CDN, sem dependência externa.
- Bootstrap foi **removido**; não há mais JavaScript de framework (apenas `hls.js` + `app.js`).
- Verificação automática: `python scripts/conferir_css.py` confere se todas as classes usadas
  nos 21 templates existem no CSS compilado (1.920 classes analisadas).

### 18.2 Painel administrativo (`/painel/`)

Operation completa **sem depender do Django admin** (que segue disponível como recurso avançado):

| Tela | O que faz |
|---|---|
| Visão geral | KPIs (cursos, médicos, aprovados, sessões do dia), distribuição do progresso, progresso por curso, quem está mais adiantado, vídeos processando/com erro, atividade recente |
| Cursos | Listagem com busca, criação, edição (capa, `%` de aprovação, desbloqueio sequencial, prazo), detalhe com **turma** e **gravações**, exclusão protegida, **exportar CSV** |
| Gravações | Cadastro, edição, detalhe com progresso por médico, reprocessamento, remoção |
| Médicos | Listagem com busca, cadastro com **senha provisória gerada e exibida uma única vez**, edição, ativar/desativar, resetar senha |
| Matrículas | Listagem com filtros (situação, curso, busca), criação, **aprovar/reprovar/resetar/cancelar** |
| Enviar gravação | Upload em blocos com barra de progresso e retomada |
| Auditoria | Registro das ações administrativas e aprovações |

Proteção: `@somente_admin` (login + `is_staff`); médico comum recebe **403**.

### 18.3 Teste com os arquivos reais do curso

Curso cadastrado: **17º Curso de Atualização em Ética e Bioética para Residência Médica**.

| Arquivo | Tamanho | Duração | Perfil |
|---|---|---|---|
| Encontro 1 — Webnário (25/05) | 7,7 GB | 3h53m (14.005 s) | 1920×1080, 30 fps, h264+aac, 4,6 Mbps |
| Encontro 2 — Webnário (26/05) | 5,3 GB | 2h41m (9.668 s) | idem |
| **Total** | **13 GB** | **6h35m** | — |

Resultados do teste de ponta a ponta com o arquivo real (3.902 segmentos HLS):

```
2) Estado do player  → duration=14005.02s (3.9h)
3) Heartbeat         → 12s de reprodução real creditados
                     → salto de 10 min RECUSADO e marcado como suspeito
4) Playlists         → variante assinada com 3502 segmentos
5) Segmentos         → primeiro liberado (200) | além do limite bloqueado (403)
```

### 18.4 Correções e melhorias de arquitetura_decorrentes do teste real

1. **Upload sem etapa de merge.** A primeira tentativa gravava cada bloco em arquivo separado e
   depois concatenava — o servidor foi **matado por falta de memória** ao juntar 7,7 GB
   (esta máquina tem 6,9 GB de RAM). Agora os blocos são gravados **direto na posição final**
   do arquivo: metade do uso de disco, sem merge, e o upload de 7,7 GB conclusion com sucesso.
2. **Retomada de sessão.** Se o envio cai, o painel reenvia o mesmo arquivo: o servidor
   encontra a sessão incompleta do mesmo vídeo (mesmo tamanho) e devolve os blocos já
   gravados — a gravação não começa do zero.
3. **`VIDEO_PIPELINE` (`auto` | `remux` | `transcode`).** `auto` transcodifica fontes acima de
   720p (gera 720p/540p/360p com ABR, ~3× menos banda para o aluno) e faz *remux* das demais
   (minutos, sem perda). O `remux` foi usado no teste real para validar o caminho rápido.
4. **Limpeza automática de uploads abandonados** no worker (`runworker --limpar`).
5. **Correção de bug** em `format_hours()` (quebrava com cursos de mais de 1 hora — exatamente
   o caso dos vídeos reais) e na máscara do CPF no formulário de médico.

### 18.5 Estado atual

- 38 testes automatizados + teste de fumaça contra o curso real: **OK**.
- Painel, área do médico e upload: **OK**.
- Pendente de decisão do CREMEPE: TLS/domínio, LGPD, importador de reprovados, certificado
  em PDF, apostila, teste de carga e política de arquivamento dos originais.

---

## 19. Rodada 3 — identidade e conteúdo reais do curso

### 19.1 O que a análise dos vídeos revelou

O quadro de abertura do Encontro 1 (00:09:00) e os slides de conteúdo identificam:

| Item | Conteúdo real |
|---|---|
| Curso | 17º Curso de Atualização em Ética e Bioética **para Residência Médica** |
| Instituição | **Escola Superior de Ética Médica (ESEM)** — Educação Médica Continuada |
| Apoio | **CREMEPE** + **CEREMPE** (Comissão Estadual de Residência Médica de Pernambuco) |
| Datas | **25 e 26 de maio de 2026** |
| Formato | **Webnário**, 2 encontros, 6h35 no total |
| Metodologia | Discussão de **casos clínicos** (Caso, Oficina, Argumentos, votação; CFM/STF/CEREMPE) |
| Visual | Gradiente turquesa→azul, **emblema (mandala) da ESEM**, títulos em verde-petróleo |

Importante: o `banner.jpg` fornecido e o **comunicado "ATENCAO, INSCRITOS!"** do curso - nao e a capa do curso.
serve de inspiração de cor, mas **não** é a capa. A capa agora é o slide de abertura real.

### 19.2 O que foi alterado

1. **Assets** — `emblema-esem.png` (logo oficial fornecida em `arquivos/`) e a versão branca
   (silhueta via canal alfa, `scripts/gerar_assets_curso.sh`); capa do curso e miniaturas dos
   encontros extraídas das gravações (slide de abertura e primeira palestra).
2. **Metadados** — título, turma (`Webnário — 25 e 26 de maio de 2026`), descrição com o
   conteúdo real e os encontros renomeados para *Webnário — Dia 1/Dia 2*.
3. **Textos institucionais** — login com o título do curso + emblema da ESEM, nome completo
   "Escola Superior de Ética Médica" na navegação, rodapé com as três marcas
   (CREMEPE · ESEM · CEREMPE) e rótulo "Encontro" no lugar de "Aula".
4. **Paleta mantida** (decisão do solicitante): é a do design original dos slides.
5. **Ferramentas** — `scripts/gerar_assets_curso.sh` (assets a partir do vídeo),
   `scripts/extrair_quadros.sh` + `scripts/amostrar_cores_video.sh` (análise),
   `--curso-id` no `cenario_inicial` para recriar o cenário sem duplicar o curso.

### 19.3 Validação

59 testes automatizados OK · jornada do médico OK · assets e stream OK.

---

## 20. Rodada 4 — portal de cursos da ESEM

### 20.1 Objetivo

Transformar o portal em um **portal de cursos da ESEM** (genérico, para qualquer curso/
simpósio) e retirar as referências fixas ao 17º Curso, mantendo-as apenas na página do
próprio curso e no banner de acesso.

### 20.2 Pesquisa (fontes oficiais)

- `cremepe.org.br/escola-superior-de-etica-medica` — definição da ESEM, resoluções
  criadoras, temas em debate, cooperação com FPS/UFPE
- `cremepe.org.br` — notícias dos cursos de atualização (16ª e 17ª edições), aula de ética
  médica para formandos, simpósios de medicina de família

### 20.3 Textos institucionais aplicados

| Onde | Conteúdo |
|---|---|
| **Rodapé** (todas as páginas) | Definição da ESEM (núcleo técnico permanente; Resoluções 03/2005 e 08/2011; pioneira no Nordeste), endereço do CREMEPE, marcas CREMEPE · ESEM · CEREMPE, link para o portal do CREMEPE |
| **Área do médico** | Cabeçalho "Portal de cursos da ESEM" + bloco "Sobre a ESEM" com missão, temas em debate e integração institucional |
| **Painel administrativo** | Cabeçalho da barra lateral "Portal de cursos — Escola Superior de Ética Médica" |
| **Banner de acesso (login)** | Mantém o 17º Curso (acesso ao curso em andamento) + linha "Portal de cursos da ESEM" |

### 20.4 Correção incidental

Auditoria de codificação revelou que um comando do PowerShell (Set-Content com `-Encoding UTF8`
em Windows PS 5.1) havia **duplicado a codificação** de `templates/panel/base.html` e
`apps/accounts/tests.py` — os acentos do menu do painel estavam corrompidos
("Opera??o", "M??dicos"). Ambos foram reescritos, e `scripts/corrigir_codificacao.py` +
`scripts/conferir_identidade.py` passaram a detectar e impedir a regressão.

### 20.5 Validação

`scripts/conferir_identidade.py` (novo) confere: presença do texto institucional da ESEM
nas telas, ausência de acentos corrompidos e **escopo das referências ao 17º curso**
(apenas em `/entrar/` e na página do curso). 63 testes automatizados OK.

---

## 21. Rodada 5 — diagnóstico do "desloga ao recarregar"

### 21.1 Sintoma

- Ao abrir a página do curso, o player acusava *"Sua sessão expirou"*.
- Um F5 simples devolvia o médico para a tela de login.

### 21.2 Investigação (com instrumentação no servidor)

O log do nginx e o log da aplicação Mostraram o padrão exato:

```
12:33:46 POST /entrar/?next=/cursos/1/        → 302   (login OK)
12:33:47 GET  /cursos/1/                       → 200   (autenticado)
12:33:47 GET  /api/player/state/?...            → 401   (a API NAO reconheceu a sessão)
12:33:48 GET  /cursos/1/                       → 302   (a pagina tambem perdeu a sessao)
```

A instrumentação da API mostrou o dado decisivo:

```
API sem sessao valida: cookies={'sessionid': 'a1vdmxrddkpx8v9sq8t4mg4lor205ief', ...}
                        session_key=None   session_no_banco=False
```

**O navegador enviava um `sessionid` que não existia no banco** (órfão) — a página
funcionava porque o Chrome envia cookies conforme o *path* da URL, e a API recebia o
cookie errado.

### 21.3 Correções

1. **Logout somente por POST** — o link "Sair" (GET) era acionado por clique
   acidental/prefetch e derrubava a sessão silenciosamente (ocorrido no log às 12:18:30).
2. **`never_cache`** em login, logout, "meus cursos" e página do curso — impede que o
   navegador reaproveite um 302 antigo de login (sem o `Set-Cookie`).
3. **Limpeza do cookie órfão**: toda resposta 401 (API) ou 302 (página) devolve
   `Set-Cookie: sessionid=; Max-Age=0; Path=/`, o que faz o próximo login funcionar.
4. **API responde 401 em JSON** em vez de redirecionar, e o player mostra "Sua sessão
   expirou" com link para entrar novamente.
5. **`preserve_sessoes`**: o reset de senha do cenário não encerra mais a sessão do
   navegador em desenvolvimento.

### 21.4 Causa raiz e correção definitiva

O log instrumentado revelou o padrão exato: **o navegador enviava cookies NOVOS e ANTIGOS
no mesmo cabeçalho** (`csrftoken` e `sessionid` duplicados — provavelmente um cookie
antigo deixado por um logout anterior). Como o Django lê apenas um `sessionid` (o primeiro
do cabeçalho) e o antigo apontava para uma sessão que não existia mais, o médico via:

```
GET /cursos/1/            → 200   (o cookie válido chegou antes)
GET /api/player/state/    → 401   (chegou o órfão)
```

**`SessaoUnicaMiddleware`** (antes do `AuthenticationMiddleware`): examina **todas** as
chaves `sessionid` do cabeçalho e, havendo mais de uma, escolhe a que **ainda existe no
banco**. Com isso, a duplicidade deixa de importar — o servidor passa a ser tolerante.

Complementos mantidos: logout só por POST, `never_cache` nas páginas/login e limpeza do
cookie órfão nas respostas 401/302.

### 21.5 Causa raiz: um componente do ambiente reescreve o cabeçalho Cookie

O log com o cabeçalho **cru** mostrou que o Chrome enviava, em todas as requisições, os
**mesmos cookies fixos desde as 09:33** — inclusive o `csrftoken` que o login acabara de
redefinir, e um `__stripe_mid` do Stripe (nenhuma relação com o portal):

```
cookie_bruto='__stripe_mid=a44465f9-...; csrftoken=WyCrkN1...; sessionid=a1vdmxrdd...'
```

E, mesmo depois de trocar para nomes de cookie próprios (`cremepe_sid`,
`cremepe_csrftoken`), o Chrome **continuou enviando apenas os antigos** — logo, o cabeçalho
`Cookie` inteiro está sendo **reescrito** nas chamadas `fetch` do player (a navegação normal
passa intacta; por isso a página abria e a API não).

### 21.6 Correção definitiva: token de API assinado

O player deixa de depender do cookie:

1. A página do curso (autenticada) entrega um **token assinado** (HMAC com o `SECRET_KEY`)
   contendo `usuario.sessao.expiracao.assinatura` — `<meta name="api-token">`.
2. O player envia `Authorization: Bearer <token>` nas chamadas da API **e** nas do hls.js
   (`xhrSetup`).
3. O `SessaoUnicaMiddleware` valida a assinatura e restaura a sessão对应的 usuário.
4. As views do player foram marcadas `csrf_exempt` (a proteção passa a ser o token
   assinado, no lugar do cookie de CSRF) — apenas elas; o upload continua exigindo CSRF.

Com isso, **o portal funciona mesmo com o cabeçalho Cookie reescrito** — situação real em
ambientes institucionais (AV/proxy com inspeção de HTTPS, extensões de navegador).

### 21.7 Validação

- 75 testes automatizados (token: geração, adulteração, expiração, uso sem cookie).
- `scripts/reproduzir_proxy_rewrites.py` simula o proxy reescrevendo o cabeçalho e comprova
  o caminho **completo** do player: estado do vídeo (200), heartbeat (200), manifesto (200),
  variante (200, 3.502 segmentos) e **segmento de vídeo entregue** (2,3 MB, magic `0x47`).
- `scripts/reproduzir_cookie_duplicado.py`, `scripts/reproduzir_cookie_orfao.py` e
  `scripts/testar_sessao_preservada.py` complementam a verificação.

