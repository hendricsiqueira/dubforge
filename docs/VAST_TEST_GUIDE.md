# Guia vivo de teste do DubForge na Vast.ai

Este documento acompanha os testes do DubForge 0.2.0 na Vast.ai. Marque os
itens concluídos e preencha o **Diário de testes** no final. Quando houver uma
correção, atualize este mesmo arquivo no GitHub.

> Estado inicial: versão em teste na branch
> `feat/adaptive-persistent-jobs`, Pull Request #3.

## Máquina de teste atual — 23/11/2026

```text
GPU: NVIDIA GeForce RTX 4090
VRAM: 49.140 MiB (~48 GB)
Driver/KMD: 620.73.04
CUDA reportada pelo driver: 13.5
RAM: 117 GiB total / 105 GiB disponível
Swap: 8 GiB
Disco: 500 GB / 500 GB livre
Python do template: 3.12.16
```

Situação encontrada: `/workspace/DubForge` existe, mas `ZastTranslate` e
`dubforge-env` ainda não. O instalador abaixo usa a revisão do ZastTranslate que
já funcionou anteriormente na Vast, em vez da versão atual do repositório.

## 1. Criar a instância

Este teste parte do mesmo template/imagem em que o ZastTranslate já funcionou.
Na tela de aluguel da Vast, use:

- [ ] 1 GPU RTX 5090, RTX 4090 ou equivalente;
- [ ] conexão SSH direta disponível;
- [ ] confiabilidade alta do host;
- [ ] pelo menos 64 GB de RAM para o teste com vários idiomas;
- [ ] **300 GB de disco** para o lote completo de 12 aulas;
- [ ] modo de inicialização **SSH**.

Para um teste curto de um único áudio, 150 GB normalmente bastam. Não use
novamente 100 GB para o lote completo: na execução anterior, apenas quatro
projetos, os modelos e o ambiente chegaram a aproximadamente 99 GB.

A Vast não permite aumentar o disco da instância depois da criação. Se o espaço
acabar, é necessário criar outra instância. Documentação oficial:

- <https://github.com/vast-ai/docs/blob/main/guides/instances/docker-environment.mdx>
- <https://github.com/vast-ai/docs/blob/main/guides/get-started/quickstart.mdx>

Anote os dados:

```text
ID da instância:
GPU:
VRAM:
Preço por hora:
Disco contratado:
Template/imagem:
```

## 2. Conectar pelo PowerShell

Na página **Instances**, clique em **Connect** e copie o comando SSH. Acrescente
o túnel da porta 7861 caso ele não esteja presente:

```powershell
ssh -p PORTA root@IP_DA_VAST -L 7861:localhost:7861
```

Exemplo de formato — não copie estes valores:

```powershell
ssh -p 12345 root@203.0.113.10 -L 7861:localhost:7861
```

Use exatamente o IP e a porta mostrados pela sua instância. Com chave SSH
configurada corretamente, a autenticação ocorre uma vez, sem solicitar senha
repetidamente.

## 3. Conferir a máquina antes de instalar

Execute no terminal SSH:

```bash
nvidia-smi
df -h /workspace
free -h
python --version
ls -la /workspace
```

Critérios:

- [ ] a GPU aparece no `nvidia-smi`;
- [ ] a VRAM esperada aparece;
- [ ] `/workspace` mostra o disco contratado;
- [ ] existe `/workspace/ZastTranslate` ou o template consegue restaurá-lo;
- [ ] existe `/workspace/dubforge-env` ou outro ambiente Python funcional.

Se `ZastTranslate` ou o ambiente Python não existir, pare neste ponto e envie a
saída dos cinco comandos. A instalação do motor depende da imagem/template usado
e não deve ser improvisada com versões diferentes de CUDA ou PyTorch.

## 4. Baixar ou atualizar a versão em teste

Para uma instância nova:

```bash
cd /workspace
git clone https://github.com/hendricsiqueira/dubforge.git DubForge
cd /workspace/DubForge
git switch feat/adaptive-persistent-jobs
```

Se `/workspace/DubForge` já existir:

```bash
cd /workspace/DubForge
git fetch origin
git switch feat/adaptive-persistent-jobs
git pull --ff-only origin feat/adaptive-persistent-jobs
```

Confirme a versão:

```bash
git branch --show-current
git log -1 --oneline
```

O primeiro comando deve mostrar `feat/adaptive-persistent-jobs`.

