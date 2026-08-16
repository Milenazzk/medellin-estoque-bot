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
MEDELLIN_RED = discord.Color(0x7A1F1F)


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
        self.commands_synced = False

    async def setup_hook(self) -> None:
        # The view has custom IDs and no timeout, so Discord can route button
        # clicks to it even after this process has restarted.
        self.add_view(InventoryPanelView(self))
        logger.info("Persistent inventory panel view registered.")

    async def sync_slash_commands(self) -> None:
        """Clear global commands and sync one copy to each guild."""
        command_definitions = self.tree.get_commands()
        self.tree.clear_commands(guild=None)
        logger.info("Clearing global slash commands.")
        cleared_global_commands = await self.tree.sync()

        # Keep the local definitions available for guild-specific syncs and
        # for future guild joins, without publishing another global copy.
        for command in command_definitions:
            self.tree.add_command(command)

        guild_command_total = 0

        for guild in self.guilds:
            logger.info("Syncing slash commands to guild %s.", guild.id)
            self.tree.copy_global_to(guild=guild)
            guild_commands = await self.tree.sync(guild=guild)
            guild_command_total += len(guild_commands)

        logger.info(
            "Slash commands synced: global registry cleared (%d remaining); "
            "%d across %d guild(s).",
            len(cleared_global_commands),
            guild_command_total,
            len(self.guilds),
        )

    async def close(self) -> None:
        self.store.close()
        await super().close()


bot = InventoryBot()


def build_inventory_embed(guild_id: int) -> discord.Embed:
    records = bot.store.list_items(guild_id)
    embed = discord.Embed(title="📦 ESTOQUE • MEDELLÍN", color=MEDELLIN_RED)
    embed.set_footer(text="Use os botões abaixo para movimentar o estoque")

    if not records:
        embed.description = "📦 O estoque está vazio."
        return embed

    lines = [
        f"**{record['name']}** — **{format_quantity(record['quantity'])}**"
        for record in records
    ]
    description = "\n".join(lines)
    if len(description) > 3900:
        description = description[:3890].rsplit("\n", 1)[0] + "\n…"
    embed.description = description
    return embed


def build_history_embed(guild_id: int, limit: int = 10) -> discord.Embed:
    records = bot.store.list_history(guild_id, limit=limit)
    embed = discord.Embed(title="📋 HISTÓRICO • MEDELLÍN", color=MEDELLIN_RED)
    embed.set_footer(text="Últimas movimentações do servidor")

    if not records:
        embed.description = "Nenhuma movimentação foi registrada ainda."
        return embed

    lines = []
    for record in records:
        action = "Entrada" if record["action"] == "add" else "Saída"
        sign = "+" if record["action"] == "add" else "-"
        lines.append(
            f"**{action}** · **{record['item_name']}** · "
            f"{sign}{format_quantity(record['quantity'])} unidade(s)\n"
            f"Usuário: {record['user_name']} · "
            f"{format_timestamp(record['created_at'])}"
        )
    embed.description = "\n\n".join(lines)
    return embed


def parse_modal_quantity(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError as error:
        raise InventoryError("A quantidade deve ser um número inteiro.") from error


class AddStockModal(discord.ui.Modal, title="➕ Adicionar ao estoque"):
    item_name = discord.ui.TextInput(
        label="Nome do item",
        placeholder="Ex.: Café Medellín",
        min_length=1,
        max_length=100,
        required=True,
    )
    quantity = discord.ui.TextInput(
        label="Quantidade",
        placeholder="Ex.: 10",
        min_length=1,
        max_length=10,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return

        try:
            quantity = parse_modal_quantity(str(self.quantity.value))
            record = bot.store.add_stock(
                guild_id=interaction.guild_id,
                item_name=str(self.item_name.value),
                quantity=quantity,
                user_id=interaction.user.id,
                user_name=str(interaction.user),
            )
        except InventoryError as error:
            embed = discord.Embed(
                title="Não foi possível adicionar",
                description=str(error),
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title="Estoque atualizado", color=discord.Color.green())
        embed.description = (
            f"Foram adicionadas **{format_quantity(quantity)}** unidade(s) de "
            f"**{record['name']}**.\nSaldo atual: **{format_quantity(record['quantity'])}** unidade(s)."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RemoveStockModal(discord.ui.Modal, title="➖ Retirar do estoque"):
    item_name = discord.ui.TextInput(
        label="Nome do item",
        placeholder="Ex.: Café Medellín",
        min_length=1,
        max_length=100,
        required=True,
    )
    quantity = discord.ui.TextInput(
        label="Quantidade",
        placeholder="Ex.: 3",
        min_length=1,
        max_length=10,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return

        try:
            quantity = parse_modal_quantity(str(self.quantity.value))
            record = bot.store.remove_stock(
                guild_id=interaction.guild_id,
                item_name=str(self.item_name.value),
                quantity=quantity,
                user_id=interaction.user.id,
                user_name=str(interaction.user),
            )
        except (InsufficientStockError, ItemNotFoundError, InventoryError) as error:
            embed = discord.Embed(
                title="Não foi possível retirar",
                description=str(error),
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title="Estoque atualizado", color=discord.Color.green())
        embed.description = (
            f"Foram retiradas **{format_quantity(quantity)}** unidade(s) de "
            f"**{record['name']}**.\nSaldo atual: **{format_quantity(record['quantity'])}** unidade(s)."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class InventoryPanelView(discord.ui.View):
    def __init__(self, inventory_bot: InventoryBot) -> None:
        super().__init__(timeout=None)
        self.inventory_bot = inventory_bot

    @discord.ui.button(
        label="Adicionar",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="inventory_panel:add",
    )
    async def add_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        await interaction.response.send_modal(AddStockModal())

    @discord.ui.button(
        label="Retirar",
        emoji="➖",
        style=discord.ButtonStyle.danger,
        custom_id="inventory_panel:remove",
    )
    async def remove_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        await interaction.response.send_modal(RemoveStockModal())

    @discord.ui.button(
        label="Histórico",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="inventory_panel:history",
    )
    async def history_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_history_embed(interaction.guild_id),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Atualizar",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="inventory_panel:refresh",
    )
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[discord.ui.View],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Este painel só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=build_inventory_embed(interaction.guild_id),
            view=InventoryPanelView(self.inventory_bot),
        )


@bot.event
async def on_ready() -> None:
    if bot.user is not None:
        logger.info("Connected as %s (%s)", bot.user, bot.user.id)
    if not bot.commands_synced:
        try:
            await bot.sync_slash_commands()
            bot.commands_synced = True
        except discord.DiscordException:
            logger.exception("Could not sync slash commands during on_ready.")


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    """Sync commands immediately when the bot is added to a new server."""
    try:
        bot.tree.copy_global_to(guild=guild)
        guild_commands = await bot.tree.sync(guild=guild)
        logger.info(
            "Slash commands synced to new guild %s: %d command(s).",
            guild.id,
            len(guild_commands),
        )
    except discord.DiscordException:
        logger.exception("Could not sync slash commands to guild %s.", guild.id)


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

    embed = build_inventory_embed(interaction.guild_id)
    await interaction.response.send_message(embed=embed, view=InventoryPanelView(bot))


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
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    await interaction.response.send_message(embed=embed, ephemeral=True)


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

    await interaction.response.send_message(embed=embed, ephemeral=True)


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN não está configurado. Adicione o token do bot como um segredo "
        "com o nome DISCORD_TOKEN antes de iniciar."
    )


bot.run(TOKEN)