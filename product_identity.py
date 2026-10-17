"""Mantiene las referencias de inventario al corregir un nombre o variante."""
from sqlalchemy import text


class IdentityConflict(ValueError):
    pass


def rename_inventory(conn, pid, old_name, old_color, name, color):
    if (old_name, old_color) == (name, color):
        return
    params = {'id': pid, 'old_name': old_name, 'old_color': old_color,
              'name': name, 'color': color}
    source = "LOWER(TRIM(nombre))=LOWER(:old_name) AND LOWER(TRIM(color))=LOWER(:old_color)"
    destination = "LOWER(TRIM(nombre))=LOWER(:name) AND LOWER(TRIM(color))=LOWER(:color)"
    # El inventario está asociado por nombre y color, no por ID/SKU. Evitar
    # atribuir a un solo SKU el stock compartido por dos entradas ambiguas.
    for table in ('productos', 'mape_productos'):
        exclusion = 'AND id<>:id' if table == 'productos' else ''
        if conn.execute(text(f'SELECT 1 FROM {table} WHERE ({source} OR {destination}) {exclusion} LIMIT 1'), params).first():
            raise IdentityConflict('Otro producto usa ese nombre y variante. Revisá la coincidencia antes de modificarlo.')
    # Si el destino ya tiene stock, no fusionar ubicaciones de forma implícita.
    same = old_name.strip().lower() == name.lower() and old_color.strip().lower() == color.lower()
    if not same and conn.execute(text('''SELECT 1 FROM stock
            WHERE LOWER(TRIM(producto))=LOWER(:name) AND LOWER(TRIM(color))=LOWER(:color) LIMIT 1'''), params).first():
        raise IdentityConflict('Ya hay stock con ese nombre y variante. Revisalo antes de modificarlo.')
    for table in ('stock', 'movimientos'):
        conn.execute(text(f'''UPDATE {table} SET producto=:name, color=:color
            WHERE LOWER(TRIM(producto))=LOWER(:old_name)
              AND LOWER(TRIM(COALESCE(color, '')))=LOWER(:old_color)'''), params)
