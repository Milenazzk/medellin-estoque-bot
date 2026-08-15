# Bot de Inventário para Discord

Bot em Python com `discord.py` que controla inventário por servidor usando comandos slash e mantém os dados em SQLite.

## Executar

- `python main.py` — inicia o bot
- `python -m unittest -v test_inventory_store.py` — executa os testes da persistência
- Segredo necessário: `DISCORD_TOKEN`

## Stack

- Python 3.11
- Discord API via `discord.py`
- SQLite com transações, foreign keys e WAL

## Onde ficam as coisas

- `main.py` — cliente Discord e comandos slash
- `inventory_store.py` — schema SQLite e operações transacionais
- `test_inventory_store.py` — testes da camada de persistência
- `data/inventory.sqlite3` — banco criado automaticamente em runtime

## Decisões

- O estoque é separado por `guild_id`, evitando que servidores diferentes compartilhem dados.
- Item é comparado por nome normalizado, mas mantém a primeira grafia usada para exibição.
- Retiradas são transacionais e nunca podem levar o saldo abaixo de zero.
- O token é lido exclusivamente do segredo `DISCORD_TOKEN`.

## Produto

Os comandos `/estoque`, `/adicionar`, `/retirar` e `/historico` permitem consultar,
movimentar e auditar o inventário diretamente no Discord.

## Preferências do usuário

- Não criar website.

## Cuidados

- O segredo `DISCORD_TOKEN` precisa existir antes de iniciar o bot.
- O convite do bot precisa incluir os escopos `bot` e `applications.commands`.
