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