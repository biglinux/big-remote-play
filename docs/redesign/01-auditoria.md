# 01 — Auditoria

Registro do projeto (2026-11-14). O aplicativo não lê nada desta pasta.

## O que foi examinado

- **Big Remote Play** (branch `feat/redesign-ashywall`, a partir de
  `feat/touch-controls`): `AGENTS.md`, `src/big_remote_play/ui/AGENTS.md`,
  [visual design](../visual-design.md), `main_window.py`, `host_view.py`,
  `guest_view.py`, `service_status_card.py`, `components.py`, os dois CSS
  (`style.css`, `gamer.css`) e os testes que fixam estrutura e estilo.
  Interface: ~22,7 mil linhas em `ui/`; `host_view.py` sozinho tem 5,5 mil.
- **AshyWall** (`/home/ruscher/Documentos/Git/ashywall`, `3344884`):
  `ui/window.py` (barra lateral, cabeçalho, ponto de quebra), `ui/style.css` e
  as capturas em `docs/screenshots/`.

O aplicativo foi aberto de verdade (sob Xvfb, com `HOME` descartável) em
claro, escuro e Gamer, a 1230, 800 e 420 px, antes e depois de cada mudança.

## O que o AshyWall faz

- `Adw.NavigationSplitView` (barra lateral 220–280 px, recolhe abaixo de
  640sp). O cabeçalho da barra lateral tem o nome do app e o menu ☰.
- Barra lateral `navigation-sidebar` com grupos sob títulos `heading
  dim-label`; o número de itens de cada lugar fica no fim da linha, apagado.
- Conteúdo nativo: listas `boxed-list`, cartões só onde se clica, nenhum
  gradiente. O CSS do app tem ~100 linhas, quase só para a grade de imagens.

## O que afastava o Big Remote Play disso

| Onde | Antes |
|---|---|
| Barra lateral | separadores sem nome; estados como etiquetas coloridas; serviços como cartões com borda, fundo tingido e animação |
| Início | fundo com gradiente; cabeçalho da marca dentro de um cartão com gradiente e borda |
| Cartões | gradientes, bordas coloridas por papel (Compartilhar azul, Conectar verde), sombras próprias |
| Conectar | cartões de computador dentro de outro cartão |
| Menu | no cabeçalho do conteúdo, disputando espaço com as abas |

A base técnica já era a mesma do AshyWall (GTK 4, libadwaita ≥ 1.7,
`NavigationSplitView`, `ViewSwitcher`, `PreferencesDialog`). Por isso o
redesenho é de linguagem visual e composição, sem reescrever serviços.

## Linha de base medida

Mesma máquina, Xvfb, `GSK_RENDERER=cairo`, `HOME` descartável, 3 execuções.

| Medida | Antes |
|---|---|
| Primeira janela | 0,56–0,60 s |
| Memória (RSS) parado | 131–133 MB |
| CPU parado (10 s) | 0–0,8 % |
| Processos filhos | 2 |
| Navegação entre páginas | mediana 4,34 ms, máx. 12,2 ms (20 trocas) |
| Testes | 1793 passaram |