## 5. Instalação automática do motor na Vast

Na instância atual, execute:

```bash
cd /workspace/DubForge
git pull --ff-only origin feat/adaptive-persistent-jobs
chmod +x scripts/install-vast.sh
./scripts/install-vast.sh
```

O instalador:

- instala FFmpeg, Git, tmux e bibliotecas do sistema;
- baixa o ZastTranslate na revisão compatível `a567da7...`;
- cria `/workspace/dubforge-env`;
- instala PyTorch 2.8.0 com CUDA 12.6, WhisperX e VoxCPM 2;
- configura o `.env`;
- verifica GPU, imports e executa os 17 testes do DubForge.

Essa etapa pode levar vários minutos e baixar muitos gigabytes. Não interrompa.
Ao terminar, envie a parte final da saída a partir de `Torch:`.

## 6. Conferir caminhos e limites

Crie o `.env` a partir do modelo:

```bash
cd /workspace/DubForge
cp -n .env.example .env
nano .env
```

Para o ambiente usado anteriormente, deixe o arquivo assim:

```dotenv
DUBFORGE_PROJECTS_DIR=/workspace/DubForge/projects
ZAST_TRANSLATE_PATH=/workspace/ZastTranslate
DUBFORGE_PYTHON=/workspace/dubforge-env/bin/python

DUBFORGE_DISK_WARNING_GB=30
DUBFORGE_MIN_START_DISK_GB=20
DUBFORGE_MIN_RUNTIME_DISK_GB=10
DUBFORGE_RESUME_DISK_GB=15

DUBFORGE_VOXCPM_WORKER_GB=11
DUBFORGE_VRAM_RESERVE_GB=4
```

Salve no `nano` com `Ctrl+O`, `Enter` e saia com `Ctrl+X`.

Se o Python estiver em outro lugar, descubra o caminho depois de ativar o
ambiente correto:

```bash
which python
```

Use o resultado em `DUBFORGE_PYTHON`.

## 7. Validação manual opcional

```bash
cd /workspace/DubForge
/workspace/dubforge-env/bin/python -m pip install -r requirements.txt
```

Valide o código sem carregar os modelos:

```bash
/workspace/dubforge-env/bin/python -m unittest discover -s tests -v
/workspace/dubforge-env/bin/python -m compileall -q app.py dubforge tests
```

Resultado esperado:

- [ ] 17 testes concluídos com `OK`;
- [ ] o `compileall` termina sem mensagem de erro.

## 8. Iniciar de forma persistente

```bash
cd /workspace/DubForge
./scripts/run-tmux.sh
```

Verifique:

```bash
tmux ls
tail -n 100 /workspace/DubForge/dubforge.log
```

O log deve mostrar:

```text
Running on local URL:  http://127.0.0.1:7861
```

O `tmux` mantém o DubForge rodando se a sessão SSH ou sua internet cair. Para
entrar no terminal do processo:

```bash
tmux attach -t dubforge
```

Para sair sem encerrar, pressione `Ctrl+B` e depois `D`.

## 9. Abrir no computador

Mantenha o PowerShell com o túnel SSH aberto e acesse:

<http://127.0.0.1:7861>

Se a página não abrir:

```bash
tmux ls
tail -n 200 /workspace/DubForge/dubforge.log
ss -ltnp | grep 7861
```

Se o túnel cair, abra outro PowerShell e execute novamente o mesmo comando SSH.
O processamento continua no servidor e a página volta a mostrar o trabalho
persistido.

## 10. Teste 1 — fumaça, um áudio curto

Use primeiro um áudio de 2 a 5 minutos, não o lote completo.

Configuração:

```text
Idioma original: Português
Idioma de destino: Inglês
Modelo Whisper: large-v3
Preservar música/ambiente: desativado
Gerar SRT: ativado
Desempenho: Automático
```

- [ ] clique em **Dublar / retomar**;
- [ ] confirme que o upload termina antes do processamento;
- [ ] veja porcentagem geral e por etapa;
- [ ] confirme GPU, VRAM, RAM e disco no painel;
- [ ] baixe o MP3 e o SRT assim que aparecerem em **Arquivos gerados**;
- [ ] ouça início, meio e fim do MP3.

Enquanto processa, abra outro terminal SSH:

```bash
watch -n 2 nvidia-smi
```

Em outro terminal:

