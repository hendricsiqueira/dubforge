# DubForge 0.1.1

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

- processamento de um projeto por vez;
- uma voz por projeto, igual à limitação atual do ZastTranslate;
- o progresso detalhado de cada segmento também aparece no terminal;
- os modelos continuam sendo armazenados no cache usado pelo ZastTranslate.
