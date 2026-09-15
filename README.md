# Saverino Manager

Saverino Manager é um gerenciador local de saves para Windows. Ele organiza jogos, perfis e cópias de segurança de arquivos ou pastas, mantendo os backups separados do save original.

Este repositório distribui atualmente apenas os pacotes prontos nas Releases; o código-fonte será publicado separadamente. A versão atual é **0.1.0**.

[![Latest release](https://img.shields.io/github/v/release/mmachida/Saverino-Manager?label=vers%C3%A3o)](https://github.com/mmachida/Saverino-Manager/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-2f5f9e)](https://github.com/mmachida/Saverino-Manager/releases)

## Imagens

Adicione suas duas imagens nos caminhos abaixo para preencher os exemplos no GitHub:

![Janela principal](screenshots/main-window.png)

![Temas e configurações](screenshots/themes-settings.png)

## Download e instalação

Baixe a versão mais recente na página de [Releases](https://github.com/mmachida/Saverino-Manager/releases).

Para a versão atual, o pacote Windows x64 contém:

- `save-manager-windows-x64.zip`: pacote do aplicativo.
- `save-manager-windows-x64.sha256`: checksum para verificar o download.

Extraia o ZIP inteiro para uma pasta gravável e execute `SaveManager.exe`. Mantenha `SaveManagerUpdater.exe` na mesma pasta, pois ele é usado pelo sistema de atualização. O aplicativo distribuído não exige Python ou outra instalação adicional.

Opcionalmente, valide o ZIP no PowerShell:

```powershell
(Get-FileHash .\save-manager-windows-x64.zip -Algorithm SHA256).Hash
Get-Content .\save-manager-windows-x64.sha256
```

O valor hexadecimal exibido pelo primeiro comando deve ser igual ao valor do arquivo `.sha256`.

## Primeira configuração

1. Abra **File > Add Game**.
2. Informe o nome do jogo, com no máximo 50 caracteres e apenas caracteres válidos para nomes de pasta do Windows.
3. Escolha se a origem é um **File** ou uma **Folder** e selecione o save original.
4. Escolha o destino dos backups: **Same folder as save** ou **Custom**.
5. Crie um perfil em **New profile**.
6. Use **Create save** para criar a primeira cópia.

A origem e o destino são configurados uma vez por jogo e valem para todos os perfis daquele jogo. Os backups ficam organizados assim:

```text
Saverino - NomeDoJogo\NomeDoPerfil\...
```

Cada perfil tem sua própria pasta. O arquivo original permanece no local escolhido pelo usuário.

## Gerenciamento de saves

A lista mostra `#`, `Name`, `Description` e `Created`. Nome e descrição podem ser editados com duplo clique; o nome segue as regras de nomes de arquivo do Windows. Saves criados automaticamente recebem nomes como `save_01`, `save_02` e assim por diante.

O aplicativo oferece:

- pesquisa e ordenação da lista;
- seleção múltipla usando Ctrl, Shift, Alt ou arrastando o mouse;
- menu de contexto para jogos e saves;
- **Create save**, **Overwrite save**, **Load save** e **Delete**;
- abertura da pasta da origem e da pasta de backups;
- seleção automática do novo save criado e rolagem até o final da lista.

**Load save** substitui o conteúdo atual da origem somente após a confirmação. Ele não cria um backup automático adicional; o usuário escolhe conscientemente qual estado deseja carregar. **Overwrite save** substitui o backup selecionado pelo estado atual da origem.

Arquivos e pastas adicionados manualmente ao diretório de um perfil são sincronizados com a lista. Quando a origem é um arquivo, apenas arquivos com a mesma extensão da origem são considerados. Alterações feitas diretamente no Explorer, incluindo renomeações, são acompanhadas pelo aplicativo.

Excluir um save pede confirmação e remove o item da lista ativa. A exclusão de um perfil remove sua pasta de backups e todos os saves gerenciados nela. A exclusão de um jogo remove seus perfis e backups gerenciados, mas mantém o arquivo ou a pasta original do jogo.

## Segurança e sincronização

As operações de cópia e restauração são executadas em segundo plano, com verificação do conteúdo e recuperação de operações interrompidas. O aplicativo não altera a origem ao criar um backup. Durante uma restauração, a origem é substituída pelo save escolhido e o resultado é verificado.

O catálogo acompanha a existência real das pastas: se a pasta de um perfil for removida manualmente, o perfil deixa de aparecer após a sincronização. Se um backup estiver ausente, ele fica indisponível até reaparecer no local esperado.

## Settings

Em **Options > Settings** estão disponíveis:

- idioma English (United States) ou Português (Brasil);
- inicialização com o Windows, desativada por padrão;
- janela sempre no topo;
- verificação automática de atualizações ao iniciar;
- hotkeys globais para criar e carregar saves;
- opção **Load save without confirmation** para a hotkey de carregamento;
- botões para limpar cada hotkey individualmente;
- efeitos sonoros independentes para importar e carregar saves;
- volume dos efeitos entre 0% e 100%, com padrão de 50%.

O atalho geral `Delete` funciona quando a janela está selecionada e aciona a exclusão do save selecionado. Hotkeys globais funcionam mesmo quando o aplicativo está em segundo plano e possuem um intervalo para evitar execuções duplicadas.

## Temas

Em **Options > Themes**, os cards estão nesta ordem:

1. Classic
2. Night Mode
3. Hollow Knight
4. Silk Song
5. Elden Ring
6. Batman: Arkham Knight

O **Night Mode** é o tema usado na primeira inicialização. A escolha é aplicada imediatamente e salva para as próximas aberturas. Cada tema mantém o mesmo comportamento de seleção, hover, botões, estados desabilitados, barras de rolagem e diálogos, alterando apenas a paleta de cores.

## Importar e exportar configurações

Em **File > Import/Export**, o aplicativo pode exportar ou importar:

- configurações dos jogos;
- perfis;
- preferências do aplicativo;
- idioma, tema, áudio e hotkeys.

Os arquivos e pastas dos saves **não são incluídos** no arquivo exportado. Depois de uma instalação limpa, mova manualmente os backups para as pastas registradas nas configurações do jogo antes de importar ou reconectar os dados.

## Onde os dados ficam salvos

As configurações do aplicativo ficam em:

```text
%APPDATA%\Saverino Manager\
```

O catálogo, journals de recuperação, metadados, lixeira interna e logs ficam dentro dessa pasta. Os backups continuam nos destinos escolhidos pelo usuário e não são copiados para `%APPDATA%`.

## Atualizações

O aplicativo consulta as releases públicas de [mmachida/Saverino-Manager](https://github.com/mmachida/Saverino-Manager). A verificação pode acontecer automaticamente na inicialização ou manualmente em **About > Check for Updates**.

Quando uma atualização compatível é encontrada, o aplicativo baixa o pacote, valida o checksum e pede confirmação antes de fechar para instalar. O atualizador substitui somente os arquivos do programa; jogos, perfis, configurações e backups ficam preservados.

Cada release publicada deve conter o ZIP e seu arquivo `.sha256` correspondentes à arquitetura distribuída. Para o Windows x64, os nomes esperados são `save-manager-windows-x64.zip` e `save-manager-windows-x64.sha256`.

## Limitações conhecidas

- O suporte atual é para volumes locais do Windows.
- Caminhos de rede UNC, links simbólicos e junctions não fazem parte do escopo atual.
- Os arquivos de backup nunca são incluídos no pacote de atualização ou no arquivo de exportação.
- O aplicativo deve permanecer em uma pasta com permissão de leitura e escrita para permitir atualizações e logs.

## Suporte

Relate problemas e sugestões na página de [Issues do GitHub](https://github.com/mmachida/Saverino-Manager/issues). Para apoiar o projeto, acesse [Ko-fi](https://ko-fi.com/mmachida).
