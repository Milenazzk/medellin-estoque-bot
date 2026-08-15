"""Discord inventory bot with slash commands and SQLite persistence."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from inventory_store import (
    InventoryError,
    InventoryStore,
    InsufficientStockError,
    ItemNotFoundError,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("inventory-bot")

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_PATH = os.getenv("INVENTORY_DATABASE", "data/inventory.sqlite3")


def format_quantity(quantity: int) -> str:
    return f"{quantity:,}".replace(",", ".")


def format_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        return discord.utils.format_dt(parsed, style="f")
    except ValueError:
        return value


class InventoryBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.store = InventoryStore(DATABASE_PATH)

    async def setup_hook(self) -> None:
        await self.tree.sync()
        logger.info("Slash commands synced globally.")

    async def close(self) -> None:
        self.store.close()
        await super().close()


bot = InventoryBot()


@bot.event
async def on_ready() -> None:
    if bot.user is not None:
        logger.info("Connected as %s (%s)", bot.user, bot.user.id)


def require_guild(interaction: discord.Interaction) -> Optional[discord.Embed]:
    if interaction.guild_id is None:
        return discord.Embed(
            title="Comando indisponível",
            description="Os comandos de inventário só podem ser usados dentro de um servidor.",
            color=discord.Color.red(),
        )
    return None


@bot.tree.command(name="estoque", description="Consulta o inventário deste servidor.")
@app_commands.describe(item="Nome do item específico (opcional)")
async def estoque(
    interaction: discord.Interaction,
    item: Optional[str] = None,
) -> None:
    guild_error = require_guild(interaction)
    if guild_error is not None:
        await interaction.response.send_message(embed=guild_error, ephemeral=True)
        return

    assert interaction.guild_id is not None
    if item:
        record = bot.store.get_item(interaction.guild_id, item)
        if record is None:
            embed = discord.Embed(
                title="Item não encontrado",
                description=f"Nenhum item chamado **{item.strip()}** está cadastrado neste servidor.",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title="Consulta de estoque", color=discord.Color.blurple())
        embed.add_field(name=record["name"], value=f"**{format_quantity(record['quantity'])}** unidade(s)")
        embed.set_footer(text="Inventário separado por servidor")
        await interaction.response.send_message(embed=embed)
        return

    records = bot.store.list_items(interaction.guild_id)
    embed = discord.Embed(title="Estoque atual", color=discord.Color.blurple())
    embed.set_footer(text="Inventário separado por servidor")

    if not records:
        embed.description = "O estoque está vazio. Use `/adicionar` para cadastrar o primeiro item."
    else:
        embed.description = "\n".join(
            f"**{record['name']}** — {format_quantity(record['quantity'])} unidade(s)"
            for record in records
        )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="adicionar", description="Adiciona unidades ao estoque.")
@app_commands.describe(
    item="Nome do item",
    quantidade="Quantidade a adicionar (maior que zero)",
)
async def adicionar(interaction: discord.Interaction, item: str, quantidade: int) -> None:
    guild_error = require_guild(interaction)
    if guild_error is not None:
        await interaction.response.send_message(embed=guild_error, ephemeral=True)
        return

    assert interaction.guild_id is not None
    try:
        record = bot.store.add_stock(
            guild_id=interaction.guild_id,
            item_name=item,
            quantity=quantidade,
            user_id=interaction.user.id,
            user_name=str(interaction.user),
        )
    except InventoryError as error:
        embed = discord.Embed(title="Não foi possível adicionar", description=str(error), color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(title="Estoque atualizado", color=discord.Color.green())
    embed.description = (
        f"Foram adicionadas **{format_quantity(quantidade)}** unidade(s) de **{record['name']}**.\n"
        f"Saldo atual: **{format_quantity(record['quantity'])}** unidade(s)."
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="retirar", description="Retira unidades do estoque.")
@app_commands.describe(
    item="Nome do item",
    quantidade="Quantidade a retirar (maior que zero)",
)
async def retirar(interaction: discord.Interaction, item: str, quantidade: int) -> None:
    guild_error = require_guild(interaction)
    if guild_error is not None:
        await interaction.response.send_message(embed=guild_error, ephemeral=True)
        return

    assert interaction.guild_id is not None
    try:
        record = bot.store.remove_stock(
            guild_id=interaction.guild_id,
            item_name=item,
            quantity=quantidade,
            user_id=interaction.user.id,
            user_name=str(interaction.user),
        )
    except InsufficientStockError as error:
        embed = discord.Embed(title="Estoque insuficiente", description=str(error), color=discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    except (ItemNotFoundError, InventoryError) as error:
        embed = discord.Embed(title="Não foi possível retirar", description=str(error), color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(title="Estoque atualizado", color=discord.Color.green())
    embed.description = (
        f"Foram retiradas **{format_quantity(quantidade)}** unidade(s) de **{record['name']}**.\n"
        f"Saldo atual: **{format_quantity(record['quantity'])}** unidade(s)."
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="historico", description="Consulta as últimas movimentações do estoque.")
@app_commands.describe(
    item="Filtra pelo nome do item (opcional)",
    limite="Quantidade de registros, de 1 a 20 (padrão: 10)",
)
async def historico(
    interaction: discord.Interaction,
    item: Optional[str] = None,
    limite: app_commands.Range[int, 1, 20] = 10,
) -> None:
    guild_error = require_guild(interaction)
    if guild_error is not None:
        await interaction.response.send_message(embed=guild_error, ephemeral=True)
        return

    assert interaction.guild_id is not None
    records = bot.store.list_history(interaction.guild_id, item_name=item, limit=limite)
    embed = discord.Embed(title="Histórico de movimentações", color=discord.Color.blurple())

    if not records:
        embed.description = "Nenhuma movimentação foi registrada ainda."
    else:
        lines = []
        for record in records:
            action = "Entrada" if record["action"] == "add" else "Saída"
            sign = "+" if record["action"] == "add" else "-"
            lines.append(
                f"**{action}** {sign}{format_quantity(record['quantity'])} × **{record['item_name']}**\n"
                f"Saldo: {format_quantity(record['balance_after'])} · "
                f"{record['user_name']} · {format_timestamp(record['created_at'])}"
            )
        embed.description = "\n\n".join(lines)

    await interaction.response.send_message(embed=embed)


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN não está configurado. Adicione o token do bot como um segredo "
        "com o nome DISCORD_TOKEN antes de iniciar."
    )


bot.run(TOKEN)