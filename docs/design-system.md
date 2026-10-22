# Sistema de design — CREMEPE / ESEM

## Identidade visual

As cores foram extraídas dos arquivos oficiais do CREMEPE (`arquivos/logotipo-cremepe.png`) e
**conferidas nos slides reais do curso** (`arquivos/*.mp4`):

| Token Tailwind | Hex | Origem |
|---|---|---|
| `marca-500` | `#009965` | Verde do anel do logotipo CREMEPE |
| `marca-100` / `marca-50` | `#c9f4e6` / `#edfbf6` | Tons claros do verde institucional |
| `turquesa-500` | `#00a396` | Início do gradiente dos slides do curso |
| `pacifico-500` | `#0079b0` | Azul do gradiente dos slides do curso |
| `abissal-900` | `#00455f` | Azul profundo do rodapé do banner |
| `ardesia-500` | `#595959` | Cinza do texto do logotipo |

> No vídeo gravado (H.264, 4,6 Mbps) as cores aparecem lavadas (`#5EAEA1` → `#2B7CA9`).
> Mantivemos a paleta do **design original**, que é a mesma dos slides.

Gradientes institucionais:

- `bg-marca-gradiente` → `marca-500 → turquesa-500 → pacifico-500` (botões, barras de progresso)
- `bg-abissal-gradiente` → `abissal-900 → abissal-700 → marca-700` (rodapé, login)

Definição completa em `frontend/tailwind.css` (bloco `@theme`).

## Assets institucionais

| Arquivo | Origem | Uso |
|---|---|---|
| `static/img/logotipo-cremepe.png` | `arquivos/logotipo-cremepe.png` | Logotipo CREMEPE (aparece invertido/branco no gradiente) |
| `static/img/emblema-esem.png` | `arquivos/logo-esem.png` | Emblema da Escola Superior de Ética Médica (verde, com transparência) |
| `static/img/logomarca-esem.png` | `arquivos/logomarca-esem.png` | Logomarca completa ESEM (emblema + nome em branco) para fundos com gradiente |
| `static/img/emblema-esem-branca.png` | gerado por `scripts/gerar_assets_curso.sh` | Silhueta branca do emblema (para o gradiente) |
| `static/img/banner-curso.jpg` | `arquivos/banner.jpg` | Comunicado institucional (não é a capa do curso) |
| `media/cortes/capa-*.jpg` | extraído do **slide de abertura** do vídeo | Capa do curso no cartão do aluno |

A capa e as miniaturas dos vídeos são geradas a partir das gravações:

```bash
docker compose exec app sh scripts/gerar_assets_curso.sh
```

## Stack de front-end

- **Tailwind CSS v4** compilado pelo CLI standalone (sem Node no servidor, sem CDN).
- **Sem JavaScript de framework**: apenas `hls.js` (player) e `app.js` (menus).
- Fontes: pilha do sistema (`Segoe UI`, `system-ui`, …) — sem webfont, sem requisição externa.

### Fluxo de trabalho do CSS

```bash
# 1. editar frontend/tailwind.css (tema + componentes)
# 2. compilar
tools/tailwindcss.exe -i frontend/tailwind.css -o static/css/tailwind.css --minify
# 3. conferir se todas as classes dos templates existem no CSS
python scripts/conferir_css.py
```

O CSS compilado é versionado no repositório — o servidor Docker **não** precisa do Tailwind.

## Componentes (Tailwind `@layer components`)

| Classe | Uso |
|---|---|
| `.btn` + `.btn-primary` / `.btn-secundario` / `.btn-fantasma` / `.btn-perigo` / `.btn-sm` | Botões |
| `.cartao` / `.cartao-padrao` | Cartões |
| `.campo` | Campos de formulário |
| `.rotulo` | Rótulos de formulário |
| `.etiqueta` | Badges de status |
| `.celula` / `.cabecalho-tabela` | Tabelas |
| `.link-menu` | Itens de menu |
| `.sombra-suave` / `.sombra-forte` | Elevações |

## Ícones

SVG inline do Heroicons (outline), expostos aos templates pelo context processor
`apps.web.identidade.identidade`:

```html
{{ icons.curso|safe }}
{{ icons.medicos|safe }}
```

Lista em `apps/web/context_processors.py`.

## Regras de uso

- Nunca usar cor fora da paleta do tema (`marca`, `turquesa`, `pacifico`, `abissal`, `ardesia`
  e as escala `slate` do Tailwind).
- Status sempre com cor **e** texto (`etiqueta`), para acessibilidade.
- Telas do painel usam a mesma base do aluno (`templates/base.html`), com a barra lateral
  em `templates/panel/base.html`.
- Contraste mínimo AA em textos sobre gradiente (usar `abissal-950` com opacidade, não cinza).
