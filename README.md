# DubForge 0.2.0

Aplicativo local para gerar dublagens em lote a partir de um único vídeo ou áudio.
Ele usa a instalação já funcional do ZastTranslate como motor, mas mantém projetos,
cache e resultados em uma pasta separada.

## O que esta versão faz

- transcreve uma única vez com WhisperX;
- traduz vários idiomas com Qwen 2.5 7B;
- armazena tradução natural e versão ajustada ao tempo;
- descarrega o Qwen antes de carregar o VoxCPM 2;
- clona a voz original ou usa uma referência escolhida;
- preserva música e ambiente com Demucs;
- exporta apenas MP3 e SRT, sem renderizar vídeo;
- retoma projetos sem repetir etapas concluídas;
- guarda cache de áudio por idioma, evitando mistura entre idiomas.
- aceita vários áudios ou vídeos em uma única fila;
- termina todos os idiomas de um arquivo antes de iniciar o próximo;
- salva a fila para ser retomada após erro, reinício ou desligamento da instância.
- mede o tempo de preparação, transcrição, tradução e dublagem por idioma;
- estima o custo da Vast em reais e compara com um serviço cobrado por minuto.
- mantém a fila em um gerenciador independente da página do navegador;
- reconecta automaticamente ao trabalho em andamento ao recarregar a interface;
- mostra porcentagem por etapa, idioma, projeto e lote, além de ETA;
- monitora GPU, VRAM, RAM e disco em tempo real;
- pausa automaticamente antes de encher o disco e retoma quando houver espaço;
- escolhe lote do Qwen e quantidade de trabalhadores VoxCPM conforme o hardware;
- executa idiomas em processos VoxCPM separados quando há VRAM suficiente;
- volta automaticamente para um trabalhador quando o paralelo falha.

## Trabalho persistente

O botão **Dublar / retomar** apenas coloca o trabalho na fila. O processamento é
controlado por um `JobManager` dentro do processo do servidor, e não pela conexão
do navegador. A página consulta o estado salvo a cada dois segundos. É possível
fechar ou recarregar a página e acompanhar novamente o mesmo trabalho.

Os registros ficam em `projects/jobs/` e incluem heartbeat, etapa, porcentagens,
ETA, uso de recursos, saídas e último erro. Se o processo do DubForge reiniciar,
trabalhos que estavam em execução voltam para a fila e reutilizam os checkpoints.
Os botões **Pausar**, **Continuar** e **Cancelar** também são propagados aos
processos VoxCPM; a pausa acontece entre segmentos e não descarta o que já foi
gerado.

Para sobreviver à queda do SSH, o servidor precisa estar em `tmux` ou em um
serviço do sistema:

```bash
./scripts/run-tmux.sh
```

Um modelo para `systemd` está em `deploy/dubforge.service.example`.

## Desempenho adaptativo

O campo **Desempenho** oferece quatro políticas:

- **Automático** — calcula lote do Qwen e até três trabalhadores VoxCPM;
- **Econômico** — usa um trabalhador e lotes menores;
- **Equilibrado** — limita o VoxCPM a dois trabalhadores;
- **Máximo** — usa o limite permitido pelos recursos detectados.

O agendador reserva VRAM para evitar paginação. Cada trabalhador VoxCPM roda em
processo separado, com cache e pasta de segmentos próprios. Caso um processo
falhe por falta de memória, o idioma é retomado sequencialmente e os segmentos
já produzidos são reutilizados.

Os limites podem ser ajustados copiando `.env.example` para `.env`. Em tempo de
execução, menos de 5 GB livres pausa o trabalho; ele retoma automaticamente ao
atingir 7 GB. Um projeto novo aguarda pelo menos 10 GB livres por padrão.

## Medidas exibidas

- progresso exato por itens concluídos em cada etapa;
- progresso ponderado estimado do projeto e do lote;
- segmento, idioma e arquivo atuais;
- tempo acumulado por operação e ETA recalculado;
- GPU, utilização, VRAM, RAM e disco livre;
- quantidade de trabalhadores VoxCPM e lote Qwen escolhidos.

## Processamento em lote

No campo **Vídeo ou áudios originais**, selecione todos os arquivos desejados.
Defina os idiomas uma única vez e clique em **Dublar / retomar**. O DubForge cria
um projeto independente para cada arquivo e uma fila persistente em
`projects/batches/`.

Ele processa a fila em ordem: arquivo 1 em todos os idiomas, depois arquivo 2,
e assim por diante. Para continuar uma fila interrompida, selecione-a em
**Lote salvo** e clique em **Retomar lote selecionado**.

## Métricas e custos

Antes de iniciar, informe o custo total da instância em US$/hora, a cotação do
dólar e o valor do serviço de comparação em R$/minuto por idioma. O painel de
andamento mostra os tempos acumulados de cada operação, os minutos efetivamente
dublados e a economia estimada. Essas métricas ficam salvas no `project.json` e
continuam disponíveis ao reabrir o projeto ou retomar o lote.

## Estrutura esperada no Windows

```text
C:\SELF-HOSTED-PROJECTS\
├── ZastTranslate\
│   └── .venv\
└── DubForge\
    ├── app.py
    ├── start.ps1
    └── projects\
```

## Como abrir

No PowerShell:

```powershell
cd C:\SELF-HOSTED-PROJECTS\DubForge
.\start.ps1
```

Ou dê dois cliques em `start.bat`.

Esta versão também adiciona automaticamente `ZastTranslate\.venv\Scripts` ao
`PATH`. Isso permite que os módulos reaproveitados encontrem `demucs.exe` ao
separar voz/ambiente, mesmo sem ativar manualmente o ambiente virtual.

A interface abre em `http://127.0.0.1:7861`. O ZastTranslate continua usando a
porta `7860`, portanto os dois não entram em conflito.

Se o ZastTranslate estiver em outro local:

```powershell
$env:ZAST_TRANSLATE_PATH = "D:\IA\ZastTranslate"
.\start.ps1
```

## Retomada

Cada projeto contém:

```text
projects\nome-do-projeto\
├── project.json
├── source\
├── cache\
│   ├── transcription.json
│   └── voice_reference.wav
├── translations\
├── audio_segments\
└── outputs\
```

Ao abrir um projeto existente, o DubForge pula a transcrição, as traduções e os
MP3 que já estejam válidos. Para adicionar um idioma depois, marque o novo idioma
e execute novamente.

## Observações desta versão

- uma fila de projeto/lote é executada por vez; idiomas podem ter múltiplos
  trabalhadores VoxCPM quando o hardware comportar;
- uma voz por projeto, igual à limitação atual do ZastTranslate;
- o progresso detalhado de cada segmento também aparece no terminal;
- os modelos continuam sendo armazenados no cache usado pelo ZastTranslate.

## Segurança e licenças

O aplicativo escuta em `127.0.0.1` e deve ser acessado remotamente por túnel SSH.
Projetos, mídia, bancos de status, modelos, logs e `.env` são ignorados pelo Git.
Consulte [SECURITY.md](SECURITY.md) antes de expor o serviço na internet.

O código do DubForge é disponibilizado sob a licença MIT. O ZastTranslate é uma
dependência externa e não é redistribuído neste repositório. Modelos como
WhisperX, Qwen, Demucs e VoxCPM possuem licenças próprias; confirme os termos de
cada peso antes de uso comercial ou redistribuição.
