# 02 — Sistema visual e decisões por página

As regras permanentes estão em [visual design](../visual-design.md) e em
[linhas de serviço](../service-status-cards.md). Aqui fica o porquê.

## Sistema visual

- **Uma superfície.** Tudo o que o app desenha (cartões de escolha, painéis de
  Compartilhar, computadores, estados vazios) usa a aparência `card` do
  libadwaita: `@card_bg_color`, a sombra de cartão do Adwaita, raio 12px.
  Removidos todos os gradientes, brilhos e cores por papel.
- **Um acento.** Compartilhar e Conectar deixaram de ter cores diferentes
  (azul/verde); o ícone e o texto já os distinguem.
- **Interação.** Hover `alpha(currentColor, 0.05)`, pressão `0.09`, foco
  só por teclado (`:focus-visible`). Sem transições nas linhas de serviço.
- **Tokens** são as cores nomeadas do Adwaita: claro, escuro e alto contraste
  vêm do sistema. O Gamer continua uma camada (`gamer.css`) sobre a mesma
  estrutura.

## Navegação e barra lateral

- Menu ☰ no cabeçalho da barra lateral, como no AshyWall; o cabeçalho do
  conteúdo fica só com o título ou as abas.
- Grupos com título: **Início** · **Transmissão** (Compartilhar, Conectar) ·
  **Conexão segura** (Conecte seus dispositivos) · **Serviços**. Os títulos
  reaproveitam textos já traduzidos nos 32 idiomas: nenhum texto novo.
- Estado da tarefa como texto discreto no fim da linha (como as contagens do
  AshyWall), verde só quando algo está rodando.
- Serviços (Sunshine/Moonlight, Tailscale, ZeroTier, Headscale) viraram linhas
  comuns da barra lateral, com o estado escrito e um ícone pequeno.

## Páginas

- **Início.** Logo, título, descrição e **Começar com o Assistente Guiado**
  direto na página; as duas escolhas (Compartilhar, Conectar) são os únicos
  cartões, porque são o que se clica. O assistente guiado e a verificação de
  componentes continuam os mesmos.
- **Compartilhar.** Mesmos painéis e o mesmo fluxo (origem, iniciar, PIN,
  imagem e captura), agora na superfície única; o botão principal é o
  `suggested-action`/`destructive-action` nativo, só mais alto.
- **Conectar.** Os computadores ficam direto na página, em duas colunas, sem o
  cartão em volta. Um computador *Offline* tem o ícone neutro, além da
  palavra.
- **Redes privadas, Preferências, Suporte, diálogos.** Já eram libadwaita
  nativo; herdam a superfície e o raio novos sem mudança de estrutura.

## O que não mudou, de propósito

Fluxos, contratos com Sunshine/Moonlight/VPNs, o assistente guiado, a
instalação de dependências, a persistência de preferências e as regras de
segurança. Nenhum serviço foi reescrito para o redesenho.
