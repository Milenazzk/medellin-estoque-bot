import tempfile
import unittest
from pathlib import Path

from inventory_store import (
    InventoryError,
    InventoryStore,
    InsufficientStockError,
    ItemNotFoundError,
)


class InventoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "inventory.sqlite3"
        self.store = InventoryStore(str(self.database_path))

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_add_and_remove_stock_are_persisted(self) -> None:
        self.store.add_stock(123, "Café", 10, 1, "Ana")
        self.store.add_stock(123, " café ", 5, 2, "Bruno")
        self.store.remove_stock(123, "CAFÉ", 4, 3, "Carla")

        self.store.close()
        reopened = InventoryStore(str(self.database_path))
        try:
            self.assertEqual(reopened.get_item(123, "café")["quantity"], 11)
            history = reopened.list_history(123)
            self.assertEqual(len(history), 3)
            self.assertEqual(history[0]["action"], "remove")
        finally:
            reopened.close()

    def test_cannot_remove_more_than_available(self) -> None:
        self.store.add_stock(123, "Teclado", 2, 1, "Ana")

        with self.assertRaises(InsufficientStockError):
            self.store.remove_stock(123, "Teclado", 3, 1, "Ana")

        self.assertEqual(self.store.get_item(123, "Teclado")["quantity"], 2)
        self.assertEqual(len(self.store.list_history(123)), 1)

    def test_unknown_item_and_invalid_quantity_fail(self) -> None:
        with self.assertRaises(ItemNotFoundError):
            self.store.remove_stock(123, "Mouse", 1, 1, "Ana")
        with self.assertRaises(InventoryError):
            self.store.add_stock(123, "Mouse", 0, 1, "Ana")

    def test_guilds_have_separate_inventory(self) -> None:
        self.store.add_stock(1, "Caderno", 3, 1, "Ana")
        self.store.add_stock(2, "Caderno", 8, 1, "Ana")

        self.assertEqual(self.store.get_item(1, "Caderno")["quantity"], 3)
        self.assertEqual(self.store.get_item(2, "Caderno")["quantity"], 8)

    def test_custom_categories_are_persisted_and_shown_in_history(self) -> None:
        self.store.add_stock(123, "MTAR", 20, 1, "Ana", category="Armas")
        self.store.remove_stock(123, "MTAR", 5, 2, "Bruno")

        item = self.store.get_item(123, "MTAR")
        self.assertEqual(item["category"], "Armas")
        self.assertEqual(item["quantity"], 15)

        history = self.store.list_history(123)
        self.assertEqual(history[0]["category"], "Armas")
        self.assertEqual(history[1]["category"], "Armas")


if __name__ == "__main__":
    unittest.main()