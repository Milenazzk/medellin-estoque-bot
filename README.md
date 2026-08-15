# Bot de Inventário para Discord

Bot em Python usando `discord.py` e comandos slash para controlar o estoque de
cada servidor Discord. Os dados são persistidos em SQLite no arquivo
`data/inventory.sqlite3`.

## Comandos

- `/estoque` — lista os itens e saldos atuais.
- `/estoque item:<nome>` — consulta um item específico.
- `/adicionar item:<nome> quantidade:<n>` — adiciona unidades e registra uma entrada.
- `/retirar item:<nome> quantidade:<n>` — retira unidades sem permitir saldo negativo.
- `/historico` — mostra as últimas movimentações.
- `/historico item:<nome> limite:<n>` — filtra o histórico por item.

Cada inventário é separado pelo ID do servidor. O histórico registra entrada ou
saída, quantidade, saldo após a operação, usuário e horário.

## Configuração

1. Crie uma aplicação no [Discord Developer Portal](https://discord.com/developers/applications),
   adicione um bot e copie o token.
2. Salve o token como um segredo chamado `DISCORD_TOKEN`.
3. Convide o bot para o servidor com os escopos `bot` e `applications.commands`.
4. Execute `python main.py`.

O token nunca é armazenado no código. O caminho do banco pode ser alterado com
`INVENTORY_DATABASE`; por padrão, o arquivo fica em `data/inventory.sqlite3`.

## Testes

```bash
python -m unittest -v test_inventory_store.py
```