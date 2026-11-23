# Segurança

## Dados locais

O DubForge processa mídia, transcrições, referências de voz e resultados localmente.
Esses artefatos ficam em `projects/`, que é excluída do Git. Arquivos `.env`, bancos
locais, chaves privadas, logs, modelos e formatos comuns de áudio e vídeo também
são ignorados.

Nunca publique:

- chaves de API, tokens ou senhas;
- endereços e credenciais SSH;
- arquivos de projeto, vozes ou transcrições sem autorização;
- bancos gerados em `projects/jobs/`.

## Exposição da interface

Por padrão o servidor escuta apenas em `127.0.0.1`. Use túnel SSH para acesso
remoto. Não altere para `0.0.0.0` sem adicionar autenticação e uma camada HTTPS.

## Relato de vulnerabilidade

Não abra uma issue pública contendo credenciais, vozes, mídia privada ou detalhes
que permitam exploração imediata. Entre em contato privadamente com o mantenedor
do repositório.

## Clonagem responsável de voz

Clone uma voz somente com autorização do titular e respeite a legislação e as
licenças dos modelos utilizados.