```bash
watch -n 5 'df -h /workspace; du -sh /workspace/DubForge/projects 2>/dev/null'
```

## 11. Teste 2 — persistência e controles

Durante uma dublagem mais longa:

- [ ] clique em **Pausar** e confirme que o uso da GPU cai entre segmentos;
- [ ] clique em **Continuar** e confirme a retomada;
- [ ] feche a aba do navegador por dois minutos;
- [ ] abra novamente `http://127.0.0.1:7861`;
- [ ] confirme que o status e as porcentagens reaparecem;
- [ ] encerre apenas o túnel SSH e conecte novamente;
- [ ] confirme que o trabalho continuou no `tmux`;
- [ ] teste **Cancelar** somente em um projeto descartável.

Os checkpoints concluídos devem permanecer após pausa, cancelamento ou falha.

## 12. Teste 3 — vários idiomas e paralelismo

Use um áudio curto e selecione Inglês, Espanhol e Italiano.

- [ ] Desempenho em **Automático**;
- [ ] numa RTX 5090 de 32 GB, espere normalmente até 2 trabalhadores VoxCPM;
- [ ] confirme que cada idioma usa pasta e cache separados;
- [ ] confirme que os arquivos aparecem individualmente durante o trabalho;
- [ ] se faltar VRAM, confirme a mensagem de fallback para um trabalhador;
- [ ] compare o tempo com o Teste 1.

Não use **Máximo** no primeiro teste. Só experimente depois de confirmar que o
modo Automático está estável.

## 13. Teste 4 — lote real

Somente depois dos testes anteriores:

- [ ] envie os arquivos de áudio, evitando vídeos quando a imagem não for usada;
- [ ] comece com duas aulas;
- [ ] confirme o tamanho previsto em disco;
- [ ] depois envie o restante do lote;
- [ ] acompanhe o progresso geral, o arquivo atual e o idioma atual;
- [ ] baixe os idiomas concluídos sem esperar o lote inteiro.

Comandos de diagnóstico:

```bash
du -xhd1 /workspace 2>/dev/null | sort -h
find /workspace/DubForge/projects -type d -name audio_segments -exec du -sh {} + 2>/dev/null | sort -h
find /workspace/DubForge/projects -type d -name outputs -exec du -sh {} + 2>/dev/null | sort -h
```

## 14. Reiniciar somente o DubForge

Use isto apenas quando nenhuma etapa crítica estiver no meio da gravação:

```bash
tmux kill-session -t dubforge
cd /workspace/DubForge
./scripts/run-tmux.sh
tail -f /workspace/DubForge/dubforge.log
```

Ao reiniciar, trabalhos que estavam rodando devem voltar para a fila e reutilizar
os checkpoints. Um trabalho pausado deve continuar pausado até você clicar em
**Continuar**.

## 15. Atualizar depois de uma correção

Quando uma nova correção for publicada na mesma branch:

```bash
cd /workspace/DubForge
git pull --ff-only origin feat/adaptive-persistent-jobs
/workspace/dubforge-env/bin/python -m pip install -r requirements.txt
/workspace/dubforge-env/bin/python -m unittest discover -s tests -v
tmux kill-session -t dubforge 2>/dev/null || true
./scripts/run-tmux.sh
```

Os projetos ficam em `projects/` e não são apagados pelo `git pull`.

## 16. Diário de testes

Preencha uma linha para cada execução:

| Data/hora | GPU | Arquivo/duração | Idiomas | Modo | Resultado | Tempo | Pico VRAM | Disco antes/depois | Observação |
|---|---|---|---|---|---|---:|---:|---:|---|
| — | — | — | — | — | — | — | — | — | — |

### Problemas encontrados

Use este formato:

```text
Data/hora:
Etapa:
Mensagem exibida:
Últimas 100 linhas do dubforge.log:
GPU e VRAM:
Espaço livre:
Projeto/idioma:
Conseguiu retomar?:
```

Para coletar um diagnóstico rápido:

```bash
cd /workspace/DubForge
echo '=== GIT ==='
git log -1 --oneline
echo '=== GPU ==='
nvidia-smi
echo '=== DISCO ==='
df -h /workspace
echo '=== PROJETOS ==='
du -sh projects 2>/dev/null
echo '=== LOG ==='
tail -n 100 dubforge.log
```

Não cole senhas, chaves SSH, tokens ou arquivos de voz no relatório público.
