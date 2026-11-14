# 03 — Validação, desempenho e limitações

## Inspeção visual (aplicativo real, Xvfb)

| Situação | Resultado |
|---|---|
| Início, Compartilhar, Conectar, Conecte seus dispositivos, página da Tailscale, Preferências, Suporte, Opções avançadas | revisados em claro e escuro |
| Gamer (Início, Conectar, Compartilhar, Preferências, Suporte, redes) | mesma estrutura, paleta própria; o cabeçalho do Início mantém o painel |
| 1230 × 850 | duas colunas de computadores, abas no cabeçalho |
| 800 × 700 | uma coluna; abas descem para a barra inferior |
| 420 × 820 | barra lateral recolhe, botão **Voltar**, sem rolagem horizontal |
| pt-BR | títulos e estados traduzidos ([print](../screenshots/connect-pt-BR.png)) |
| Hebraico (RTL) | janela espelhada; os títulos da barra lateral usavam margem CSS (física) e ficavam desalinhados — passaram a margens lógicas no código |
| Japonês (CJK) | quebra de linha correta no título do Início e nas linhas |
| Alto contraste (`ADW_DEBUG_HIGH_CONTRAST=1`) | sombras trocadas por bordas de contraste total; estados com texto em cor plena |

Os prints do README foram gerados do aplicativo real com dados fictícios
(computadores "Living room PC", redes `example.com`, `100.64.x`). No gerador,
as setas dos cartões saem como `→`; no aplicativo instalado, como `›`
(a mesma ação, com o glifo `go-next-symbolic` resolvido em outra ordem de
temas de ícones).

## Desempenho (mesmo ambiente da linha de base)

| Medida | Antes | Depois |
|---|---|---|
| Primeira janela | 0,56–0,60 s | 0,57–0,61 s |
| Memória (RSS) parado | 131–133 MB | 131–134 MB |
| CPU parado (10 s) | 0–0,8 % | 0,1–0,5 % |
| Processos filhos | 2 | 2 |
| Navegação entre páginas (20 trocas) | mediana 4,34 ms, máx. 12,2 ms | mediana 4,26 ms, máx. 14,0 ms |

Sem regressão. O redesenho não acrescenta temporizadores, processos nem
animações (as linhas de serviço perderam a transição e a animação que tinham).

## Verificações automáticas

`make lint`, `make typecheck` (0 erros), `make check-i18n` (nenhum texto
novo), `make metadata-check`, `make release-check` e a suíte completa com
Xvfb + D-Bus: todos passaram (1793 testes, 0 falhas; `release-check` 0 erros,
0 avisos). Testes atualizados para o contrato novo:
títulos de grupo na barra lateral (com papel de cabeçalho para leitores de
tela), um título **Serviços**, linhas de serviço sem movimento.

## Não validado aqui

- KDE Plasma, GNOME e Window Maker X11 reais, escala fracionária e leitor de
  tela (Orca): só estrutura, CSS e papéis acessíveis foram verificados; falta
  olhar em cada ambiente.
- Estado "Compartilhando" com um dispositivo jogando: não foi iniciado
  compartilhamento real durante a inspeção.
