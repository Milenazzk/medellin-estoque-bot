"""SQLite persistence layer for the Discord inventory bot."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Optional


LEGACY_CATEGORY = "Sem categoria"


class InventoryError(ValueError):
    """Base error for invalid inventory operations."""


class ItemNotFoundError(InventoryError):
    """Raised when an operation references an unknown item."""


class InsufficientStockError(InventoryError):
    """Raised when a withdrawal is larger than the current stock."""


class InventoryStore:
    def __init__(self, database_path: str) -> None:
        path = Path(database_path)
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.lock = RLock()
        self._create_schema()

    def _create_schema(self) -> None:
        with self.lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Sem categoria',
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, normalized_name)
                );

                CREATE TABLE IF NOT EXISTS inventory_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Sem categoria',
                    item_name TEXT NOT NULL,
                    normalized_item_name TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('add', 'remove')),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (item_id) REFERENCES inventory_items (id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_movements_guild_created
                    ON inventory_movements (guild_id, created_at DESC);
                """
            )

            # Kept as a separate additive migration so existing databases can be
            # upgraded without deleting their inventory or movement history.
            item_columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(inventory_items)")
            }
            if "category" not in item_columns:
                self.connection.execute(
                    "ALTER TABLE inventory_items ADD COLUMN category TEXT NOT NULL DEFAULT 'Sem categoria'"
                )

            columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(inventory_movements)")
            }
            if "category" not in columns:
                self.connection.execute(
                    "ALTER TABLE inventory_movements ADD COLUMN category TEXT NOT NULL DEFAULT 'Sem categoria'"
                )
                self.connection.execute(
                    """
                    UPDATE inventory_movements
                    SET category = COALESCE(
                        (SELECT category FROM inventory_items WHERE inventory_items.id = inventory_movements.item_id),
                        'Sem categoria'
                    )
                    WHERE category = 'Sem categoria'
                    """
                )
            if "normalized_item_name" not in columns:
                self.connection.execute(
                    "ALTER TABLE inventory_movements ADD COLUMN normalized_item_name TEXT NOT NULL DEFAULT ''"
                )
                self.connection.execute(
                    """
                    UPDATE inventory_movements
                    SET normalized_item_name = lower(trim(item_name))
                    WHERE normalized_item_name = ''
                    """
                )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_movements_guild_item_created
                    ON inventory_movements (guild_id, normalized_item_name, created_at DESC)
                """
            )

    @staticmethod
    def normalize_name(item_name: str) -> str:
        normalized = " ".join(item_name.strip().split()).casefold()
        if not normalized:
            raise InventoryError("O nome do item não pode ficar vazio.")
        if len(normalized) > 100:
            raise InventoryError("O nome do item deve ter no máximo 100 caracteres.")
        return normalized

    @staticmethod
    def display_name(item_name: str) -> str:
        name = " ".join(item_name.strip().split())
        if not name:
            raise InventoryError("O nome do item não pode ficar vazio.")
        if len(name) > 100:
            raise InventoryError("O nome do item deve ter no máximo 100 caracteres.")
        return name

    @staticmethod
    def display_category(category: str) -> str:
        value = " ".join(category.strip().split())
        if not value:
            raise InventoryError("A categoria não pode ficar vazia.")
        if len(value) > 100:
            raise InventoryError("A categoria deve ter no máximo 100 caracteres.")
        return value

    @staticmethod
    def validate_quantity(quantity: int) -> None:
        if quantity <= 0:
            raise InventoryError("A quantidade deve ser maior que zero.")
        if quantity > 2_147_483_647:
            raise InventoryError("A quantidade informada é grande demais.")

    def get_item(self, guild_id: int, item_name: str) -> Optional[dict[str, Any]]:
        normalized = self.normalize_name(item_name)
        with self.lock:
            row = self.connection.execute(
                """
                SELECT id, guild_id, category, name, quantity, created_at, updated_at
                FROM inventory_items
                WHERE guild_id = ? AND normalized_name = ?
                """,
                (guild_id, normalized),
            ).fetchone()
        return dict(row) if row else None

    def list_items(self, guild_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT id, guild_id, category, name, quantity, created_at, updated_at
                FROM inventory_items
                WHERE guild_id = ?
                ORDER BY lower(category) ASC, lower(name) ASC
                """,
                (guild_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_stock(
        self,
        guild_id: int,
        item_name: str,
        quantity: int,
        user_id: int,
        user_name: str,
        category: Optional[str] = None,
    ) -> dict[str, Any]:
        self.validate_quantity(quantity)
        name = self.display_name(item_name)
        normalized = self.normalize_name(name)
        requested_category = (
            self.display_category(category) if category is not None else None
        )
        timestamp = self._timestamp()

        with self.lock, self.connection:
            row = self.connection.execute(
                """
                SELECT id, category, name, quantity
                FROM inventory_items
                WHERE guild_id = ? AND normalized_name = ?
                """,
                (guild_id, normalized),
            ).fetchone()

            if row:
                new_quantity = row["quantity"] + quantity
                stored_category = requested_category or row["category"]
                self.connection.execute(
                    """
                    UPDATE inventory_items
                    SET category = ?, quantity = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (stored_category, new_quantity, timestamp, row["id"]),
                )
                item_id = row["id"]
                stored_name = row["name"]
            else:
                new_quantity = quantity
                stored_category = requested_category or LEGACY_CATEGORY
                cursor = self.connection.execute(
                    """
                    INSERT INTO inventory_items
                        (guild_id, category, name, normalized_name, quantity, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        stored_category,
                        name,
                        normalized,
                        quantity,
                        timestamp,
                        timestamp,
                    ),
                )
                item_id = cursor.lastrowid
                stored_name = name

            self._record_movement(
                guild_id=guild_id,
                item_id=item_id,
                category=stored_category,
                item_name=stored_name,
                normalized_item_name=normalized,
                action="add",
                quantity=quantity,
                balance_after=new_quantity,
                user_id=user_id,
                user_name=user_name,
                created_at=timestamp,
            )

        return {
            "id": item_id,
            "category": stored_category,
            "name": stored_name,
            "quantity": new_quantity,
        }

    def remove_stock(
        self,
        guild_id: int,
        item_name: str,
        quantity: int,
        user_id: int,
        user_name: str,
    ) -> dict[str, Any]:
        self.validate_quantity(quantity)
        normalized = self.normalize_name(item_name)
        timestamp = self._timestamp()

        with self.lock, self.connection:
            row = self.connection.execute(
                """
                SELECT id, category, name, quantity
                FROM inventory_items
                WHERE guild_id = ? AND normalized_name = ?
                """,
                (guild_id, normalized),
            ).fetchone()

            if row is None:
                raise ItemNotFoundError(f"O item **{item_name.strip()}** não está cadastrado.")
            if row["quantity"] < quantity:
                raise InsufficientStockError(
                    f"Saldo de **{row['name']}**: {row['quantity']} unidade(s). "
                    f"Você tentou retirar {quantity}."
                )

            new_quantity = row["quantity"] - quantity
            self.connection.execute(
                """
                UPDATE inventory_items
                SET quantity = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_quantity, timestamp, row["id"]),
            )
            self._record_movement(
                guild_id=guild_id,
                item_id=row["id"],
                category=row["category"],
                item_name=row["name"],
                normalized_item_name=normalized,
                action="remove",
                quantity=quantity,
                balance_after=new_quantity,
                user_id=user_id,
                user_name=user_name,
                created_at=timestamp,
            )

        return {
            "id": row["id"],
            "category": row["category"],
            "name": row["name"],
            "quantity": new_quantity,
        }

    def list_history(
        self,
        guild_id: int,
        item_name: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 20:
            raise InventoryError("O limite deve ficar entre 1 e 20.")

        with self.lock:
            if item_name:
                normalized = self.normalize_name(item_name)
                rows = self.connection.execute(
                    """
                    SELECT category, item_name, action, quantity, balance_after, user_name, created_at
                    FROM inventory_movements
                    WHERE guild_id = ? AND normalized_item_name = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (guild_id, normalized, limit),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """
                    SELECT category, item_name, action, quantity, balance_after, user_name, created_at
                    FROM inventory_movements
                    WHERE guild_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (guild_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def _record_movement(
        self,
        guild_id: int,
        item_id: int,
        category: str,
        item_name: str,
        normalized_item_name: str,
        action: str,
        quantity: int,
        balance_after: int,
        user_id: int,
        user_name: str,
        created_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO inventory_movements
                (guild_id, item_id, category, item_name, normalized_item_name, action,
                 quantity, balance_after, user_id, user_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                item_id,
                category,
                item_name,
                normalized_item_name,
                action,
                quantity,
                balance_after,
                user_id,
                user_name,
                created_at,
            ),
        )

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def close(self) -> None:
        with self.lock:
            self.connection.close()