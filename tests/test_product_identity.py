import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from product_identity import rename_inventory, IdentityConflict


class ProductIdentity(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        with self.engine.begin() as conn:
            for table in ('productos', 'mape_productos'):
                conn.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, nombre TEXT, color TEXT)'))
            conn.execute(text('CREATE TABLE stock (producto TEXT, color TEXT, cajas INTEGER, piezas_por_caja INTEGER, palet_id INTEGER)'))
            conn.execute(text('CREATE TABLE movimientos (producto TEXT, color TEXT, cajas INTEGER, fecha TEXT)'))
            conn.execute(text("INSERT INTO productos VALUES (136,'MESA PLEGABLE','120X60'),(48,'MESA PLEGABLE CAMA','Negro')"))
            conn.execute(text("INSERT INTO stock VALUES ('MESA PLEGABLE','120X60',40,1,10),('MESA PLEGABLE CAMA','Negro',2,10,6)"))
            conn.execute(text("INSERT INTO movimientos VALUES ('MESA PLEGABLE','120X60',40,'2026-10-01')"))

    def tearDown(self):
        self.engine.dispose()

    def test_size_correction_keeps_quantities_locations_history(self):
        with self.engine.begin() as conn:
            rename_inventory(conn, 136, 'MESA PLEGABLE', '120X60', 'MESA PLEGABLE', '90X60')
            row = conn.execute(text("SELECT * FROM stock WHERE producto='MESA PLEGABLE'")).first()
            self.assertEqual((row.color,row.cajas,row.piezas_por_caja,row.palet_id),('90X60',40,1,10))
            history = conn.execute(text('SELECT * FROM movimientos')).first()
            self.assertEqual((history.color,history.cajas,history.fecha),('90X60',40,'2026-10-01'))
            self.assertEqual(conn.execute(text("SELECT cajas FROM stock WHERE producto='MESA PLEGABLE CAMA'")).scalar(),2)

    def test_sku_only_does_not_touch_inventory(self):
        with self.engine.begin() as conn:
            before=conn.execute(text('SELECT * FROM stock')).fetchall()
            rename_inventory(conn,48,'MESA PLEGABLE CAMA','Negro','MESA PLEGABLE CAMA','Negro')
            self.assertEqual(before,conn.execute(text('SELECT * FROM stock')).fetchall())

    def test_reject_destination_stock_and_ambiguous_catalog(self):
        for table in ('stock','productos','mape_productos'):
            with self.subTest(table=table), self.engine.begin() as conn:
                if table=='stock':
                    conn.execute(text("INSERT INTO stock VALUES ('MESA PLEGABLE','90X60',3,1,20)"))
                else:
                    conn.execute(text(f"INSERT INTO {table} VALUES (999,'MESA PLEGABLE','90X60')"))
                with self.assertRaises(IdentityConflict):
                    rename_inventory(conn,136,'MESA PLEGABLE','120X60','MESA PLEGABLE','90X60')
                self.assertEqual(conn.execute(text("SELECT cajas FROM stock WHERE color='120X60'")).scalar(),40)
                conn.execute(text(f"DELETE FROM {table} WHERE color='90X60'"))


if __name__ == '__main__':
    unittest.main()
